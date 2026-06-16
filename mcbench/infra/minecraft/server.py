"""Readiness probe for a slot's Paper server.

``ServerConfig`` (how to reach a server) lives in :mod:`mcbench.core.slot`; this
module just waits until the server starts answering RCON.
"""

from __future__ import annotations

import time

from mcbench.core.slot import ServerConfig
from mcbench.infra.minecraft.rcon import rcon_session


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
