"""Trace schema: structured event log + final-state snapshot produced by an agent run."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    """A single event emitted by the agent during a run.

    The agent is responsible for emitting these as JSONL on stdout (one per line).
    The runner consumes them and appends to the trace.
    """

    t: float = Field(default_factory=time.monotonic)
    kind: str  # e.g., "action", "obs", "chat", "error", "info"
    data: dict[str, Any] = Field(default_factory=dict)


class FinalState(BaseModel):
    """End-of-run snapshot pulled from the server (not the agent).

    Populated by the runner using RCON after the agent stops.
    """

    inventory: dict[str, int] = Field(default_factory=dict)
    position: tuple[float, float, float] | None = None
    health: float | None = None
    food: float | None = None


class Trace(BaseModel):
    challenge_id: str
    agent_name: str
    started_at: float
    # When the agent first reported `ready` (spawned in-world). The agent-active
    # window — used for time/efficiency scoring — is agent_ready_at -> ended_at,
    # which excludes container boot and world load. None if the agent never spawned.
    agent_ready_at: float | None = None
    ended_at: float | None = None
    timed_out: bool = False
    events: list[TraceEvent] = Field(default_factory=list)
    final_state: FinalState = Field(default_factory=FinalState)

    def append(self, event: TraceEvent) -> None:
        self.events.append(event)

    def dump(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "Trace":
        return cls.model_validate_json(Path(path).read_text())


def parse_event_line(line: str) -> TraceEvent | None:
    """Parse a single JSONL line from an agent's stdout into a TraceEvent.

    Returns None for blank or non-JSON lines (which are treated as info logs upstream).
    """
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "kind" not in obj:
        return None
    return TraceEvent.model_validate(obj)
