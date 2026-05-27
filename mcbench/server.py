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


def reset_world() -> None:
    """Wipe the world dir so the next `up` regenerates a fresh world.

    The server must be stopped first.
    """
    down()
    for sub in ("world", "world_nether", "world_the_end"):
        target = DATA_DIR / sub
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


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
