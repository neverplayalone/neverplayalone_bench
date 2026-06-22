from __future__ import annotations

import time

from mcbench.evaluation.run_slot import ServerEndpoint
from mcbench.minecraft.rcon_client import rcon_session


def wait_for_ready(server_endpoint: ServerEndpoint, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with rcon_session(
                server_endpoint.host,
                server_endpoint.rcon_port,
                server_endpoint.rcon_password,
                connect_timeout=5,
            ) as rcon:
                rcon.command("list")
                return
        except (TimeoutError, ConnectionError, OSError):
            time.sleep(2.0)
    raise TimeoutError(f"Server not ready within {timeout}s")
