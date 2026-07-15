from __future__ import annotations

import socket
import ssl
import time
from contextlib import contextmanager
from typing import Iterator

from mcrcon import MCRcon, MCRconException


DEFAULT_SOCKET_TIMEOUT = 20.0
SAFE_COMMAND_ATTEMPTS = 3


class _ThreadSafeMCRcon(MCRcon):
    def __init__(self, host, password, port=25575, tlsmode=0, timeout=5):
        self.host = host
        self.password = password
        self.port = port
        self.tlsmode = tlsmode
        self.timeout = timeout
        self.socket = None

    def connect(self):
        self.socket = socket.create_connection(
            (self.host, self.port),
            timeout=self.timeout,
        )

        if self.tlsmode > 0:
            ctx = ssl.create_default_context()
            if self.tlsmode > 1:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            self.socket = ctx.wrap_socket(self.socket, server_hostname=self.host)
            self.socket.settimeout(self.timeout)

        self._send(3, self.password)

    def _read(self, length):
        data = b""
        while len(data) < length:
            try:
                chunk = self.socket.recv(length - len(data))
            except socket.timeout as e:
                raise MCRconException("Connection timeout error") from e
            if not chunk:
                raise MCRconException("Connection closed")
            data += chunk
        return data


@contextmanager
def rcon_session(
    host: str = "127.0.0.1",
    port: int = 25575,
    password: str = "npabench",
    connect_timeout: float = 60.0,
    socket_timeout: float = DEFAULT_SOCKET_TIMEOUT,
) -> Iterator[MCRcon]:
    deadline = time.monotonic() + connect_timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        rcon = _ThreadSafeMCRcon(host, password, port=port, timeout=int(socket_timeout))
        try:
            rcon.connect()
        except (ConnectionRefusedError, OSError, MCRconException) as e:
            last_err = e
            time.sleep(1.0)
            continue
        try:
            yield rcon
        finally:
            try:
                rcon.disconnect()
            except Exception:
                pass
        return
    raise TimeoutError(
        f"RCON not reachable on {host}:{port} after {connect_timeout}s (last: {last_err})"
    ) from last_err


def command_with_retry(
    rcon: MCRcon,
    command: str,
    *,
    attempts: int = SAFE_COMMAND_ATTEMPTS,
    retry_delay: float = 1.0,
) -> str:
    """Run an idempotent RCON command, reconnecting after transient failures.

    Only use this for commands that are safe to execute more than once.  A
    timeout can happen after Minecraft has already applied a command, so this
    helper must not be used for non-idempotent commands such as ``give``.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    last_error: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            try:
                rcon.disconnect()
            except Exception:
                pass
            time.sleep(retry_delay)
            try:
                rcon.connect()
            except (MCRconException, OSError) as exc:
                last_error = exc
                continue
        try:
            return rcon.command(command)
        except (MCRconException, OSError) as exc:
            last_error = exc

    assert last_error is not None
    raise last_error
