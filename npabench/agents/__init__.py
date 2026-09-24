from __future__ import annotations

from npabench.agents.base import Agent, AgentRunContext, AgentSpec
from npabench.agents.sandboxed_agent import SandboxedAgent, ensure_agent_image
from npabench.agents.subprocess_agent import SubprocessAgent
from npabench.evaluation.run_slot import AgentRunSlot

__all__ = [
    "Agent",
    "AgentSpec",
    "AgentRunContext",
    "SubprocessAgent",
    "SandboxedAgent",
    "ensure_agent_image",
    "create_agent",
]


def create_agent(
    spec: AgentSpec,
    *,
    agent_mode,
    agent_run_slot: AgentRunSlot,
    image: str | None = None,
) -> Agent:
    mode_value = getattr(agent_mode, "value", agent_mode)
    if mode_value == "sandboxed":
        container_name = (
            f"npabench-agent-{agent_run_slot.slot_id}"
            if agent_run_slot.container_prefix == "npabench-eval"
            else f"{agent_run_slot.container_name}-agent"
        )
        return SandboxedAgent(
            spec,
            container_name=container_name,
            network_name=agent_run_slot.network_name,
            server_host=agent_run_slot.container_name,
            image=image,
        )
    if mode_value == "host":
        return SubprocessAgent(spec)
    raise ValueError(f"unknown agent mode {agent_mode!r}")
