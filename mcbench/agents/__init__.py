from __future__ import annotations

from mcbench.agents.base import Agent, AgentRunContext, AgentSpec
from mcbench.agents.sandboxed_agent import SandboxedAgent, ensure_agent_image
from mcbench.agents.subprocess_agent import SubprocessAgent
from mcbench.evaluation.run_slot import AgentRunSlot

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
        return SandboxedAgent(
            spec,
            container_name=f"mcbench-agent-{agent_run_slot.slot_id}",
            network_name=agent_run_slot.network_name,
            server_host=agent_run_slot.container_name,
            image=image,
        )
    if mode_value == "host":
        return SubprocessAgent(spec)
    raise ValueError(f"unknown agent mode {agent_mode!r}")
