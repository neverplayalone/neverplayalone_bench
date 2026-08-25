from __future__ import annotations

import json

from mcrcon import MCRcon

from npabench.minecraft.rcon_client import command_with_retry
from npabench.minecraft.rcon_helpers import read_score
from npabench.minecraft.spawn import use_world_spawn
from npabench.missions.base import StartingItem
from npabench.missions.crafting_v3.config_schema import CraftingV3MissionConfig


def configure_crafting_v3_world(
    rcon: MCRcon,
    mission_config: CraftingV3MissionConfig,
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
    # No ore placement: iron is one of the most common ores, so the mission relies
    # on the world's naturally generated iron (structures stay off, but ore veins
    # are terrain, not structures, so natural iron is always present). This keeps
    # the world fully natural and every land seed solvable.


def setup_crafting_v3_agent(
    rcon: MCRcon,
    mission_config: CraftingV3MissionConfig,
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
