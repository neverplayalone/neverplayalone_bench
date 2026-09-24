from __future__ import annotations

import time
from typing import Any

from npabench.evaluation.run_trace import AgentRunTrace
from npabench.missions.farming.config_schema import FarmingMissionConfig


def score_farming_run(
    mission_config: FarmingMissionConfig,
    agent_run_trace: AgentRunTrace,
    final_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final_snapshot = final_snapshot or {}
    harvests = final_snapshot.get("harvests", {}) or {}
    runtime = final_snapshot.get("runtime") or {}
    resources: list[dict[str, Any]] = []
    tiers: list[dict[str, Any]] = []
    total_score = 0.0

    for difficulty in ("easy", "medium"):
        rows: list[dict[str, Any]] = []
        tier_score = 0.0
        tier_max = 0.0
        for target in (item for item in mission_config.targets if item.difficulty == difficulty):
            observed = max(0, int(harvests.get(target.key, 0) or 0))
            achieved = min(observed, target.target_count)
            completion_ratio = achieved / target.target_count
            points = target.points * completion_ratio
            row = {
                "key": target.key,
                "item": target.item,
                "display_name": target.display_name,
                "category": "crop",
                "tier": difficulty,
                "difficulty": difficulty,
                "template": target.template,
                "target_count": target.target_count,
                "achieved": achieved,
                "observed": observed,
                "harvested_units": observed,
                "completion_ratio": completion_ratio,
                "points": points,
                "max_points": target.points,
            }
            rows.append(row)
            resources.append(row)
            tier_score += points
            tier_max += target.points
        tiers.append(
            {
                "tier": difficulty,
                "score": tier_score,
                "max_points": tier_max,
                "completion_ratio": tier_score / tier_max if tier_max else 0.0,
                "targets": rows,
            }
        )
        total_score += tier_score

    farming_score = min(100.0, total_score)
    spawned = agent_run_trace.agent_ready_at is not None
    runtime_failed = isinstance(runtime, dict) and runtime.get("status") == "error"
    final_score = farming_score if spawned and not runtime_failed else 0.0
    status = "error" if runtime_failed else ("ok" if spawned else "agent_never_spawned")
    play_start = agent_run_trace.agent_ready_at or agent_run_trace.started_at
    elapsed = max(0.0, (agent_run_trace.ended_at or time.time()) - play_start)
    return {
        "task_id": mission_config.id,
        "agent": agent_run_trace.agent_name,
        "seed": mission_config.seed,
        "score": final_score,
        "ranking_score": final_score,
        "resource_score": farming_score,
        "distance_multiplier": 1.0,
        "max_score": 100.0,
        "spawned": spawned,
        "status": status,
        "tiers": tiers,
        "resources": resources,
        "harvests": {key: int(value) for key, value in harvests.items()},
        "inventory": final_snapshot.get("inventory", {}),
        "plots": final_snapshot.get("plots", []),
        "duration_seconds": mission_config.duration_seconds,
        "elapsed_seconds": elapsed,
        "timed_out": agent_run_trace.timed_out,
        "alive": bool(final_snapshot.get("alive", False)),
        "deaths": max(0, int(final_snapshot.get("deaths", 0) or 0)),
        "final_position": agent_run_trace.final_state.position,
        "runtime": runtime,
        "error": (runtime.get("errors") or runtime.get("error")) if runtime_failed else None,
    }
