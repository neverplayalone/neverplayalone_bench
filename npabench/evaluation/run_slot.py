from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path

from npabench.config import DEFAULT_BASE_GAME_PORT, DEFAULT_BASE_RCON_PORT, RESULTS_DIR


def random_rcon_password() -> str:
    return secrets.token_hex(24)


@dataclass(frozen=True)
class ServerEndpoint:
    host: str = "127.0.0.1"
    rcon_port: int = DEFAULT_BASE_RCON_PORT
    rcon_password: str = "npabench"
    game_port: int = DEFAULT_BASE_GAME_PORT


@dataclass(frozen=True)
class AgentRunSlot:
    slot_id: int = 0
    host: str = "127.0.0.1"
    base_game_port: int = DEFAULT_BASE_GAME_PORT
    base_rcon_port: int = DEFAULT_BASE_RCON_PORT
    rcon_password: str = field(default_factory=random_rcon_password)
    container_prefix: str = "npabench-eval"
    data_root: Path = RESULTS_DIR / "runs"
    # Extra containers (e.g. a subnet egress proxy) to attach to this slot's
    # dedicated internal network so the sandboxed agent can reach them.
    sidecar_containers: tuple[str, ...] = ()

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

    def server_endpoint(self) -> ServerEndpoint:
        return ServerEndpoint(
            host=self.host,
            game_port=self.game_port,
            rcon_port=self.rcon_port,
            rcon_password=self.rcon_password,
        )

    @classmethod
    def allocate(
        cls,
        *,
        slot_id: int = 0,
        base_game_port: int = DEFAULT_BASE_GAME_PORT,
        base_rcon_port: int = DEFAULT_BASE_RCON_PORT,
        data_root: Path | None = None,
        container_prefix: str = "npabench-eval",
        sidecar_containers: tuple[str, ...] = (),
    ) -> "AgentRunSlot":
        return cls(
            slot_id=slot_id,
            base_game_port=base_game_port,
            base_rcon_port=base_rcon_port,
            data_root=data_root or (RESULTS_DIR / "runs"),
            container_prefix=container_prefix,
            sidecar_containers=sidecar_containers,
        )
