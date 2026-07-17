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
PROMPT_SCHEMA_VERSION = "mining.v1"

PROMPT_EXAMPLES = """Examples:
Task:
- 24 coal (essential)
- 14 iron (essential)
- 3 gold (essential)
- 3 diamond (optional)
- 16 redstone (optional)
Prompt:
Head underground and mine 24 coal, 14 iron, 3 gold, 3 diamond, and 16 redstone. Keep everything in your inventory, then climb back to the surface and finish within 20 blocks of spawn.

Task:
- 28 coal (essential)
- 18 iron (essential)
- 4 gold (essential)
- 20 copper (optional)
- 12 lapis lazuli (optional)
Prompt:
Dig down and bring back 28 coal, 18 iron, 4 gold, 20 copper, and 12 lapis lazuli. Keep the ores in your inventory and return to the surface within 20 blocks of spawn when you are done.

Task:
- 20 coal (essential)
- 12 iron (essential)
- 2 gold (essential)
- 2 diamond (optional)
- 18 copper (optional)
Prompt:
Mine 20 coal, 12 iron, 2 gold, 2 diamond, and 18 copper from underground. Keep them all in your inventory, then surface and end the run within 20 blocks of spawn."""


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
            f"{key_env} is required for mining prompt generation (provider={provider})"
        )
    model = os.environ.get("NPABENCH_PROMPT_MODEL")
    if not model:
        raise RuntimeError(
            "NPABENCH_PROMPT_MODEL is required for mining prompt generation"
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
        "- the ores must be mined from underground\n"
        "- keep the mined ores in inventory\n"
        "- return to the surface and finish within 20 blocks of spawn\n"
        "- vary the phrasing naturally; do not always start with 'Mine'\n"
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
