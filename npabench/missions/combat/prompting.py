from __future__ import annotations

from pathlib import Path

from npabench.missions.base import PromptMetadata
from npabench.missions.combat.config_schema import TIER_ORDER
from npabench.missions.combat.task import CombatTask

PROMPT_SCHEMA_VERSION = "combat.v1"


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
    by_tier = {
        tier: [target for target in task.targets if target.tier == tier] for tier in TIER_ORDER
    }
    lines = [
        "You start with an empty inventory.",
        (
            f"Use the first {task.preparation_seconds // 60} minutes to gather materials and "
            "craft your own weapons, armor, shield, food, and defenses. Hostile mobs do not "
            "spawn during preparation, and crafting itself gives no points."
        ),
        (
            f"Combat then lasts {task.combat_seconds // 60} minutes. Natural hostile spawning "
            "will begin and target waves will appear near you. Only kills credited to you count."
        ),
        "Kill these targets:",
    ]
    tier_points = {"easy": 20, "medium": 35, "hard": 45}
    for tier in TIER_ORDER:
        descriptions = ", ".join(
            f"{target.target_count} {target.display_name}" for target in by_tier[tier]
        )
        lines.append(f"- {tier.title()} ({tier_points[tier]} points): {descriptions}")
    lines.extend(
        [
            "Each tier awards linear partial credit based on its weighted completed kill count.",
            "All three tiers total exactly 100 points. Extra and non-target kills give no points.",
            "Mob drops are yours to collect but do not add separate points.",
            "Emit ready before beginning and emit done when you want the run to end.",
        ]
    )
    return "\n".join(lines)
