"""Agent interface. An agent is anything that can connect to the server and emit a trace."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator

from ..trace import TraceEvent


@dataclass
class AgentSpec:
    """Static metadata describing how to launch an agent."""

    name: str
    path: str  # directory or executable
    extra_args: list[str] | None = None


@dataclass
class AgentRunContext:
    """Per-run parameters passed to the agent on startup."""

    host: str
    port: int
    username: str
    goal: str
    timeout_seconds: int


class Agent(ABC):
    """Base agent. Implementations wrap a subprocess, a Python coroutine, or an HTTP service."""

    def __init__(self, spec: AgentSpec):
        self.spec = spec

    @abstractmethod
    def run(self, ctx: AgentRunContext) -> Iterator[TraceEvent]:
        """Launch the agent and yield TraceEvents until it exits or the runner cancels."""

    @abstractmethod
    def stop(self) -> None:
        """Best-effort cleanup of the underlying process."""
