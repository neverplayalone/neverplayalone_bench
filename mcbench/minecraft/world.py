"""World setup over RCON: gamerules, spawn selection, and the competition kit."""

from __future__ import annotations

import json
import math
import time

from mcrcon import MCRcon
from rich.console import Console

from ..models.competition import KitItem, ResourceCompetitionConfig
from .commands import _block_matches, _parse_pos
from .rcon import rcon_session
from .server import ServerConfig

console = Console()

SPAWN_SEARCH_RADIUS = 16
SPAWN_SEARCH_UP = 4
SPAWN_SEARCH_DOWN = 8
SPAWN_SEARCH_MAX_CANDIDATES = 256
# Each candidate costs several RCON round-trips, so an exhaustive search can run
# tens of seconds and delay the kit past the agent's spawn-wait window. Cap the
# wall-clock spend; on timeout we fall back to Minecraft's spawn.
SPAWN_SEARCH_TIME_BUDGET = 6.0
AIR_BLOCKS = ("minecraft:air", "minecraft:cave_air", "minecraft:void_air")
SAFE_SPAWN_GROUND_BLOCKS = (
    "minecraft:grass_block",
    "minecraft:dirt",
    "minecraft:coarse_dirt",
    "minecraft:podzol",
    "minecraft:stone",
    "minecraft:deepslate",
    "minecraft:sand",
    "minecraft:red_sand",
    "minecraft:gravel",
    "minecraft:snow_block",
)
BAD_SPAWN_BLOCKS = (
    "minecraft:water",
    "minecraft:lava",
    "minecraft:powder_snow",
    "minecraft:fire",
    "minecraft:soul_fire",
    "minecraft:cactus",
)


def _configure_world_start(server: ServerConfig, cfg: ResourceCompetitionConfig) -> None:
    with rcon_session(server.host, server.rcon_port, server.rcon_password) as mcr:
        mcr.command("gamerule keep_inventory false")
        mcr.command("gamerule advance_time true")
        mcr.command("gamerule advance_weather true")
        # Determinism across slots: mobs spawn from per-container RNG that a shared
        # world template cannot pin down. With peaceful difficulty plus no mob
        # spawning, every slot sees the same empty world.
        mcr.command("gamerule doMobSpawning false")
        mcr.command(f"difficulty {cfg.difficulty}")
        mcr.command(f"time set {cfg.spawn_time}")
        # Bound the playable arena to a square of side cfg.world_size centered on
        # spawn (world spawn is near 0,0; per-slot spawn is chosen within a few
        # blocks of it, so this is effectively centered on the agent).
        mcr.command("worldborder center 0 0")
        mcr.command(f"worldborder set {cfg.world_size}")


def _prepare_playable_spawn(
    mcr: MCRcon,
    username: str,
) -> tuple[int, int, int]:
    pos = _parse_pos(mcr.command(f"data get entity {username} Pos"))
    if pos is None:
        raise RuntimeError(f"could not read spawn position for {username}")
    origin = (math.floor(pos[0]), math.floor(pos[1]), math.floor(pos[2]))
    spawn_pos = origin
    if not _is_playable_spawn(mcr, *spawn_pos):
        deadline = time.monotonic() + SPAWN_SEARCH_TIME_BUDGET
        for candidate in _nearby_spawn_candidates(*origin):
            if time.monotonic() > deadline:
                console.log(
                    "[yellow]Spawn search time budget exceeded; using Minecraft's spawn.[/]"
                )
                break
            if _is_playable_spawn(mcr, *candidate):
                spawn_pos = candidate
                break
        else:
            console.log(
                "[yellow]Could not find a better local spawn; using Minecraft's spawn.[/]"
            )
    _set_exact_player_spawn(mcr, username, spawn_pos)
    return spawn_pos


def _set_exact_player_spawn(
    mcr: MCRcon,
    username: str,
    spawn_pos: tuple[int, int, int],
) -> None:
    x, y, z = spawn_pos
    mcr.command("gamerule spawnRadius 0")
    mcr.command(f"setworldspawn {x} {y} {z}")
    mcr.command(f"spawnpoint {username} {x} {y} {z}")
    mcr.command(f"tp {username} {x + 0.5} {y} {z + 0.5} 0 0")


def _nearby_spawn_candidates(x: int, y: int, z: int) -> list[tuple[int, int, int]]:
    candidates: list[tuple[int, int, int]] = []
    for dx in range(-SPAWN_SEARCH_RADIUS, SPAWN_SEARCH_RADIUS + 1):
        for dz in range(-SPAWN_SEARCH_RADIUS, SPAWN_SEARCH_RADIUS + 1):
            for cy in range(y + SPAWN_SEARCH_UP, y - SPAWN_SEARCH_DOWN - 1, -1):
                candidates.append((x + dx, cy, z + dz))
    candidates.sort(
        key=lambda pos: (
            (pos[0] - x) ** 2 + (pos[2] - z) ** 2,
            abs(pos[1] - y),
            -pos[1],
        )
    )
    return candidates[:SPAWN_SEARCH_MAX_CANDIDATES]


def _is_playable_spawn(mcr: MCRcon, x: int, y: int, z: int) -> bool:
    return (
        _is_air(mcr, x, y, z)
        and _is_air(mcr, x, y + 1, z)
        and _is_safe_spawn_ground(mcr, x, y - 1, z)
        and not _has_bad_spawn_block_nearby(mcr, x, y, z)
    )


def _is_air(mcr: MCRcon, x: int, y: int, z: int) -> bool:
    return any(_block_matches(mcr, x, y, z, block) for block in AIR_BLOCKS)


def _is_safe_spawn_ground(mcr: MCRcon, x: int, y: int, z: int) -> bool:
    return any(
        _block_matches(mcr, x, y, z, block)
        for block in SAFE_SPAWN_GROUND_BLOCKS
    )


def _has_bad_spawn_block_nearby(mcr: MCRcon, x: int, y: int, z: int) -> bool:
    positions = (
        (x, y, z),
        (x, y - 1, z),
        (x + 1, y, z),
        (x - 1, y, z),
        (x, y, z + 1),
        (x, y, z - 1),
    )
    return any(
        _block_matches(mcr, px, py, pz, block)
        for px, py, pz in positions
        for block in BAD_SPAWN_BLOCKS
    )


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


