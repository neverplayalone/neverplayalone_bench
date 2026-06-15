"""Thin RCON helper. Wraps mcrcon with retries and a context manager."""

from __future__ import annotations

import time
from contextlib import contextmanager
import socket
import ssl
from typing import Iterator

from mcrcon import MCRcon, MCRconException


class _ThreadSafeMCRcon(MCRcon):
    """MCRcon variant that avoids SIGALRM so it can run inside worker threads."""

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
        mcr = _ThreadSafeMCRcon(host, password, port=port, timeout=int(socket_timeout))
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
