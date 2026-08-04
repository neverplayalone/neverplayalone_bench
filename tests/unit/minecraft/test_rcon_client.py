from __future__ import annotations

import inspect

from mcrcon import MCRconException
import pytest

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


class _RejectedRcon:
    def command(self, command: str) -> str:
        return f"Incorrect argument for command\n{command}<--[HERE]"


def test_strict_rcon_command_rejects_brigadier_error_response() -> None:
    with pytest.raises(RuntimeError, match="Minecraft rejected RCON command"):
        command_with_retry(
            _RejectedRcon(),
            "gamerule spawnRadius 0",
            require_success=True,
        )


def test_non_strict_rcon_command_preserves_expected_error_responses() -> None:
    response = command_with_retry(_RejectedRcon(), "scoreboard objectives remove missing")

    assert response.startswith("Incorrect argument for command")
