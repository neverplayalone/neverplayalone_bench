from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from npabench.missions.base import PromptMetadata
from npabench.missions.crafting_v3.task import CraftingV3Task, CraftingV3TaskTarget

log = logging.getLogger("npabench.crafting_v3.prompting")

PROVIDER_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "chutes": "https://llm.chutes.ai/v1",
}
PROMPT_SCHEMA_VERSION = "crafting.v3"

# One retry, then the deterministic template. At temperature 0 a straight retry
# reproduces the same text, so the second attempt nudges the temperature.
PROMPT_ATTEMPTS = 2
RETRY_TEMPERATURE = 0.4

# Diamond-tier few-shot. The tasks reach through stone/smelting into diamond
# items, so the examples show the lower tiers as the way up to the diamond ones.
PROMPT_EXAMPLES = """Examples:
Task:
- 24 Cobblestone Slab
- 4 Stone
- 1 Diamond Pickaxe
- 1 Diamond Chestplate
Prompt:
Craft 24 Cobblestone Slabs, 4 Stone, 1 Diamond Pickaxe, and 1 Diamond Chestplate, then return to within 20 blocks of your starting point with everything on you.

Task:
- 16 Cobblestone Wall
- 2 Stone Bricks
- 1 Diamond Sword
- 1 Diamond Helmet
- 1 Block of Diamond
Prompt:
Your goal is to make 16 Cobblestone Walls, 2 Stone Bricks, 1 Diamond Sword, 1 Diamond Helmet, and 1 Block of Diamond. Keep them all and finish back near where you spawned, within 20 blocks.

Task:
- 12 Cobblestone Stairs
- 1 Smooth Stone
- 2 Diamond Shovel
- 1 Diamond Boots
- 1 Jukebox
Prompt:
Assemble 12 Cobblestone Stairs, 1 Smooth Stone, 2 Diamond Shovels, 1 Diamond Boots, and 1 Jukebox — hold on to every one and end the run within 20 blocks of your start."""


def materialize_task_prompt(task: CraftingV3Task, output_dir: Path) -> CraftingV3Task:
    cached_task = _load_cached_task(output_dir / "task.json")
    if cached_task is not None and _can_reuse_cached_prompt(task, cached_task):
        return task.model_copy(
            update={
                "prompt": cached_task.prompt,
                "prompt_metadata": cached_task.prompt_metadata,
            }
        )

    prompt, metadata = _resolve_prompt(task)
    return task.model_copy(update={"prompt": prompt, "prompt_metadata": metadata})


def _load_cached_task(path: Path) -> CraftingV3Task | None:
    if not path.exists():
        return None
    try:
        return CraftingV3Task.model_validate_json(path.read_text())
    except Exception:
        return None


def _can_reuse_cached_prompt(task: CraftingV3Task, cached_task: CraftingV3Task) -> bool:
    metadata = cached_task.prompt_metadata
    return (
        cached_task.task_id == task.task_id
        and cached_task.targets == task.targets
        and metadata is not None
        and metadata.schema_version == PROMPT_SCHEMA_VERSION
        and bool(cached_task.prompt.strip())
    )


def _resolve_prompt(task: CraftingV3Task) -> tuple[str, PromptMetadata]:
    """Generate the prompt, verifying it still names every target faithfully.

    If a model renders "1 Smithing Table" as "a crafting bench" the agent cannot
    map it back to an item id, so a generated prompt is only accepted when every
    display name and count survived; otherwise we fall back to a deterministic
    template. The fallback also means a missing API key degrades the prompt
    instead of failing the run."""
    for attempt in range(PROMPT_ATTEMPTS):
        try:
            prompt, metadata = _generate_prompt(task, attempt=attempt)
        except Exception as exc:  # noqa: BLE001 - a prompt must never fail a run
            # Deliberately broad: no provider failure may abort an evaluation,
            # and provider failures are not all RuntimeError -- a socket read
            # timeout surfaces as a bare TimeoutError out of ssl.
            log.warning("crafting_v3 prompt generation failed (attempt %s): %s", attempt + 1, exc)
            break
        missing = unfaithful_targets(prompt, task.targets)
        if not missing:
            return prompt, metadata
        log.warning(
            "crafting_v3 prompt dropped or renamed targets %s (attempt %s); prompt=%r",
            missing,
            attempt + 1,
            prompt,
        )

    log.warning("crafting_v3 falling back to the deterministic prompt template")
    return fallback_prompt(task.targets), PromptMetadata(
        provider="template",
        model="none",
        schema_version=PROMPT_SCHEMA_VERSION,
    )


