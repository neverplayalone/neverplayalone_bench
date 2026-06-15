"""Base task types and shared run configuration.

The engine in :mod:`mcbench.core` knows nothing about any specific task
(logs, crafting, hunting, ...). It drives whatever ``Task`` it is given
through the hooks below. Each task lives under
:mod:`mcbench.tasks` and supplies its own config schema, instance
generation, world setup, capture, and scoring.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

from mcrcon import MCRcon
from pydantic import BaseModel, Field, field_validator

USERNAME = "BenchmarkBot"


class KitItem(BaseModel):
    """One item handed to the agent at setup. Shared across tasks."""

    item: str
    count: int = 1
    enchantments: list[str] = Field(default_factory=list)
    slot: str | None = None

    @field_validator("count")
    @classmethod
    def count_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("kit item count must be positive")
        return value


class RunConfig(BaseModel):
    """Infra + world settings the engine needs, shared by every task.

    Per-task configs subclass this to add their own fields (kit, scoring,
    targets, ...). The engine only ever reads the fields defined here.
    """

    id: str = "task"
    seed: int = 0
    minecraft_version: str = "1.21.11"
    world_type: str = "normal"
    # When set (e.g. "minecraft:forest"), the world is pinned to this single biome
    # via a generated world-preset datapack; None => normal multi-biome generation.
    biome: str | None = None
    # Side length (blocks) of the square worldborder centered on spawn.
    world_size: int = 50000
    generate_structures: bool = True
    difficulty: Literal["peaceful", "easy", "normal", "hard"] = "peaceful"
    memory: str = "2G"
    duration_seconds: int = 1200
    spawn_time: int = 0
    username: str = USERNAME
    goal: str = ""

    @field_validator("duration_seconds")
    @classmethod
    def duration_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("duration_seconds must be positive")
        return value


class Task(ABC):
    """A task type. The engine calls these hooks; everything Minecraft-,
    Docker-, agent-, and recording-related is shared and never overridden."""

    id: str

    @abstractmethod
    def default_config_path(self) -> Path:
        """Bundled default config (configs/default.yaml) — settings + task catalog."""

    @abstractmethod
    def load_config(self, path: str | Path) -> RunConfig:
        """Parse the config YAML into this task's RunConfig subclass."""

    @abstractmethod
    def generate_instance(
        self,
        base_cfg: RunConfig,
        seed: int,
        instance_id: str | None = None,
    ) -> Any:
        """Deterministically derive the frozen instance from (config, seed).

        The returned object must expose ``instance_id`` (str), ``world_seed``
        (int), ``model_dump()``, and ``to_run_config(base_cfg) -> RunConfig``.
        """

    @abstractmethod
    def configure_world(self, mcr: MCRcon, cfg: RunConfig) -> None:
        """Gamerules / difficulty / mob policy / time / worldborder. Runs at the
        template build and on every slot."""

    @abstractmethod
    def setup_agent(self, mcr: MCRcon, cfg: RunConfig) -> Any:
        """Apply kit, pin spawn, record baselines. Returns task-defined
        setup state passed back to :meth:`capture`."""

    @abstractmethod
    def goal_text(self, cfg: RunConfig) -> str:
        """The natural-language goal handed to the agent."""

    @abstractmethod
    def capture(self, mcr: MCRcon, cfg: RunConfig, setup_state: Any) -> dict[str, Any]:
        """Read end-of-run world state. Must include a ``"final_state"`` key
        (a :class:`mcbench.core.trace.FinalState`)."""

    @abstractmethod
    def score(self, cfg: RunConfig, trace: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Compute the score report from the trace + captured snapshot."""
