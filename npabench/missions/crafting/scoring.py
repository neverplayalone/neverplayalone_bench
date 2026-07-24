from __future__ import annotations

import math
import time
from typing import Any

from npabench.evaluation.run_trace import AgentRunTrace
from npabench.missions.crafting.config_schema import CraftingMissionConfig, RecipeSpec

TIME_EFFICIENCY_TIEBREAKER_MAX = 1e-3


def score_crafting_run(
    mission_config: CraftingMissionConfig,
    agent_run_trace: AgentRunTrace,
    final_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final_snapshot = final_snapshot or {}
    inventory = agent_run_trace.final_state.inventory
    recipes: list[dict[str, Any]] = []
    base_score = 0.0
    max_recipe_score = 0.0

    for recipe_spec in mission_config.recipes:
        count = recipe_count(inventory, recipe_spec)
        achieved = min(count, recipe_spec.target_count)
        completion_ratio = achieved / recipe_spec.target_count
        points = recipe_spec.points * completion_ratio
        max_recipe_score += recipe_spec.points
        base_score += points
        recipes.append(
            {
                "item": recipe_spec.item,
                "display_name": recipe_spec.display_name
                or recipe_spec.item.replace("_", " "),
                "items": counted_items(recipe_spec),
                "count": count,
                "target_count": recipe_spec.target_count,
                "achieved": achieved,
                "completion_ratio": completion_ratio,
                "points": points,
                "max_points": recipe_spec.points,
                # `band` replaces the other missions' `role`: every crafting target
                # is rolled, so there is no fixed/optional split to report, and
                # band says the thing that actually matters (which station gates
                # this item).
                "band": recipe_spec.band,
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
    total = base_score * multiplier
    max_score = max_recipe_score

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
    # Preserve the task score as the primary metric and only use time
    # efficiency to break exact score ties among early-finished runs.
    ranking_score = total + (time_efficiency * TIME_EFFICIENCY_TIEBREAKER_MAX)

    spawned = agent_run_trace.agent_ready_at is not None
    status = "ok" if spawned else "agent_never_spawned"
    return {
        "task_id": mission_config.id,
        "agent": agent_run_trace.agent_name,
        "seed": mission_config.seed,
        "score": total,
        "ranking_score": ranking_score,
        "max_score": max_score,
        "spawned": spawned,
        "status": status,
        "recipe_score": base_score,
        "distance_multiplier": multiplier,
        "time_efficiency": time_efficiency,
        "elapsed_seconds": elapsed,
        "timed_out": agent_run_trace.timed_out,
        "alive": alive,
        "deaths": deaths,
        # Emitted under the `resources` key that the API and dashboard already
        # read for the other two missions, even though crafting calls them
        # recipes internally.
        "resources": recipes,
        "biome": mission_config.biome,
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


def distance_from_spawn_3d(
    position: tuple[float, float, float] | None,
    spawn_pos: tuple[int, int, int] | None,
) -> float | None:
    """Full 3D distance from the pinned spawn, as in mining. The target list is a
    delivery manifest, so an agent that crafted everything but is still 15 blocks
    underground beneath spawn has not handed anything over."""
    if position is None or spawn_pos is None:
        return None
    return math.sqrt(
        (position[0] - spawn_pos[0]) ** 2
        + (position[1] - spawn_pos[1]) ** 2
        + (position[2] - spawn_pos[2]) ** 2
    )


def counted_items(recipe: RecipeSpec) -> list[str]:
    return recipe.items or [recipe.item]


def recipe_count(inventory: dict[str, int], recipe: RecipeSpec) -> int:
    if recipe.item in inventory:
        return inventory.get(recipe.item, 0)
    return sum(inventory.get(item, 0) for item in counted_items(recipe))
