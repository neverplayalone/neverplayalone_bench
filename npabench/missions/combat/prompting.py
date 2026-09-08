from __future__ import annotations

from pathlib import Path

from npabench.missions.base import PromptMetadata
from npabench.missions.combat.config_schema import TIER_ORDER
from npabench.missions.combat.task import CombatTask

PROMPT_SCHEMA_VERSION = "combat.v2"


def _duration_text(seconds: int) -> str:
    minutes, remaining = divmod(seconds, 60)
    parts = []
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if remaining or not parts:
        parts.append(f"{remaining} second{'s' if remaining != 1 else ''}")
    return " ".join(parts)


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
            f"Use the first {_duration_text(task.preparation_seconds)} to gather materials and "
            "craft your own weapons, armor, shield, food, and defenses. Hostile mobs do not "
            "spawn during preparation, and crafting itself gives no points."
        ),
        (
            f"Combat then lasts {_duration_text(task.combat_seconds)}. "
            + (
                "Natural hostile spawning and staged target waves will begin. "
                if task.spawn_mobs_naturally
                else "Natural hostile spawning stays disabled; staged target waves will appear. "
            )
            + "Only kills credited to you count."
        ),
        (
            f"New spawns pause while {task.max_active_mobs} or more living, loaded mission "
            "enemies are present. Unloaded survivors can reappear when you revisit their area. "
            "Wave release times are measured from the start of combat, not the start of preparation. "
            "Released enemies wait in a queue when the active-enemy limit is full; "
            "a release time does not guarantee an immediate spawn."
        ),
        "Wave releases:",
    ]
    for wave in task.waves:
        tiers = ", ".join(
            tier for tier in TIER_ORDER if any(spawn.tier == tier for spawn in wave.spawns)
        )
        lines.append(f"- {_duration_text(wave.offset_seconds)}: {tiers or 'no target releases'}")
    lines.extend(
        [
            (
                "Reserve enemies replace missing kill opportunities only; they do not all spawn "
                "automatically. Completed targets need no additional spawns. Queued enemies and "
                "reserves still obey the active-enemy limit and the combat time limit."
            ),
            "Kill these targets:",
        ]
    )
    for tier in TIER_ORDER:
        descriptions = ", ".join(
            f"{target.target_count} {target.display_name}" for target in by_tier[tier]
        )
        tier_points = sum(target.points for target in by_tier[tier])
        lines.append(f"- {tier.title()} ({tier_points:g} points): {descriptions}")
    lines.extend(
        [
            "Each tier awards linear partial credit based on its weighted completed kill count.",
            "All three tiers total exactly 100 points. Extra and non-target kills give no points.",
            (
                f"Each death subtracts {task.death_penalty_points:g} points. "
                "Death penalties continue until your score reaches zero."
            ),
            (
                "You keep your inventory after death."
                if task.keep_inventory
                else "You lose carried inventory after death."
            ),
            "Mob drops are yours to collect but do not add separate points.",
            "Emit ready before beginning and emit done when you want the run to end.",
        ]
    )
    return "\n".join(lines)
