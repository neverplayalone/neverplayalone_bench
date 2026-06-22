from __future__ import annotations

import math
import time
from typing import Any

from mcbench.evaluation.run_trace import AgentRunTrace
from mcbench.missions.resource_gathering.config_schema import (
    ResourceGatheringMissionConfig,
    ResourceSpec,
)


def score_resource_gathering_run(
    mission_config: ResourceGatheringMissionConfig,
    agent_run_trace: AgentRunTrace,
    final_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final_snapshot = final_snapshot or {}
    inventory = agent_run_trace.final_state.inventory
    resources: list[dict[str, Any]] = []
    resource_score = 0.0
    max_resource_score = 0.0

    for resource_spec in mission_config.resources:
        count = resource_count(inventory, resource_spec)
        achieved = min(count, resource_spec.target_count)
        points = resource_spec.points * achieved / resource_spec.target_count
        max_resource_score += resource_spec.points
        resource_score += points
        resources.append(
            {
                "item": resource_spec.item,
                "items": counted_items(resource_spec),
                "count": count,
                "target_count": resource_spec.target_count,
                "achieved": achieved,
                "points": points,
                "max_points": resource_spec.points,
            }
        )

    deaths = int(final_snapshot.get("deaths", 0) or 0)
    alive = bool(final_snapshot.get("alive", False))

    distance = final_snapshot.get("distance_from_spawn")
    multiplier = distance_multiplier(
        distance,
        mission_config.scoring.distance_bands,
        mission_config.scoring.distance_floor_mult,
    )
    total = resource_score * multiplier
    max_score = max_resource_score

    play_start = agent_run_trace.agent_ready_at or agent_run_trace.started_at
    elapsed = max(0.0, (agent_run_trace.ended_at or time.time()) - play_start)
    finished_early = not agent_run_trace.timed_out and any(
        event.kind == "done" for event in agent_run_trace.events
    )
    time_efficiency = (
        max(0.0, (mission_config.duration_seconds - elapsed) / mission_config.duration_seconds)
        if finished_early
        else 0.0
    )

    spawned = agent_run_trace.agent_ready_at is not None
    status = "ok" if spawned else "agent_never_spawned"
    return {
        "task_id": mission_config.id,
        "agent": agent_run_trace.agent_name,
        "seed": mission_config.seed,
        "score": total,
        "max_score": max_score,
        "spawned": spawned,
        "status": status,
        "resource_score": resource_score,
        "distance_multiplier": multiplier,
        "time_efficiency": time_efficiency,
        "elapsed_seconds": elapsed,
        "timed_out": agent_run_trace.timed_out,
        "alive": alive,
        "deaths": deaths,
        "resources": resources,
        "final_position": agent_run_trace.final_state.position,
        "final_health": agent_run_trace.final_state.health,
        "spawn": final_snapshot.get("spawn"),
        "distance_from_spawn": distance,
        "distance_bands": [list(band) for band in mission_config.scoring.distance_bands],
    }


def distance_multiplier(
    distance: float | None,
    bands: list[tuple[float, float]],
    floor: float,
) -> float:
    if distance is None:
        return floor
    for upper, multiplier in bands:
        if distance <= upper:
            return multiplier
    return floor


def horizontal_distance_from_spawn(
    position: tuple[float, float, float] | None,
    spawn_pos: tuple[int, int, int] | None,
) -> float | None:
    if position is None or spawn_pos is None:
        return None
    return math.sqrt((position[0] - spawn_pos[0]) ** 2 + (position[2] - spawn_pos[2]) ** 2)


def counted_items(resource: ResourceSpec) -> list[str]:
    return resource.items or [resource.item]


def resource_count(inventory: dict[str, int], resource: ResourceSpec) -> int:
    if resource.item in inventory:
        return inventory.get(resource.item, 0)
    return sum(inventory.get(item, 0) for item in counted_items(resource))
