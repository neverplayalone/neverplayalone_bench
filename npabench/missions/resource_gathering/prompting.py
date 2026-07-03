from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from npabench.missions.base import PromptMetadata, Task

PROVIDER_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "chutes": "https://llm.chutes.ai/v1",
}
PROMPT_SCHEMA_VERSION = "resource_gathering.v3"

PROMPT_EXAMPLES = """Examples:
Task:
- 24 logs (essential)
- 20 cobblestone (essential)
- 9 raw meat (essential)
- 16 sand (optional)
- 3 apple (optional)
Prompt:
Collect 24 logs, 20 cobblestone, 9 raw meat, 16 sand, and 3 apples. Keep the items in your inventory and finish within 20 blocks of spawn.

Task:
- 30 logs (essential)
- 22 cobblestone (essential)
- 12 raw meat (essential)
- 16 kelp (optional)
- 6 flowers (optional)
Prompt:
Bring back 30 logs, 22 cobblestone, 12 raw meat, 16 kelp, and 6 flowers. Keep everything in your inventory and return to within 20 blocks of spawn when you finish.

Task:
- 21 logs (essential)
- 28 cobblestone (essential)
- 7 raw meat (essential)
- 16 dirt (optional)
- 5 pumpkins (optional)
Prompt:
Retrieve 21 logs, 28 cobblestone, 7 raw meat, 16 dirt, and 5 pumpkins. Keep the gathered items in your inventory and end the run within 20 blocks of spawn."""


def materialize_task_prompt(task: Task, output_dir: Path) -> Task:
    cached_task = _load_cached_task(output_dir / "task.json")
    if cached_task is not None and _can_reuse_cached_prompt(task, cached_task):
        return task.model_copy(
            update={
                "prompt": cached_task.prompt,
                "prompt_metadata": cached_task.prompt_metadata,
            }
        )

    prompt, metadata = _generate_prompt(task)
    return task.model_copy(update={"prompt": prompt, "prompt_metadata": metadata})


def _load_cached_task(path: Path) -> Task | None:
    if not path.exists():
        return None
    try:
        return Task.model_validate_json(path.read_text())
    except Exception:
        return None


def _can_reuse_cached_prompt(task: Task, cached_task: Task) -> bool:
    metadata = cached_task.prompt_metadata
    return (
        cached_task.task_id == task.task_id
        and cached_task.targets == task.targets
        and metadata is not None
        and metadata.schema_version == PROMPT_SCHEMA_VERSION
        and bool(cached_task.prompt.strip())
    )


def _generate_prompt(task: Task) -> tuple[str, PromptMetadata]:
    provider = os.environ.get("NPABENCH_PROMPT_PROVIDER", "openrouter").strip().lower()
    key_env = "CHUTES_API_KEY" if provider == "chutes" else "OPENROUTER_API_KEY"
    api_key = os.environ.get(key_env)
    if not api_key:
        raise RuntimeError(
            f"{key_env} is required for resource_gathering prompt generation (provider={provider})"
        )
    model = os.environ.get("NPABENCH_PROMPT_MODEL")
    if not model:
        raise RuntimeError(
            "NPABENCH_PROMPT_MODEL is required for resource_gathering prompt generation"
        )
    base_url = (
        os.environ.get("NPABENCH_PROMPT_BASE_URL")
        or PROVIDER_BASE_URLS.get(provider, PROVIDER_BASE_URLS["openrouter"])
    ).rstrip("/")
    temperature = float(os.environ.get("NPABENCH_PROMPT_TEMPERATURE", "0"))
    max_tokens = int(os.environ.get("NPABENCH_PROMPT_MAX_TOKENS", "180"))
    timeout_seconds = float(os.environ.get("NPABENCH_PROMPT_TIMEOUT_SECONDS", "8"))

    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write concise Minecraft benchmark prompts. Return only the prompt text. "
                    "Do not add bullet points, labels, explanations, or extra rules. "
                    "Vary the opening wording naturally instead of always starting with the same verb."
                ),
            },
            {
                "role": "user",
                "content": _prompt_brief(task),
            },
        ],
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"prompt generation failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"prompt generation request failed: {exc}") from exc

    prompt = _extract_prompt_text(payload).strip()
    if not prompt:
        raise RuntimeError("prompt generation returned an empty response")
    metadata = PromptMetadata(
        provider=provider,
        model=model,
        schema_version=PROMPT_SCHEMA_VERSION,
    )
    return prompt, metadata


def _prompt_brief(task: Task) -> str:
    target_lines = "\n".join(
        f"- {target.target_count} {target.display_name} ({target.role})"
        for target in task.targets
    )
    return (
        "Write one concise instruction for a Minecraft benchmark agent.\n"
        f"{PROMPT_EXAMPLES}\n"
        "Now write a prompt for this task.\n"
        "Requirements:\n"
        f"{target_lines}\n"
        "- keep the gathered items in inventory\n"
        "- finish within 20 blocks of spawn\n"
        "- vary the phrasing naturally; do not always start with 'Gather'\n"
        "Return only a single natural-language prompt."
    )


def _extract_prompt_text(payload: dict) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("prompt generation returned no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError("prompt generation returned an invalid message payload")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "".join(parts)
    raise RuntimeError("prompt generation returned no text content")
