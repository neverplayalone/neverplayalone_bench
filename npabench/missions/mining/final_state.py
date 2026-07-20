from __future__ import annotations

from typing import Any

from mcrcon import MCRcon

from npabench.evaluation.run_trace import FinalAgentState
from npabench.minecraft.rcon_helpers import count_item, parse_pos, parse_scalar, read_score
from npabench.missions.mining.config_schema import MiningMissionConfig
from npabench.missions.mining.scoring import (
    counted_items,
    distance_from_spawn_3d,
)


def collect_mining_state(
    rcon: MCRcon,
    mission_config: MiningMissionConfig,
    setup_state: tuple[int, tuple[int, int, int] | None] | None,
) -> dict[str, Any]:
    death_baseline, spawn_pos = setup_state if setup_state else (0, None)
    final_state = FinalAgentState()
    final_state.position = parse_pos(rcon.command(f"data get entity {mission_config.username} Pos"))
    final_state.health = parse_scalar(rcon.command(f"data get entity {mission_config.username} Health"))
    final_state.food = parse_scalar(rcon.command(f"data get entity {mission_config.username} foodLevel"))
    for resource in mission_config.resources:
        total = 0
        for item in counted_items(resource):
            count = count_item(rcon, mission_config.username, item)
            final_state.inventory[item] = count
            total += count
        final_state.inventory[resource.item] = total
    deaths = max(0, read_score(rcon, mission_config.username, "mcb_deaths") - death_baseline)
    distance_from_spawn = distance_from_spawn_3d(final_state.position, spawn_pos)
    return {
        "final_state": final_state,
        "deaths": deaths,
        "alive": final_state.health is not None and final_state.health > 0,
        "spawn": {"position": spawn_pos},
        "distance_from_spawn": distance_from_spawn,
    }
