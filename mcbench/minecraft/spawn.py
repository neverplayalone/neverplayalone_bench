from __future__ import annotations

from mcrcon import MCRcon

from mcbench.minecraft.rcon_helpers import parse_pos


def set_exact_spawn(
    rcon: MCRcon,
    username: str,
    x: int,
    y: int,
    z: int,
) -> tuple[int, int, int]:
    rcon.command("gamerule spawnRadius 0")
    rcon.command(f"setworldspawn {x} {y} {z}")
    rcon.command(f"spawnpoint {username} {x} {y} {z}")
    rcon.command(f"tp {username} {x + 0.5} {y} {z + 0.5} 0 0")
    return (x, y, z)


def use_world_spawn(rcon: MCRcon, username: str) -> tuple[int, int, int]:
    pos = parse_pos(rcon.command(f"data get entity {username} Pos"))
    if pos is None:
        raise RuntimeError("could not read player position")
    return set_exact_spawn(rcon, username, int(pos[0]), int(pos[1]), int(pos[2]))
