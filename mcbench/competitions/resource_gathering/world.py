"""Resource-gathering world rules: configure the world, set up the competitor, capture.

These are the competition-specific RCON interactions. Generic primitives (spawn
search, RCON parsers) come from ``mcbench.minecraft``.
"""

from __future__ import annotations

import json
from typing import Any

from mcrcon import MCRcon

from ...core.competition import KitItem
from ...core.trace import FinalState
from ...minecraft.commands import _count_item, _parse_pos, _parse_scalar, _read_score
from ...minecraft.world import _prepare_playable_spawn
from .config import ResourceCompetitionConfig
from .scoring import _counted_items, _horizontal_distance_from_spawn


def configure_world(mcr: MCRcon, cfg: ResourceCompetitionConfig) -> None:
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


def setup_competitor(
    mcr: MCRcon, cfg: ResourceCompetitionConfig
) -> tuple[int, tuple[int, int, int]]:
    mcr.command(f"op {cfg.username}")
    mcr.command(f"clear {cfg.username}")
    mcr.command("kill @e[type=item]")
    mcr.command("scoreboard objectives remove mcb_deaths")
    mcr.command("scoreboard objectives add mcb_deaths minecraft.custom:minecraft.deaths")
    death_baseline = _read_score(mcr, cfg.username, "mcb_deaths")
    spawn_pos = _prepare_playable_spawn(mcr, cfg.username)
    for kit in cfg.kit:
        _give_kit_item(mcr, cfg.username, kit)
    mcr.command(f"gamemode survival {cfg.username}")
    mcr.command(f"effect give {cfg.username} minecraft:saturation 3 10 true")
    mcr.command(f"deop {cfg.username}")
    return death_baseline, spawn_pos


def capture(
    mcr: MCRcon,
    cfg: ResourceCompetitionConfig,
    setup_state: tuple[int, tuple[int, int, int] | None] | None,
) -> dict[str, Any]:
    death_baseline, spawn_pos = setup_state if setup_state else (0, None)
    state = FinalState()
    state.position = _parse_pos(mcr.command(f"data get entity {cfg.username} Pos"))
    state.health = _parse_scalar(mcr.command(f"data get entity {cfg.username} Health"))
    state.food = _parse_scalar(mcr.command(f"data get entity {cfg.username} foodLevel"))
    for resource in cfg.resources:
        total = 0
        for item in _counted_items(resource):
            count = _count_item(mcr, cfg.username, item)
            state.inventory[item] = count
            total += count
        state.inventory[resource.item] = total
    deaths = max(0, _read_score(mcr, cfg.username, "mcb_deaths") - death_baseline)
    distance_from_spawn = _horizontal_distance_from_spawn(state.position, spawn_pos)
    return {
        "final_state": state,
        "deaths": deaths,
        "alive": state.health is not None and state.health > 0,
        "spawn": {"position": spawn_pos},
        "distance_from_spawn": distance_from_spawn,
    }


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
