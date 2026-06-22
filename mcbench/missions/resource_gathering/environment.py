from __future__ import annotations

import json

from mcrcon import MCRcon

from mcbench.minecraft.rcon_helpers import read_score
from mcbench.minecraft.spawn import use_world_spawn
from mcbench.missions.base import StartingItem
from mcbench.missions.resource_gathering.config_schema import ResourceGatheringMissionConfig


def configure_resource_gathering_world(
    rcon: MCRcon,
    mission_config: ResourceGatheringMissionConfig,
) -> None:
    rcon.command("gamerule keep_inventory false")
    rcon.command("gamerule advance_time true")
    rcon.command("gamerule advance_weather true")
    rcon.command("gamerule doMobSpawning false")
    rcon.command(f"difficulty {mission_config.difficulty}")
    rcon.command(f"time set {mission_config.spawn_time}")
    rcon.command("worldborder center 0 0")
    rcon.command(f"worldborder set {mission_config.world_size}")


def setup_resource_gathering_agent(
    rcon: MCRcon,
    mission_config: ResourceGatheringMissionConfig,
) -> tuple[int, tuple[int, int, int]]:
    rcon.command(f"op {mission_config.username}")
    rcon.command(f"clear {mission_config.username}")
    rcon.command("kill @e[type=item]")
    rcon.command("scoreboard objectives remove mcb_deaths")
    rcon.command("scoreboard objectives add mcb_deaths minecraft.custom:minecraft.deaths")
    death_baseline = read_score(rcon, mission_config.username, "mcb_deaths")
    spawn_pos = use_world_spawn(rcon, mission_config.username)
    for starting_item in mission_config.starting_items:
        give_starting_item(rcon, mission_config.username, starting_item)
    rcon.command(f"gamemode survival {mission_config.username}")
    rcon.command(f"effect give {mission_config.username} minecraft:saturation 3 10 true")
    rcon.command(f"deop {mission_config.username}")
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
