"""Thin RCON helper. Wraps mcrcon with retries and a context manager."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from mcrcon import MCRcon, MCRconException


@contextmanager
def rcon_session(
    host: str = "127.0.0.1",
    port: int = 25575,
    password: str = "mcbench",
    connect_timeout: float = 60.0,
    socket_timeout: float = 5.0,
) -> Iterator[MCRcon]:
    """Connect to RCON, retrying until the server accepts and authenticates.

    During server boot, the RCON port may be open before the listener is fully
    ready, causing mcrcon to raise MCRconException("Connection timeout error")
    mid-handshake. We retry that too, not just OSError.
    """
    deadline = time.monotonic() + connect_timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        mcr = MCRcon(host, password, port=port, timeout=int(socket_timeout))
        try:
            mcr.connect()
        except (ConnectionRefusedError, OSError, MCRconException) as e:
            last_err = e
            time.sleep(1.0)
            continue
        try:
            yield mcr
        finally:
            try:
                mcr.disconnect()
            except Exception:
                pass
        return
    raise TimeoutError(
        f"RCON not reachable on {host}:{port} after {connect_timeout}s (last: {last_err})"
    ) from last_err


def run_commands(mcr: MCRcon, commands: list[str]) -> list[str]:
    """Run a batch of commands and return their responses."""
    out: list[str] = []
    for cmd in commands:
        cmd = cmd.lstrip("/")
        out.append(mcr.command(cmd))
    return out
