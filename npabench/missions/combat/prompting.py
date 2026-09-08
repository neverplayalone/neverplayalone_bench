from __future__ import annotations

from pathlib import Path

from npabench.missions.base import PromptMetadata
from npabench.missions.combat.task import CombatTask

PROMPT_SCHEMA_VERSION = "combat.v5"

PROMPT_TEMPLATES = (
    "Prepare your gear, then face the incoming waves and kill {targets}. "
    "Adapt to each wave and stay alive until the mission ends.",
    "Gather resources and craft your combat gear, then kill {targets} as enemy waves arrive. "
    "Adapt to each wave and stay alive until the mission ends.",
    "Get equipped for battle, then kill {targets} as enemy waves arrive. "
    "Adapt to each wave and stay alive until the mission ends.",
)


def materialize_task_prompt(task: CombatTask, output_dir: Path) -> CombatTask:
    del output_dir
    return task.model_copy(
        update={
            "prompt": fallback_prompt(task),
            "prompt_metadata": PromptMetadata(
                provider="template",
                model="none",
                schema_version=PROMPT_SCHEMA_VERSION,
            ),
        }
    )


def fallback_prompt(task: CombatTask) -> str:
    parts = []
    plurals = {"Drowned": "Drowned", "Witch": "Witches", "Enderman": "Endermen"}
    for target in task.targets:
        name = target.display_name
        if target.target_count != 1:
            name = plurals.get(name, f"{name}s")
        parts.append(f"{target.target_count} {name}")
    if len(parts) > 2:
        listed = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    else:
        listed = " and ".join(parts) or "no mobs"
    template = PROMPT_TEMPLATES[task.seed % len(PROMPT_TEMPLATES)]
    return template.format(targets=listed)