def unfaithful_targets(prompt: str, targets: list[CraftingV3TaskTarget]) -> list[str]:
    """Targets whose display name or count did not survive into the prompt.

    Substring match so natural plurals pass ("Doors" contains "Door"), and a
    word-boundary match on the count so a request for 1 is not satisfied by an
    unrelated "12" elsewhere."""
    lowered = prompt.lower()
    missing: list[str] = []
    for target in targets:
        name = (target.display_name or target.key.replace("_", " ")).lower()
        has_name = name in lowered
        has_count = re.search(rf"\b{target.target_count}\b", prompt) is not None
        if not (has_name and has_count):
            missing.append(target.key)
    return missing


def fallback_prompt(targets: list[CraftingV3TaskTarget]) -> str:
    parts = [
        f"{target.target_count} {target.display_name or target.key.replace('_', ' ')}"
        for target in targets
    ]
    if len(parts) > 1:
        listed = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    else:
        listed = parts[0] if parts else "nothing"
    return (
        f"Craft {listed}. Keep them all in your inventory and finish "
        "within 20 blocks of where you started."
    )


def _generate_prompt(task: CraftingV3Task, *, attempt: int = 0) -> tuple[str, PromptMetadata]:
    provider = os.environ.get("NPABENCH_PROMPT_PROVIDER", "openrouter").strip().lower()
    key_env = "CHUTES_API_KEY" if provider == "chutes" else "OPENROUTER_API_KEY"
    api_key = os.environ.get(key_env)
    if not api_key:
        raise RuntimeError(
            f"{key_env} is required for crafting_v3 prompt generation (provider={provider})"
        )
    model = os.environ.get("NPABENCH_PROMPT_MODEL")
    if not model:
        raise RuntimeError("NPABENCH_PROMPT_MODEL is required for crafting_v3 prompt generation")
    base_url = (
        os.environ.get("NPABENCH_PROMPT_BASE_URL")
        or PROVIDER_BASE_URLS.get(provider, PROVIDER_BASE_URLS["openrouter"])
    ).rstrip("/")
    temperature = float(os.environ.get("NPABENCH_PROMPT_TEMPERATURE", "0"))
    if attempt:
        temperature = max(temperature, RETRY_TEMPERATURE)
    max_tokens = int(os.environ.get("NPABENCH_PROMPT_MAX_TOKENS", "220"))
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
            {"role": "user", "content": _prompt_brief(task)},
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
    except OSError as exc:
        # Read timeouts raise a bare TimeoutError (an OSError) from the socket
        # layer rather than a URLError, so they need their own arm.
        raise RuntimeError(f"prompt generation timed out: {exc}") from exc

    prompt = _extract_prompt_text(payload).strip()
    if not prompt:
        raise RuntimeError("prompt generation returned an empty response")
    metadata = PromptMetadata(
        provider=provider,
        model=model,
        schema_version=PROMPT_SCHEMA_VERSION,
    )
    return prompt, metadata


def _prompt_brief(task: CraftingV3Task) -> str:
    target_lines = "\n".join(
        f"- {target.target_count} {target.display_name or target.key.replace('_', ' ')}"
        for target in task.targets
    )
    # Deliberately absent: that the agent starts with stone tools, that a furnace
    # must be placed, where iron comes from. Those are world/kit facts the agent
    # discovers by looking at its inventory and surroundings. Only scoring
    # criteria -- the item list, holding them, the radius -- belong here.
    return (
        "Write one concise instruction for a Minecraft benchmark agent.\n"
        f"{PROMPT_EXAMPLES}\n"
        "Now write a prompt for this task.\n"
        "Requirements:\n"
        f"{target_lines}\n"
        "- every listed item must be crafted by the agent\n"
        "- keep all of them in inventory\n"
        "- finish within 20 blocks of where the agent started\n"
        "- use each item name exactly as written above; do not rename or abbreviate\n"
        "- write every count as a digit, including 1\n"
        "- vary the phrasing naturally; do not always start with 'Craft'\n"
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
