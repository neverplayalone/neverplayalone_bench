from __future__ import annotations

from collections import Counter
from typing import Any

from mcrcon import MCRcon

from npabench.minecraft.rcon_client import command_with_retry
from npabench.minecraft.rcon_helpers import read_score
from npabench.minecraft.spawn import use_world_spawn
from npabench.missions.farming.config_schema import FarmingMissionConfig
from npabench.missions.farming.ledger import (
    HARVEST_OBJECTIVE,
    PLUGIN_SCORE_HOLDER,
    SYSTEM_OBJECTIVE,
)

DEATH_OBJECTIVE = "mcb_deaths"


def configure_farming_world(rcon: MCRcon, mission_config: FarmingMissionConfig) -> None:
    command_with_retry(
        rcon,
        "gamerule keep_inventory " + ("true" if mission_config.keep_inventory else "false"),
    )
    command_with_retry(rcon, "gamerule advance_time true")
    command_with_retry(rcon, "gamerule advance_weather true")
    command_with_retry(rcon, "gamerule spawn_mobs false")
    command_with_retry(rcon, "gamerule mob_griefing false")
    command_with_retry(
        rcon, f"gamerule random_tick_speed {mission_config.environment.random_tick_speed}"
    )
    command_with_retry(rcon, f"difficulty {mission_config.difficulty}")
    command_with_retry(rcon, f"time set {mission_config.spawn_time}")
    command_with_retry(rcon, "worldborder center 0 0")
    command_with_retry(rcon, f"worldborder set {mission_config.world_size}")


def setup_farming_agent(
    rcon: MCRcon,
    mission_config: FarmingMissionConfig,
) -> dict[str, Any]:
    command_with_retry(rcon, f"op {mission_config.username}")
    command_with_retry(rcon, f"clear {mission_config.username}")
    command_with_retry(rcon, f"effect clear {mission_config.username}")
    command_with_retry(rcon, "kill @e[type=item]")

    if read_score(rcon, PLUGIN_SCORE_HOLDER, SYSTEM_OBJECTIVE) != 1:
        raise RuntimeError("farming harvest plugin did not load")

    command_with_retry(rcon, f"scoreboard objectives remove {HARVEST_OBJECTIVE}")
    command_with_retry(rcon, f"scoreboard objectives add {HARVEST_OBJECTIVE} dummy")
    for target in mission_config.targets:
        command_with_retry(
            rcon,
            f"scoreboard players set {target.harvest_holder} {HARVEST_OBJECTIVE} 0",
        )

    # Minecraft chooses the world spawn from the random seed. Do not flatten
    # land, create crop plots, move the player to a hand-picked site, or filter
    # seeds. Only a supply barrel and a small, contained water pool are placed.
    spawn = use_world_spawn(rcon, mission_config.username)
    cache = _place_supply_cache(rcon, mission_config, spawn)
    water_source = _place_water_pool(rcon, spawn)

    command_with_retry(rcon, f"scoreboard objectives remove {DEATH_OBJECTIVE}")
    command_with_retry(
        rcon, f"scoreboard objectives add {DEATH_OBJECTIVE} minecraft.custom:minecraft.deaths"
    )
    command_with_retry(rcon, f"scoreboard players add {mission_config.username} {DEATH_OBJECTIVE} 0")
    death_baseline = read_score(rcon, mission_config.username, DEATH_OBJECTIVE)
    command_with_retry(rcon, f"gamemode survival {mission_config.username}")
    command_with_retry(rcon, f"deop {mission_config.username}")
    return {
        "death_baseline": death_baseline,
        "spawn": spawn,
        "supply_cache": cache,
        "plots": [],
        "sources": [],
        "water_source": water_source,
    }


def _place_supply_cache(
    rcon: MCRcon,
    mission_config: FarmingMissionConfig,
    spawn: tuple[int, int, int],
) -> tuple[int, int, int]:
    x, y, z = spawn
    bx, bz = x + 2, z
    command_with_retry(rcon, f"setblock {x + 1} {y - 1} {z} oak_planks")
    command_with_retry(rcon, f"setblock {x + 1} {y} {z} air")
    command_with_retry(rcon, f"setblock {x + 1} {y + 1} {z} air")
    command_with_retry(rcon, f"setblock {bx} {y - 1} {bz} oak_planks")
    command_with_retry(rcon, f"setblock {bx} {y} {bz} barrel")
    command_with_retry(rcon, f"setblock {bx} {y + 1} {bz} air")
    supplies: Counter[str] = Counter({"stone_hoe": 1, "stone_axe": 1, "bucket": 1})
    # Use a non-target wood type: the cache itself must not contain oak logs
    # that could be placed and immediately harvested for oak-tree credit.
    supplies["birch_log"] += mission_config.environment.utility_log_count
    for target in mission_config.targets:
        supplies[target.starter_item] += target.starter_count
        material = {
            "farmland": "dirt",
            "water_edge": "sand",
            "cactus": "sand",
            "bamboo": "dirt",
            "sweet_berries": "dirt",
            "stem": "dirt",
            "cocoa": "jungle_log",
            "kelp": "sand",
            "mushroom": "podzol",
            "glow_berries": "moss_block",
            "tree": "dirt",
            "nether_wart": "soul_sand",
        }[target.template]
        supplies[material] += 16
    if len(supplies) > 27:
        raise RuntimeError("farming supply kit exceeds barrel capacity")
    for slot, (item, count) in enumerate(sorted(supplies.items())):
        if count > 64:
            raise RuntimeError(f"farming supply stack exceeds 64: {item}={count}")
        command_with_retry(
            rcon,
            f"item replace block {bx} {y} {bz} container.{slot} with minecraft:{item} {count}",
        )
    return bx, y, bz


def _place_water_pool(rcon: MCRcon, spawn: tuple[int, int, int]) -> dict[str, Any]:
    """Build a refillable 2x2 pool without levelling the surrounding terrain."""
    x, y, z = spawn
    near_z, far_z = z + 4, z + 5

    # The solid 4x4 shell contains both water layers even on a cliff edge.
    # Its top is flush with the spawn walkway, so the player can reach in
    # with an empty bucket. Sand at the bottom also permits kelp planting.
    for command in (
        f"fill {x - 1} {y} {z + 3} {x + 2} {y + 2} {z + 6} air",
        f"fill {x - 1} {y - 3} {z + 3} {x + 2} {y - 1} {z + 6} dirt",
        f"fill {x} {y - 3} {near_z} {x + 1} {y - 3} {far_z} sand",
        f"fill {x} {y - 2} {near_z} {x + 1} {y - 1} {far_z} water",
        f"fill {x} {y - 1} {z + 1} {x} {y - 1} {z + 3} oak_planks",
        f"fill {x} {y} {z + 1} {x} {y + 1} {z + 3} air",
    ):
        command_with_retry(rcon, command)
    return {
        "position": (x, y - 1, near_z),
        "size": (2, 2),
        "depth": 2,
        "item": "bucket",
    }
