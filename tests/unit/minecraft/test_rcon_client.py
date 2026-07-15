from __future__ import annotations

import inspect

from mcrcon import MCRconException

from npabench.minecraft.rcon_client import (
    DEFAULT_SOCKET_TIMEOUT,
    command_with_retry,
    rcon_session,
)


class _FlakyRcon:
    def __init__(self) -> None:
        self.command_calls = 0
        self.connect_calls = 0
        self.disconnect_calls = 0

    def command(self, command: str) -> str:
        self.command_calls += 1
        if self.command_calls == 1:
            raise MCRconException("Connection timeout error")
        return f"ran: {command}"

    def connect(self) -> None:
        self.connect_calls += 1

    def disconnect(self) -> None:
        self.disconnect_calls += 1


def test_command_with_retry_reconnects_after_timeout(monkeypatch) -> None:
    rcon = _FlakyRcon()
    monkeypatch.setattr("npabench.minecraft.rcon_client.time.sleep", lambda _: None)

    response = command_with_retry(rcon, "clear npabench_agent", attempts=3)

    assert response == "ran: clear npabench_agent"
    assert rcon.command_calls == 2
    assert rcon.disconnect_calls == 1
    assert rcon.connect_calls == 1


def test_rcon_session_uses_longer_default_command_timeout() -> None:
    default_timeout = inspect.signature(rcon_session).parameters["socket_timeout"].default

    assert default_timeout == DEFAULT_SOCKET_TIMEOUT == 20.0
