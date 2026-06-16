"""Playable-spawn search and setup over RCON.

Task-agnostic helpers. World rules (gamerules, mobs, kit) live with each task.
"""

from __future__ import annotations

import math
import time

from mcrcon import MCRcon
from rich.console import Console

from mcbench.infra.minecraft.commands import _block_matches, _parse_pos

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


def prepare_playable_spawn(
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
