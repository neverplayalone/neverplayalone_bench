from __future__ import annotations

from typing import Any

from mcrcon import MCRcon

from npabench.evaluation.run_trace import FinalAgentState
from npabench.minecraft.rcon_helpers import count_item, parse_pos, parse_scalar, read_score
from npabench.missions.combat.config_schema import CombatMissionConfig
from npabench.missions.combat.environment import DEATH_OBJECTIVE


def collect_combat_state(
    rcon: MCRcon,
    mission_config: CombatMissionConfig,
    setup_state: dict[str, Any] | None,
) -> dict[str, Any]:
    setup_state = setup_state or {}
    baselines = setup_state.get("kill_baselines", {})
    final_state = FinalAgentState()
    final_state.position = parse_pos(rcon.command(f"data get entity {mission_config.username} Pos"))
    final_state.health = parse_scalar(
        rcon.command(f"data get entity {mission_config.username} Health")
    )
    final_state.food = parse_scalar(
        rcon.command(f"data get entity {mission_config.username} foodLevel")
    )

    kills: dict[str, int] = {}
    for target in mission_config.targets:
        final_count = read_score(rcon, mission_config.username, target.objective)
        kills[target.key] = max(0, final_count - int(baselines.get(target.key, 0)))

    drop_items = sorted({item for target in mission_config.targets for item in target.drop_items})
    drops = {item: count_item(rcon, mission_config.username, item) for item in drop_items}
    final_state.inventory.update(drops)

    death_baseline = int(setup_state.get("death_baseline", 0))
    deaths = max(
        0,
        read_score(rcon, mission_config.username, DEATH_OBJECTIVE) - death_baseline,
    )
    return {
        "final_state": final_state,
        "kills": kills,
        "drops": drops,
        "deaths": deaths,
        "alive": final_state.health is not None and final_state.health > 0,
        "spawn": {"position": setup_state.get("spawn")},
    }
