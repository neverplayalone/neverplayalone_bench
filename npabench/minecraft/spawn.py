from __future__ import annotations

from mcrcon import MCRcon

from npabench.minecraft.rcon_client import command_with_retry
from npabench.minecraft.rcon_helpers import parse_pos


def set_exact_spawn(
    rcon: MCRcon,
    username: str,
    x: int,
    y: int,
    z: int,
) -> tuple[int, int, int]:
    # Minecraft 1.21.11 exposes gamerules as snake_case and renamed the old
    # spawnRadius rule to respawn_radius.  The old spelling returns an error
    # string over RCON rather than raising, so it could silently leave the
    # vanilla random radius enabled.
    command_with_retry(rcon, "gamerule respawn_radius 0", require_success=True)
    command_with_retry(rcon, f"setworldspawn {x} {y} {z}", require_success=True)
    command_with_retry(rcon, f"spawnpoint {username} {x} {y} {z}", require_success=True)
    command_with_retry(
        rcon,
        f"tp {username} {x + 0.5} {y} {z + 0.5} 0 0",
        require_success=True,
    )
    return (x, y, z)


def use_world_spawn(rcon: MCRcon, username: str) -> tuple[int, int, int]:
    pos = parse_pos(command_with_retry(rcon, f"data get entity {username} Pos"))
    if pos is None:
        raise RuntimeError("could not read player position")
    return set_exact_spawn(rcon, username, int(pos[0]), int(pos[1]), int(pos[2]))
