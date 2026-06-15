"""Scoring: score = resource_score * distance multiplier (time only breaks ties)."""

from __future__ import annotations

import math
import time
from typing import Any

from mcbench.core.trace import Trace
from mcbench.tasks.resource_gathering.config_schema import (
    ResourceGatheringTaskConfig,
    ResourceTarget,
)

def score_resource_gathering(
    cfg: ResourceGatheringTaskConfig,
    trace: Trace,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = snapshot or {}
    inventory = trace.final_state.inventory
    resources: list[dict[str, Any]] = []
    resource_score = 0.0
    max_resource_score = 0.0

    # Resources count wherever they were gathered; returning near spawn is rewarded
    # separately by the graded distance component, not as an all-or-nothing gate.
    for spec in cfg.resources:
        count = _resource_count(inventory, spec)
        achieved = min(count, spec.target_count)
        score = spec.points * achieved / spec.target_count
        max_resource_score += spec.points
        resource_score += score
        resources.append(
            {
                "item": spec.item,
                "items": _counted_items(spec),
                "count": count,
                "target_count": spec.target_count,
                "achieved": achieved,
                "points": score,
                "max_points": spec.points,
            }
        )

    deaths = int(snapshot.get("deaths", 0) or 0)
    alive = bool(snapshot.get("alive", False))

    # Distance multiplier scales the whole resource score: full credit within
    # distance_radius of spawn, decaying linearly to distance_floor_mult. Because
    # the multiplier is floored (never 0), resources always count for something,
    # while returning home is still rewarded.
    distance = snapshot.get("distance_from_spawn")
    distance_multiplier = _distance_multiplier(
        distance,
        cfg.scoring.distance_bands,
        cfg.scoring.distance_floor_mult,
    )
    total = resource_score * distance_multiplier
    max_score = max_resource_score

    # Time efficiency is NOT part of the score — it is only a tie-breaker between
    # agents that finish with the same score. Measured over the agent-active window
    # (spawn -> end) so boot/world-load never count against the agent. Only an agent
    # that actually reported `done` earns it; a crash or timeout sits at 0 so
    # "finishing fast" by dying can't win a tie.
    play_start = trace.agent_ready_at or trace.started_at
    elapsed = max(0.0, (trace.ended_at or time.time()) - play_start)
    finished_early = not trace.timed_out and any(e.kind == "done" for e in trace.events)
    time_efficiency = (
        max(0.0, (cfg.duration_seconds - elapsed) / cfg.duration_seconds)
        if finished_early
        else 0.0
    )

    # An agent that never reported `ready` never had its kit/spawn setup applied,
    # so a score of 0 means a broken agent, not a poor strategy. agent_ready_at is
    # stamped on exactly that event, so it doubles as the spawned flag.
    spawned = trace.agent_ready_at is not None
    return {
        "task_id": cfg.id,
        "agent": trace.agent_name,
        "seed": cfg.seed,
        "score": total,
        "max_score": max_score,
        "spawned": spawned,
        "status": "ok" if spawned else "agent_never_spawned",
        "resource_score": resource_score,
        "distance_multiplier": distance_multiplier,
        "time_efficiency": time_efficiency,
        "elapsed_seconds": elapsed,
        "timed_out": trace.timed_out,
        "alive": alive,
        "deaths": deaths,
        "resources": resources,
        "final_position": trace.final_state.position,
        "final_health": trace.final_state.health,
        "spawn": snapshot.get("spawn"),
        "distance_from_spawn": distance,
        "distance_bands": [list(band) for band in cfg.scoring.distance_bands],
    }


def _distance_multiplier(
    distance: float | None,
    bands: list[tuple[float, float]],
    floor: float,
) -> float:
    """Multiplier applied to the resource score for ending near spawn.

    ``bands`` is a low-to-high list of (upper_distance, multiplier); the first band
    whose bound the distance falls within wins. Beyond the last band the multiplier
    is ``floor``. An unknown distance (snapshot failed) is treated as "did not
    verifiably return" -> ``floor``.
    """
    if distance is None:
        return floor
    for upper, multiplier in bands:
        if distance <= upper:
            return multiplier
    return floor


def _horizontal_distance_from_spawn(
    position: tuple[float, float, float] | None,
    spawn_pos: tuple[int, int, int] | None,
) -> float | None:
    if position is None or spawn_pos is None:
        return None
    return math.sqrt((position[0] - spawn_pos[0]) ** 2 + (position[2] - spawn_pos[2]) ** 2)


def _counted_items(resource: ResourceTarget) -> list[str]:
    return resource.items or [resource.item]


def _resource_count(inventory: dict[str, int], resource: ResourceTarget) -> int:
    if resource.item in inventory:
        return inventory.get(resource.item, 0)
    return sum(inventory.get(item, 0) for item in _counted_items(resource))

