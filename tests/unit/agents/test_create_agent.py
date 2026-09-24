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
    assert agent.container_name == "npabench-agent-3"


def test_sandboxed_agent_uses_custom_slot_prefix_for_concurrent_runs(tmp_path) -> None:
    agent = create_agent(
        AgentSpec(name="a", path=tmp_path),
        agent_mode=AgentMode.SANDBOXED,
        agent_run_slot=AgentRunSlot.allocate(
            slot_id=0,
            container_prefix="npa-rewrite-123",
            data_root=tmp_path / "slot",
        ),
    )
    assert isinstance(agent, SandboxedAgent)
    assert agent.container_name == "npa-rewrite-123-0-agent"
