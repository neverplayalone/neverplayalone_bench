from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    t: float = Field(default_factory=time.monotonic)
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)


class FinalAgentState(BaseModel):
    inventory: dict[str, int] = Field(default_factory=dict)
    position: tuple[float, float, float] | None = None
    health: float | None = None
    food: float | None = None


class AgentRunTrace(BaseModel):
    task_id: str
    agent_name: str
    started_at: float
    agent_ready_at: float | None = None
    ended_at: float | None = None
    timed_out: bool = False
    events: list[TraceEvent] = Field(default_factory=list)
    final_state: FinalAgentState = Field(default_factory=FinalAgentState)

    def append(self, event: TraceEvent) -> None:
        self.events.append(event)

    def dump(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "AgentRunTrace":
        return cls.model_validate_json(Path(path).read_text())


def parse_trace_event_line(line: str) -> TraceEvent | None:
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
