"""Server config + readiness probe for a slot's Paper container.

The resource-gathering path owns its container lifecycle (see
``mcbench.container``); this module only holds the RCON-facing ``ServerConfig``
and a readiness probe.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .rcon import rcon_session


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    rcon_port: int = 25575
    rcon_password: str = "mcbench"
    game_port: int = 25565


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
