from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from mcbench.agents.base import Agent, AgentRunContext, AgentSpec
from mcbench.evaluation.run_trace import FinalAgentState, TraceEvent
from mcbench.missions.base import Mission, MissionConfig, Task


class FakeAgent(Agent):
    def __init__(self, events: list[TraceEvent]) -> None:
        super().__init__(AgentSpec(name="fake_agent", path=Path("/tmp/fake-agent")))
        self._events = events
        self.context: AgentRunContext | None = None
        self.stop_called = False

    def run(self, context: AgentRunContext):
        self.context = context
        yield from self._events

    def stop(self) -> None:
        self.stop_called = True


class FakeTask(Task):
    extra: str = "fake"


class FakeMission(Mission):
    id = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def default_config_path(self) -> Path:
        return Path("default.yaml")

    def load_config(self, path: str | Path) -> MissionConfig:
        return MissionConfig()

    def generate_task(
        self,
        base_config: MissionConfig,
        seed: int,
        task_id: str | None = None,
    ) -> Task:
        return FakeTask(
            task_id=task_id or "fake-task",
            seed=seed,
            minecraft_seed=seed,
            prompt="collect one log",
        )

    def configure_world(self, rcon, mission_config: MissionConfig) -> None:
        self.calls.append("configure_world")

    def setup_agent(self, rcon, mission_config: MissionConfig) -> Any:
        self.calls.append("setup_agent")
        return {"started": True}

    def prompt_text(self, mission_config: MissionConfig) -> str:
        return "collect one log"

    def collect_final_state(self, rcon, mission_config: MissionConfig, setup_state: Any) -> dict[str, Any]:
        self.calls.append("collect_final_state")
        return {
            "final_state": FinalAgentState(inventory={"oak_log": 1}, health=20),
            "deaths": 0,
            "alive": True,
        }

    def score(self, mission_config: MissionConfig, agent_run_trace, final_snapshot: dict[str, Any]) -> dict[str, Any]:
        return {"score": 1.0, "max_score": 1.0, "status": "ok"}


@pytest.fixture
def fake_agent() -> FakeAgent:
    return FakeAgent([TraceEvent(kind="ready", data={}), TraceEvent(kind="done", data={})])


@pytest.fixture
def fake_mission() -> FakeMission:
    return FakeMission()


@pytest.fixture
def fake_rcon_session():
    @contextmanager
    def _session(*args, **kwargs):
        yield object()

    return _session
