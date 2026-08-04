from __future__ import annotations

from npabench.agents import SandboxedAgent, SubprocessAgent, create_agent
from npabench.agents.base import AgentSpec
from npabench.evaluation.evaluate import AgentMode
from npabench.evaluation.run_slot import AgentRunSlot


def test_create_agent_returns_subprocess_agent(tmp_path) -> None:
    agent = create_agent(
        AgentSpec(name="a", path=tmp_path),
        agent_mode=AgentMode.HOST,
        agent_run_slot=AgentRunSlot.allocate(data_root=tmp_path / "slot"),
    )
    assert isinstance(agent, SubprocessAgent)


def test_create_agent_returns_sandboxed_agent(tmp_path) -> None:
    agent = create_agent(
        AgentSpec(name="a", path=tmp_path),
        agent_mode=AgentMode.SANDBOXED,
        agent_run_slot=AgentRunSlot.allocate(slot_id=3, data_root=tmp_path / "slot"),
    )
    assert isinstance(agent, SandboxedAgent)
    assert agent.container_name == "npabench-eval-3-agent"
