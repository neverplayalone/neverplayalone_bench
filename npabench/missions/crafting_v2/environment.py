from __future__ import annotations

import json
import math
import random

from mcrcon import MCRcon

from npabench.minecraft.rcon_client import command_with_retry
from npabench.minecraft.rcon_helpers import read_score
from npabench.minecraft.spawn import use_world_spawn
from npabench.missions.base import StartingItem
from npabench.missions.crafting_v2.config_schema import CraftingV2MissionConfig

# Fixed salt: deterministic from the world seed, distinct from mining (0x304D494E)
# and v1's deposit if it ever gets one. 0x43525632 = "CRV2".
_DEPOSIT_SALT = 0x43525632


def configure_crafting_v2_world(
    rcon: MCRcon,
    mission_config: CraftingV2MissionConfig,
) -> None:
    # 1.21.11 gamerules are snake_case (verified against a real reference-world
    # level.dat GameRules NBT); note spawn_mobs, not the pre-1.21.11 doMobSpawning
    # the mining/resource_gathering missions still use.
    command_with_retry(rcon, "gamerule keep_inventory false")
    command_with_retry(rcon, "gamerule advance_time true")
    command_with_retry(rcon, "gamerule advance_weather true")
    command_with_retry(rcon, "gamerule spawn_mobs false")
    command_with_retry(rcon, f"difficulty {mission_config.difficulty}")
    command_with_retry(rcon, f"time set {mission_config.spawn_time}")
    command_with_retry(rcon, "worldborder center 0 0")
    command_with_retry(rcon, f"worldborder set {mission_config.world_size}")
    if mission_config.deposit.enabled:
        _place_iron_deposit(rcon, mission_config)


def _iron_demand(mission_config: CraftingV2MissionConfig) -> float:
    """Total iron ingots the iron-band targets require this run, read from the
    per-recipe cost that build_mission_config carried through."""
    return sum(recipe.cost.iron * recipe.target_count for recipe in mission_config.recipes)


def _place_iron_deposit(
    rcon: MCRcon,
    mission_config: CraftingV2MissionConfig,
) -> None:
    """Place a compact, guaranteed vein of each configured ore underground near
    world spawn (0,0), so the iron tier is solvable regardless of seed ore-luck.

    The iron vein is sized to the run's actual iron demand (times over_provision)
    so partial mining still suffices; fuel ores get a fixed floor. Each ore sits
    at its own depth so the agent still has to dig down. Placement is
    deterministic from the world seed; the per-validator seed shifts the spot so
    it can't be pre-memorised. Natural ores stay in the world -- this is a floor,
    not the only source."""
    settings = mission_config.deposit
    rng = random.Random(mission_config.seed ^ _DEPOSIT_SALT)
    base_x = rng.randint(-settings.offset_range, settings.offset_range)
    base_z = rng.randint(-settings.offset_range, settings.offset_range)
    iron_demand = _iron_demand(mission_config)
    for ore in settings.ores.values():
        if ore.scale_to == "iron":
            drops_needed = math.ceil(iron_demand * settings.over_provision)
            blocks_needed = max(ore.min_blocks, math.ceil(drops_needed / ore.drops_per_block))
        else:
            blocks_needed = ore.min_blocks
        width = max(1, math.ceil(math.sqrt(blocks_needed)))
        ox = base_x + rng.randint(-6, 6)
        oz = base_z + rng.randint(-6, 6)
        command_with_retry(
            rcon,
            f"fill {ox} {ore.depth_y} {oz} {ox + width - 1} {ore.depth_y} {oz + width - 1} "
            f"minecraft:{ore.block}",
        )


def setup_crafting_v2_agent(
    rcon: MCRcon,
    mission_config: CraftingV2MissionConfig,
) -> tuple[int, tuple[int, int, int]]:
    command_with_retry(rcon, f"op {mission_config.username}")
    command_with_retry(rcon, f"clear {mission_config.username}")
    command_with_retry(rcon, "kill @e[type=item]")
    command_with_retry(rcon, "scoreboard objectives remove mcb_deaths")
    command_with_retry(rcon, "scoreboard objectives add mcb_deaths minecraft.custom:minecraft.deaths")
    death_baseline = read_score(rcon, mission_config.username, "mcb_deaths")
    spawn_pos = use_world_spawn(rcon, mission_config.username)
    # The stone-tool kit. Unlike v1 (empty hands), v2 hands over the tooling so
    # the run is spent on the iron tier. Peaceful refills hunger, so no food is
    # given; the saturation burst just avoids a hungry first few seconds.
    for starting_item in mission_config.starting_items:
        _give_starting_item(rcon, mission_config.username, starting_item)
    command_with_retry(rcon, f"gamemode survival {mission_config.username}")
    command_with_retry(rcon, f"effect give {mission_config.username} minecraft:saturation 3 10 true")
    command_with_retry(rcon, f"deop {mission_config.username}")
    return death_baseline, spawn_pos


def _give_starting_item(rcon: MCRcon, username: str, starting_item: StartingItem) -> None:
    # `give` is NOT idempotent -- a timeout can land after the server already
    # applied it -- so it must not go through command_with_retry (mirrors mining's
    # give_starting_item).
    item_stack = _starting_item_stack(starting_item)
    if starting_item.slot:
        rcon.command(
            f"item replace entity {username} {starting_item.slot} with "
            f"{item_stack} {starting_item.count}"
        )
    else:
        rcon.command(f"give {username} {item_stack} {starting_item.count}")


def _starting_item_stack(starting_item: StartingItem) -> str:
    item = f"minecraft:{starting_item.item}"
    if not starting_item.enchantments:
        return item
    enchantment_levels = {
        f"minecraft:{name}": _enchantment_level(raw)
        for name, raw in ((raw.partition(":")[0], raw) for raw in starting_item.enchantments)
    }
    enchantment_json = json.dumps(enchantment_levels, separators=(",", ":"))
    return f"{item}[minecraft:enchantments={enchantment_json}]"


def _enchantment_level(raw: str) -> int:
    _, _, level_raw = raw.partition(":")
    return int(level_raw or "1")
