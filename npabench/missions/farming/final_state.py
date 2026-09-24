from __future__ import annotations

from typing import Any

from mcrcon import MCRcon

from npabench.evaluation.run_trace import FinalAgentState
from npabench.minecraft.rcon_helpers import count_item, parse_pos, parse_scalar, read_score
from npabench.missions.farming.config_schema import FarmingMissionConfig
from npabench.missions.farming.datapack import HARVEST_OBJECTIVE
from npabench.missions.farming.environment import DEATH_OBJECTIVE


def collect_farming_state(
    rcon: MCRcon,
    mission_config: FarmingMissionConfig,
    setup_state: dict[str, Any] | None,
) -> dict[str, Any]:
    setup_state = setup_state or {}
    final_state = FinalAgentState()
    final_state.position = parse_pos(rcon.command(f"data get entity {mission_config.username} Pos"))
    final_state.health = parse_scalar(
        rcon.command(f"data get entity {mission_config.username} Health")
    )
    final_state.food = parse_scalar(
        rcon.command(f"data get entity {mission_config.username} foodLevel")
    )

    harvests = {
        target.key: max(0, read_score(rcon, target.harvest_holder, HARVEST_OBJECTIVE))
        for target in mission_config.targets
    }
    inventory = {
        target.item: count_item(rcon, mission_config.username, target.item)
        for target in mission_config.targets
    }
    final_state.inventory.update(inventory)
    death_baseline = int(setup_state.get("death_baseline", 0))
    deaths = max(
        0,
        read_score(rcon, mission_config.username, DEATH_OBJECTIVE) - death_baseline,
    )
    return {
        "final_state": final_state,
        "harvests": harvests,
        "inventory": inventory,
        "deaths": deaths,
        "alive": final_state.health is not None and final_state.health > 0,
        "spawn": {"position": setup_state.get("spawn")},
        "plots": setup_state.get("plots", []),
    }
