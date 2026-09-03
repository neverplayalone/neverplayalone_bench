from __future__ import annotations

import time
from typing import Any

from npabench.evaluation.run_trace import AgentRunTrace
from npabench.missions.combat.config_schema import TIER_ORDER, CombatMissionConfig


def score_combat_run(
    mission_config: CombatMissionConfig,
    agent_run_trace: AgentRunTrace,
    final_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final_snapshot = final_snapshot or {}
    kills = final_snapshot.get("kills", {})
    drops = final_snapshot.get("drops", {})
    resources: list[dict[str, Any]] = []
    tiers: list[dict[str, Any]] = []
    total_score = 0.0

    for tier in TIER_ORDER:
        tier_targets = [target for target in mission_config.targets if target.tier == tier]
        required_units = sum(
            target.target_count * target.difficulty_weight for target in tier_targets
        )
        achieved_units = 0.0
        tier_rows: list[dict[str, Any]] = []
        for target in tier_targets:
            kill_count = max(0, int(kills.get(target.key, 0) or 0))
            achieved = min(kill_count, target.target_count)
            completion_ratio = achieved / target.target_count
            achieved_units += achieved * target.difficulty_weight
            points = target.points * completion_ratio
            row = {
                "key": target.key,
                "mob": target.entity_type,
                "display_name": target.display_name,
                "tier": tier,
                "kill_count": kill_count,
                "target_count": target.target_count,
                "achieved": achieved,
                "completion_ratio": completion_ratio,
                "difficulty_weight": target.difficulty_weight,
                "points": points,
                "max_points": target.points,
                "drop_items": list(target.drop_items),
                "collected_drops": {
                    item: int(drops.get(item, 0) or 0) for item in target.drop_items
                },
            }
            tier_rows.append(row)
            resources.append(row)

        max_points = mission_config.tier_rules[tier].points
        completion_ratio = achieved_units / required_units if required_units else 0.0
        tier_score = max_points * min(1.0, completion_ratio)
        total_score += tier_score
        tiers.append(
            {
                "tier": tier,
                "score": tier_score,
                "max_points": max_points,
                "required_difficulty_units": required_units,
                "achieved_difficulty_units": achieved_units,
                "completion_ratio": min(1.0, completion_ratio),
                "targets": tier_rows,
            }
        )

    runtime = final_snapshot.get("runtime")
    spawned = agent_run_trace.agent_ready_at is not None
    runtime_failed = isinstance(runtime, dict) and runtime.get("status") == "error"
    if runtime_failed or not spawned:
        total_score = 0.0
    status = "error" if runtime_failed else ("ok" if spawned else "agent_never_spawned")
    play_start = agent_run_trace.agent_ready_at or agent_run_trace.started_at
    elapsed = max(0.0, (agent_run_trace.ended_at or time.time()) - play_start)
    return {
        "task_id": mission_config.id,
        "agent": agent_run_trace.agent_name,
        "seed": mission_config.seed,
        "score": min(100.0, total_score),
        "max_score": 100.0,
        "spawned": spawned,
        "status": status,
        "tiers": tiers,
        "resources": resources,
        "kills": {key: int(value) for key, value in kills.items()},
        "drops": {key: int(value) for key, value in drops.items()},
        "preparation_seconds": mission_config.phase.preparation_seconds,
        "combat_seconds": mission_config.phase.combat_seconds,
        "elapsed_seconds": elapsed,
        "timed_out": agent_run_trace.timed_out,
        "alive": bool(final_snapshot.get("alive", False)),
        "deaths": int(final_snapshot.get("deaths", 0) or 0),
        "final_position": agent_run_trace.final_state.position,
        "runtime": runtime,
        "error": (runtime.get("errors") or runtime.get("error")) if runtime_failed else None,
    }
