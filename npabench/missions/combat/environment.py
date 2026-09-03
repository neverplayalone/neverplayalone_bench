from __future__ import annotations

from typing import Any

from mcrcon import MCRcon

from npabench.minecraft.rcon_client import command_with_retry
from npabench.minecraft.rcon_helpers import read_score
from npabench.minecraft.spawn import use_world_spawn
from npabench.missions.combat.config_schema import CombatMissionConfig

DEATH_OBJECTIVE = "ncb_deaths"


def configure_combat_world(rcon: MCRcon, mission_config: CombatMissionConfig) -> None:
    command_with_retry(rcon, "gamerule keep_inventory false")
    command_with_retry(rcon, "gamerule advance_time false")
    command_with_retry(rcon, "gamerule advance_weather false")
    command_with_retry(rcon, "gamerule spawn_mobs false")
    command_with_retry(rcon, "gamerule mob_griefing false")
    command_with_retry(rcon, f"difficulty {mission_config.difficulty}")
    command_with_retry(rcon, f"time set {mission_config.spawn_time}")
    command_with_retry(rcon, "weather clear")
    command_with_retry(rcon, "worldborder center 0 0")
    command_with_retry(rcon, f"worldborder set {mission_config.world_size}")


def setup_combat_agent(rcon: MCRcon, mission_config: CombatMissionConfig) -> dict[str, Any]:
    command_with_retry(rcon, f"op {mission_config.username}")
    command_with_retry(rcon, f"clear {mission_config.username}")
    command_with_retry(rcon, f"effect clear {mission_config.username}")
    command_with_retry(rcon, "kill @e[tag=npabench_wave]")
    for entity_type in sorted({target.entity_type for target in mission_config.targets}):
        command_with_retry(rcon, f"kill @e[type=minecraft:{entity_type}]")
    # Entity kills can create drops, so clear item entities only after removing mobs.
    command_with_retry(rcon, "kill @e[type=item]")

    command_with_retry(rcon, f"scoreboard objectives remove {DEATH_OBJECTIVE}")
    command_with_retry(
        rcon,
        f"scoreboard objectives add {DEATH_OBJECTIVE} minecraft.custom:minecraft.deaths",
    )
    death_baseline = read_score(rcon, mission_config.username, DEATH_OBJECTIVE)

    kill_baselines: dict[str, int] = {}
    for target in mission_config.targets:
        command_with_retry(rcon, f"scoreboard objectives remove {target.objective}")
        command_with_retry(
            rcon,
            "scoreboard objectives add "
            f"{target.objective} minecraft.killed:minecraft.{target.entity_type}",
        )
        kill_baselines[target.key] = read_score(
            rcon,
            mission_config.username,
            target.objective,
        )

    spawn_pos = use_world_spawn(rcon, mission_config.username)
    command_with_retry(rcon, f"gamemode survival {mission_config.username}")
    command_with_retry(rcon, f"deop {mission_config.username}")
    return {
        "death_baseline": death_baseline,
        "kill_baselines": kill_baselines,
        "spawn": spawn_pos,
    }
