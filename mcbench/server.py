"""Server lifecycle: bring a Paper server up/down via docker compose, reset world between runs."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .rcon import rcon_session

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKER_DIR = REPO_ROOT / "docker"
DATA_DIR = DOCKER_DIR / "data"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    rcon_port: int = 25575
    rcon_password: str = "mcbench"
    game_port: int = 25565


def _compose(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke `docker compose ...`. On failure, surface stderr instead of swallowing it."""
    result = subprocess.run(
        ["docker", "compose", "-f", str(DOCKER_DIR / "docker-compose.yml"), *args],
        cwd=DOCKER_DIR,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        hint = ""
        combined = f"{result.stderr}\n{result.stdout}".lower()
        if "permission denied" in combined and "docker.sock" in combined:
            hint = (
                "\nDocker is installed, but this user cannot access "
                "/var/run/docker.sock. Add the user to the docker group and "
                "start a new login shell, or run the benchmark from a shell "
                "with Docker access."
            )
        raise RuntimeError(
            f"docker compose {' '.join(args)} failed (exit {result.returncode})\n"
            f"--- stderr ---\n{result.stderr}\n"
            f"--- stdout ---\n{result.stdout}"
            f"{hint}"
        )
    return result


def up(wait: bool = True, cfg: ServerConfig | None = None, ready_timeout: float = 600.0) -> None:
    """Bring the server up. If wait=True, block until RCON answers.

    Idempotent: if a healthy `mcbench-server` is already running and answering
    RCON, returns immediately without touching compose. This avoids name/port
    conflicts when `up` is called twice.
    """
    cfg = cfg or ServerConfig()
    if _already_running_and_healthy(cfg):
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _compose("up", "-d")
    if not wait:
        return
    with rcon_session(
        cfg.host, cfg.rcon_port, cfg.rcon_password, connect_timeout=ready_timeout
    ) as mcr:
        mcr.command("list")


def _already_running_and_healthy(cfg: ServerConfig) -> bool:
    if not is_running():
        return False
    try:
        with rcon_session(
            cfg.host, cfg.rcon_port, cfg.rcon_password, connect_timeout=3, socket_timeout=2
        ) as mcr:
            mcr.command("list")
        return True
    except Exception:
        return False


def down() -> None:
    _compose("down", "-v")


WORLD_DIRS = ("world", "world_nether", "world_the_end")


def _wipe_world_dirs() -> None:
    for sub in WORLD_DIRS:
        target = DATA_DIR / sub
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


def reset_world() -> None:
    """Wipe the world dir so the next `up` regenerates a fresh world.

    The server must be stopped first.
    """
    down()
    _wipe_world_dirs()


def wipe_player_data() -> None:
    """Delete saved player data (inventory/health/position) so bots rejoin fresh.

    A bot that disconnects while dead (on the death screen) persists Health:0 in
    its offline save; on the next run it loads already-dead, dies before it can
    spawn, and the run silently fails. Wiping playerdata each reset prevents that —
    the server recreates the player at world spawn (alive, full health) on join.
    Safe while the server runs: the bots are offline during reset, so nothing holds
    these files open.

    Statistics (world/stats) are deliberately NOT deleted: the per-episode grading
    reads minecraft.mined/used/killed via scoreboard objectives and measures the
    delta against a baseline captured at setup. Deleting the stats file resets the
    underlying stats but leaves the scoreboard objectives showing stale values,
    desyncing baseline vs. final and zeroing every delta. Letting stats accumulate
    keeps the objectives in sync, so the delta is always this episode's true count.
    """
    directory = DATA_DIR / "world" / "playerdata"
    if not directory.exists():
        return
    for f in directory.glob("*.dat*"):
        try:
            f.unlink()
        except OSError:
            pass


# Block layers of the generated FLAT world (LEVEL_TYPE=FLAT, 1.20.4):
#   y=-64 bedrock, y=-63..-62 dirt, y=-61 grass_block, y>=-60 air.
_FLAT_FLOOR_Y = -64
_FLAT_SURFACE_Y = -61  # topmost solid layer (grass)
# Max blocks a single /fill may affect (vanilla limit is 32768; stay under it).
_FILL_MAX_VOLUME = 32000


def _fill_box(
    mcr,
    p1: tuple[int, int, int],
    p2: tuple[int, int, int],
    block: str,
) -> None:
    """`/fill` a box, splitting into sub-boxes so each command stays under the volume limit."""
    x1, x2 = sorted((p1[0], p2[0]))
    y1, y2 = sorted((p1[1], p2[1]))
    z1, z2 = sorted((p1[2], p2[2]))
    nx, nz = x2 - x1 + 1, z2 - z1 + 1
    layer = nx * nz
    if layer <= _FILL_MAX_VOLUME:
        dy = max(1, _FILL_MAX_VOLUME // layer)
        for y in range(y1, y2 + 1, dy):
            ye = min(y + dy - 1, y2)
            mcr.command(f"fill {x1} {y} {z1} {x2} {ye} {z2} minecraft:{block}")
        return
    # Layer itself too large — split along x and recurse.
    dx = max(1, _FILL_MAX_VOLUME // nz)
    for x in range(x1, x2 + 1, dx):
        xe = min(x + dx - 1, x2)
        _fill_box(mcr, (x, y1, z1), (xe, y2, z2), block)


def clean_world_inplace(
    cfg: ServerConfig | None = None,
    radius: int = 48,
    ceiling: int = 64,
) -> None:
    """Reset the area around spawn to a pristine flat world without restarting.

    Restarting the container costs ~2min here (JVM + Paper bootstrap dominate,
    not downloads), so a full reset between runs is too slow. Instead we clean
    in place over RCON: kill leftover entities/items and restore a bounded box
    around spawn to the flat-world profile. This is near-instant and keeps runs
    reproducible as long as the agent stays within `radius` of spawn (tasks here
    operate within ~16 blocks of spawn, so the default leaves wide margin).

    Also wipes saved player data so a dead bot can't poison later runs (see
    wipe_player_data). The bot for this run hasn't connected yet, so its files
    are safe to delete here.
    """
    cfg = cfg or ServerConfig()
    r = radius
    wipe_player_data()
    with rcon_session(cfg.host, cfg.rcon_port, cfg.rcon_password) as mcr:
        mcr.command(f"forceload add {-r} {-r} {r} {r}")
        try:
            mcr.command("kill @e[type=!minecraft:player]")
            _fill_box(mcr, (-r, _FLAT_SURFACE_Y + 1, -r), (r, ceiling, r), "air")
            _fill_box(mcr, (-r, _FLAT_FLOOR_Y, -r), (r, _FLAT_FLOOR_Y, r), "bedrock")
            _fill_box(mcr, (-r, _FLAT_FLOOR_Y + 1, -r), (r, _FLAT_SURFACE_Y - 1, r), "dirt")
            _fill_box(mcr, (-r, _FLAT_SURFACE_Y, -r), (r, _FLAT_SURFACE_Y, r), "grass_block")
        finally:
            mcr.command("forceload remove all")


def is_running() -> bool:
    try:
        result = _compose("ps", "-q", "mc")
        return bool(result.stdout.strip())
    except RuntimeError as e:
        if "permission denied" in str(e).lower() and "docker.sock" in str(e).lower():
            raise
        return False


def wait_for_ready(cfg: ServerConfig | None = None, timeout: float = 120.0) -> None:
    cfg = cfg or ServerConfig()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with rcon_session(cfg.host, cfg.rcon_port, cfg.rcon_password, connect_timeout=5) as mcr:
                mcr.command("list")
                return
        except (TimeoutError, ConnectionError, OSError):
            time.sleep(2.0)
    raise TimeoutError(f"Server not ready within {timeout}s")
