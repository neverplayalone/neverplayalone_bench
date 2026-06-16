"""Slot: ports, container name, and data dir for one isolated run."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path

from mcbench.paths import RESULTS_DIR


@dataclass
class ServerConfig:
    """How to reach a slot's Paper server over the network (RCON + game port)."""

    host: str = "127.0.0.1"
    rcon_port: int = 25575
    rcon_password: str = "mcbench"
    game_port: int = 25565


def _random_rcon_password() -> str:
    """A fresh, unguessable RCON password per slot.

    RCON is the score oracle: it both configures the world (op/give/gamemode) and
    reads the final inventory/position used for scoring. A static shared password
    means anything that can reach the RCON port can fabricate a perfect score, so
    each slot gets its own random secret that is never exposed to the agent.
    """
    return secrets.token_hex(24)


@dataclass(frozen=True)
class Slot:
    """One isolated evaluation slot.

    Parallelism later is just multiple slots with different ids/ports/data dirs.
    """

    slot_id: int = 0
    host: str = "127.0.0.1"
    base_game_port: int = 25565
    base_rcon_port: int = 25575
    rcon_password: str = field(default_factory=_random_rcon_password)
    container_prefix: str = "mcbench-resource"
    data_root: Path = RESULTS_DIR / "slots"

    @property
    def game_port(self) -> int:
        return self.base_game_port + self.slot_id

    @property
    def rcon_port(self) -> int:
        return self.base_rcon_port + self.slot_id

    @property
    def container_name(self) -> str:
        return f"{self.container_prefix}-{self.slot_id}"

    @property
    def network_name(self) -> str:
        return f"{self.container_name}-net"

    @property
    def data_dir(self) -> Path:
        return self.data_root / f"slot-{self.slot_id}" / "data"

    def server_config(self) -> ServerConfig:
        return ServerConfig(
            host=self.host,
            game_port=self.game_port,
            rcon_port=self.rcon_port,
            rcon_password=self.rcon_password,
        )

