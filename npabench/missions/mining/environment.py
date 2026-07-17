from __future__ import annotations

import json
import math
import random

from mcrcon import MCRcon

from npabench.minecraft.rcon_helpers import read_score
from npabench.minecraft.rcon_client import command_with_retry
from npabench.minecraft.spawn import use_world_spawn
from npabench.missions.base import StartingItem
from npabench.missions.mining.config_schema import MiningMissionConfig

# Per-ore placement metadata used ONLY by the optional deterministic deposit
# (config: deposit.enabled). Each entry is (ore_block_id, drops_per_block,
# characteristic_depth_y). Deepslate variants are used below y=0; both stone and
# deepslate variants drop the same item, so scoring is unaffected either way.
ORE_META: dict[str, tuple[str, int, int]] = {
    "copper": ("copper_ore", 3, 48),
    "coal": ("coal_ore", 1, 40),
    "iron": ("iron_ore", 1, 8),
    "lapis": ("lapis_ore", 6, 0),
    "gold": ("deepslate_gold_ore", 1, -16),
    "redstone": ("deepslate_redstone_ore", 5, -55),
    "diamond": ("deepslate_diamond_ore", 1, -58),
}


def configure_mining_world(
    rcon: MCRcon,
    mission_config: MiningMissionConfig,
) -> None:
    command_with_retry(rcon, "gamerule keep_inventory false")
    command_with_retry(rcon, "gamerule advance_time true")
    command_with_retry(rcon, "gamerule advance_weather true")
    command_with_retry(rcon, "gamerule doMobSpawning false")
    command_with_retry(rcon, f"difficulty {mission_config.difficulty}")
    command_with_retry(rcon, f"time set {mission_config.spawn_time}")
    command_with_retry(rcon, "worldborder center 0 0")
    command_with_retry(rcon, f"worldborder set {mission_config.world_size}")
    if mission_config.deposit.enabled:
        _place_ore_deposit(rcon, mission_config)


def _place_ore_deposit(
    rcon: MCRcon,
    mission_config: MiningMissionConfig,
) -> None:
    """Place a compact, guaranteed vein of each target ore underground near the
    world spawn (0,0), so the task is solvable regardless of seed ore-luck. Each
    ore sits at its own characteristic depth, so the agent still has to descend
    and dig through the layers. Placement is deterministic from the world seed;
    the per-validator seed shifts the exact spot so it can't be pre-memorized."""
    settings = mission_config.deposit
    rng = random.Random(mission_config.seed ^ 0x304D494E)  # 0x304D494E = "0MIN", a fixed mining salt
    base_x = rng.randint(-settings.offset_range, settings.offset_range)
    base_z = rng.randint(-settings.offset_range, settings.offset_range)
    for spec in mission_config.resources:
        meta = ORE_META.get(spec.item)
        if meta is None:
            continue
        ore_block, drops_per_block, depth_y = meta
        blocks_needed = max(1, math.ceil(spec.target_count / drops_per_block * settings.over_provision))
        width = max(1, math.ceil(math.sqrt(blocks_needed)))
        # spread each ore a little around the dig site so it isn't one perfect column
        ox = base_x + rng.randint(-8, 8)
        oz = base_z + rng.randint(-8, 8)
        command_with_retry(
            rcon,
            f"fill {ox} {depth_y} {oz} {ox + width - 1} {depth_y} {oz + width - 1} minecraft:{ore_block}",
        )


def setup_mining_agent(
    rcon: MCRcon,
    mission_config: MiningMissionConfig,
) -> tuple[int, tuple[int, int, int]]:
    command_with_retry(rcon, f"op {mission_config.username}")
    command_with_retry(rcon, f"clear {mission_config.username}")
    command_with_retry(rcon, "kill @e[type=item]")
    command_with_retry(rcon, "scoreboard objectives remove mcb_deaths")
    command_with_retry(rcon, "scoreboard objectives add mcb_deaths minecraft.custom:minecraft.deaths")
    death_baseline = read_score(rcon, mission_config.username, "mcb_deaths")
    spawn_pos = use_world_spawn(rcon, mission_config.username)
    for starting_item in mission_config.starting_items:
        give_starting_item(rcon, mission_config.username, starting_item)
    command_with_retry(rcon, f"gamemode survival {mission_config.username}")
    command_with_retry(rcon, f"effect give {mission_config.username} minecraft:saturation 3 10 true")
    command_with_retry(rcon, f"deop {mission_config.username}")
    return death_baseline, spawn_pos


def give_starting_item(rcon: MCRcon, username: str, starting_item: StartingItem) -> None:
    item_stack = starting_item_stack(starting_item)
    if starting_item.slot:
        rcon.command(
            f"item replace entity {username} {starting_item.slot} with "
            f"{item_stack} {starting_item.count}"
        )
    else:
        rcon.command(f"give {username} {item_stack} {starting_item.count}")


def starting_item_stack(starting_item: StartingItem) -> str:
    item = f"minecraft:{starting_item.item}"
    if not starting_item.enchantments:
        return item
    enchantment_levels = {
        f"minecraft:{name}": level
        for name, level in starting_item_enchantments(starting_item)
    }
    enchantment_json = json.dumps(enchantment_levels, separators=(",", ":"))
    return f"{item}[minecraft:enchantments={enchantment_json}]"


def starting_item_enchantments(starting_item: StartingItem) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for raw in starting_item.enchantments:
        name, _, level_raw = raw.partition(":")
        level = int(level_raw or "1")
        out.append((name, level))
    return out
