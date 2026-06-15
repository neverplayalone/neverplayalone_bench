"""Resource-gathering environment setup over RCON."""

from __future__ import annotations

import json

from mcrcon import MCRcon

from mcbench.core.base_task import KitItem
from mcbench.minecraft.commands import _read_score
from mcbench.minecraft.spawn import prepare_playable_spawn
from mcbench.tasks.resource_gathering.config_schema import ResourceGatheringTaskConfig


def configure_world(mcr: MCRcon, cfg: ResourceGatheringTaskConfig) -> None:
    mcr.command("gamerule keep_inventory false")
    mcr.command("gamerule advance_time true")
    mcr.command("gamerule advance_weather true")
    # Determinism across slots: mobs spawn from per-container RNG that a shared
    # world template cannot pin down. Peaceful + no mob spawning => identical worlds.
    mcr.command("gamerule doMobSpawning false")
    mcr.command(f"difficulty {cfg.difficulty}")
    mcr.command(f"time set {cfg.spawn_time}")
    # Bound the playable arena to a square of side cfg.world_size centered on spawn.
    mcr.command("worldborder center 0 0")
    mcr.command(f"worldborder set {cfg.world_size}")


def setup_agent(
    mcr: MCRcon, cfg: ResourceGatheringTaskConfig
) -> tuple[int, tuple[int, int, int]]:
    mcr.command(f"op {cfg.username}")
    mcr.command(f"clear {cfg.username}")
    mcr.command("kill @e[type=item]")
    mcr.command("scoreboard objectives remove mcb_deaths")
    mcr.command("scoreboard objectives add mcb_deaths minecraft.custom:minecraft.deaths")
    death_baseline = _read_score(mcr, cfg.username, "mcb_deaths")
    spawn_pos = prepare_playable_spawn(mcr, cfg.username)
    for kit in cfg.kit:
        _give_kit_item(mcr, cfg.username, kit)
    mcr.command(f"gamemode survival {cfg.username}")
    mcr.command(f"effect give {cfg.username} minecraft:saturation 3 10 true")
    mcr.command(f"deop {cfg.username}")
    return death_baseline, spawn_pos


def _give_kit_item(mcr: MCRcon, username: str, kit: KitItem) -> None:
    item = _kit_item_stack(kit)
    if kit.slot:
        mcr.command(f"item replace entity {username} {kit.slot} with {item} {kit.count}")
    else:
        mcr.command(f"give {username} {item} {kit.count}")


def _kit_item_stack(kit: KitItem) -> str:
    item = f"minecraft:{kit.item}"
    if not kit.enchantments:
        return item
    levels = {f"minecraft:{name}": level for name, level in _enchants(kit)}
    enchantments = json.dumps(levels, separators=(",", ":"))
    return f"{item}[minecraft:enchantments={enchantments}]"


def _enchants(kit: KitItem) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for raw in kit.enchantments:
        name, _, level_raw = raw.partition(":")
        level = int(level_raw or "1")
        out.append((name, level))
    return out
