from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from mcrcon import MCRcon
from pydantic import BaseModel, Field, field_validator

from npabench.config import DEFAULT_AGENT_USERNAME, DEFAULT_MINECRAFT_VERSION
from npabench.evaluation.run_trace import AgentRunTrace

if TYPE_CHECKING:
    from npabench.evaluation.run_slot import ServerEndpoint


class MissionRuntime(Protocol):
    """Optional benchmark-owned work that runs while an agent is connected."""

    def stop(self) -> dict[str, Any]: ...


class StartingItem(BaseModel):
    item: str
    count: int = 1
    enchantments: list[str] = Field(default_factory=list)
    slot: str | None = None

    @field_validator("count")
    @classmethod
    def count_must_be_positive(cls, input_value: int) -> int:
        if input_value < 1:
            raise ValueError("starting item count must be positive")
        return input_value


class MissionConfig(BaseModel):
    id: str = "mission"
    seed: int = 0
    minecraft_version: str = DEFAULT_MINECRAFT_VERSION
    world_type: str = "normal"
    biome: str | None = None
    world_size: int = 5000
    generate_structures: bool = True
    difficulty: Literal["peaceful", "easy", "normal", "hard"] = "peaceful"
    memory: str = "2G"
    duration_seconds: int = 120
    spawn_time: int = 0
    username: str = DEFAULT_AGENT_USERNAME
    prompt: str = ""

    @field_validator("duration_seconds")
    @classmethod
    def duration_must_be_positive(cls, input_value: int) -> int:
        if input_value <= 0:
            raise ValueError("duration_seconds must be positive")
        return input_value


class PromptMetadata(BaseModel):
    provider: str
    model: str
    schema_version: str


class TaskTarget(BaseModel):
    key: str
    display_name: str
    items: list[str] = Field(default_factory=list)
    target_count: int
    role: Literal["essential", "optional"] = "optional"
    points: float = 0.0

    @field_validator("target_count")
    @classmethod
    def target_count_must_be_positive(cls, input_value: int) -> int:
        if input_value <= 0:
            raise ValueError("target_count must be positive")
        return input_value


class Task(BaseModel):
    task_id: str
    seed: int
    minecraft_seed: int
    prompt: str = ""
    targets: list[TaskTarget] = Field(default_factory=list)
    prompt_metadata: PromptMetadata | None = None

    def to_mission_config(self, base_config: MissionConfig) -> MissionConfig:
        data = base_config.model_dump()
        data.update(
            {
                "id": self.task_id,
                "seed": self.minecraft_seed,
                "prompt": self.prompt,
            }
        )
        return MissionConfig.model_validate(data)


class Mission(ABC):
    id: str

    @abstractmethod
    def default_config_path(self) -> Path: ...

    @abstractmethod
    def load_config(self, path: str | Path) -> MissionConfig: ...

    @abstractmethod
    def generate_task(
        self,
        base_config: MissionConfig,
        seed: int,
        task_id: str | None = None,
    ) -> Task: ...

    def build_mission_config(
        self,
        base_config: MissionConfig,
        task: Task,
    ) -> MissionConfig:
        return task.to_mission_config(base_config)

    def materialize_task(
        self,
        base_config: MissionConfig,
        task: Task,
        output_dir: Path,
    ) -> Task:
        return task

    @abstractmethod
    def configure_world(self, rcon: MCRcon, mission_config: MissionConfig) -> None: ...

    @abstractmethod
    def setup_agent(self, rcon: MCRcon, mission_config: MissionConfig) -> Any: ...

    def start_runtime(
        self,
        server_endpoint: ServerEndpoint,
        mission_config: MissionConfig,
        setup_state: Any,
    ) -> MissionRuntime | None:
        """Start optional timed mission behavior after the agent emits ``ready``."""
        return None

    @abstractmethod
    def prompt_text(self, mission_config: MissionConfig) -> str: ...

    @abstractmethod
    def collect_final_state(
        self,
        rcon: MCRcon,
        mission_config: MissionConfig,
        setup_state: Any,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def score(
        self,
        mission_config: MissionConfig,
        agent_run_trace: AgentRunTrace,
        final_snapshot: dict[str, Any],
    ) -> dict[str, Any]: ...
