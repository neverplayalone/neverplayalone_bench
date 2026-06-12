"""Capture final resource-gathering state from Minecraft over RCON."""

from __future__ import annotations

from typing import Any

from mcrcon import MCRcon

from mcbench.core.trace import FinalState
from mcbench.minecraft.commands import _count_item, _parse_pos, _parse_scalar, _read_score
from mcbench.tasks.resource_gathering.config_schema import ResourceGatheringTaskConfig
from mcbench.tasks.resource_gathering.scoring import _counted_items, _horizontal_distance_from_spawn


def capture_final_state(
    mcr: MCRcon,
    cfg: ResourceGatheringTaskConfig,
    setup_state: tuple[int, tuple[int, int, int] | None] | None,
) -> dict[str, Any]:
    death_baseline, spawn_pos = setup_state if setup_state else (0, None)
    state = FinalState()
    state.position = _parse_pos(mcr.command(f"data get entity {cfg.username} Pos"))
    state.health = _parse_scalar(mcr.command(f"data get entity {cfg.username} Health"))
    state.food = _parse_scalar(mcr.command(f"data get entity {cfg.username} foodLevel"))
    for resource in cfg.resources:
        total = 0
        for item in _counted_items(resource):
            count = _count_item(mcr, cfg.username, item)
            state.inventory[item] = count
            total += count
        state.inventory[resource.item] = total
    deaths = max(0, _read_score(mcr, cfg.username, "mcb_deaths") - death_baseline)
    distance_from_spawn = _horizontal_distance_from_spawn(state.position, spawn_pos)
    return {
        "final_state": state,
        "deaths": deaths,
        "alive": state.health is not None and state.health > 0,
        "spawn": {"position": spawn_pos},
        "distance_from_spawn": distance_from_spawn,
    }
