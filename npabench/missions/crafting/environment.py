from __future__ import annotations

from mcrcon import MCRcon

from npabench.minecraft.rcon_client import command_with_retry
from npabench.minecraft.rcon_helpers import read_score
from npabench.minecraft.spawn import use_world_spawn
from npabench.missions.crafting.config_schema import CraftingMissionConfig


def configure_crafting_world(
    rcon: MCRcon,
    mission_config: CraftingMissionConfig,
) -> None:
    # 1.21.11 renamed every gamerule to snake_case (verified against the
    # GameRules NBT in a real reference-world level.dat: the server persists
    # `minecraft:keep_inventory`, `minecraft:advance_time`,
    # `minecraft:advance_weather`, `minecraft:spawn_mobs`). The other two
    # missions use the correct snake_case names for the first three but still
    # spell mob spawning the pre-1.21.11 way (`doMobSpawning`), which this
    # version no longer recognises -- see spawn_mobs below.
    command_with_retry(rcon, "gamerule keep_inventory false")
    command_with_retry(rcon, "gamerule advance_time true")
    command_with_retry(rcon, "gamerule advance_weather true")
    command_with_retry(rcon, "gamerule spawn_mobs false")
    command_with_retry(rcon, f"difficulty {mission_config.difficulty}")
    command_with_retry(rcon, f"time set {mission_config.spawn_time}")
    command_with_retry(rcon, "worldborder center 0 0")
    command_with_retry(rcon, f"worldborder set {mission_config.world_size}")


def setup_crafting_agent(
    rcon: MCRcon,
    mission_config: CraftingMissionConfig,
) -> tuple[int, tuple[int, int, int]]:
    command_with_retry(rcon, f"op {mission_config.username}")
    command_with_retry(rcon, f"clear {mission_config.username}")
    command_with_retry(rcon, "kill @e[type=item]")
    command_with_retry(rcon, "scoreboard objectives remove mcb_deaths")
    command_with_retry(rcon, "scoreboard objectives add mcb_deaths minecraft.custom:minecraft.deaths")
    death_baseline = read_score(rcon, mission_config.username, "mcb_deaths")
    spawn_pos = use_world_spawn(rcon, mission_config.username)
    # No `give` step: the agent starts empty and must punch its first log by hand.
    # Peaceful difficulty refills hunger on its own, so no food is needed either;
    # the saturation burst below just avoids a hungry first few seconds.
    command_with_retry(rcon, f"gamemode survival {mission_config.username}")
    command_with_retry(rcon, f"effect give {mission_config.username} minecraft:saturation 3 10 true")
    command_with_retry(rcon, f"deop {mission_config.username}")
    return death_baseline, spawn_pos
