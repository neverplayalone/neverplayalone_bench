from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from npabench.evaluation.run_trace import TraceEvent


@dataclass(frozen=True)
class AgentSpec:
    name: str
    path: Path
    extra_args: list[str] | None = None
    kind: str | None = None
    # Extra environment variables injected into the agent's sandbox container.
    # The subnet uses this to route LLM calls through its egress proxy; core
    # NPABENCH_* variables always take precedence over anything set here.
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class AgentRunContext:
    host: str
    port: int
    username: str
    prompt: str
    timeout_seconds: int


class Agent(ABC):
    def __init__(self, spec: AgentSpec):
        self.spec = spec

    @abstractmethod
    def run(self, context: AgentRunContext) -> Iterator[TraceEvent]: ...

    @abstractmethod
    def stop(self) -> None: ...
