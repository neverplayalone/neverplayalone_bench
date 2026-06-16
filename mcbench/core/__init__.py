"""Core kernel: the task contract and shared data models.

This is the bottom layer — ``infra``, ``engine``, and ``tasks`` all depend on it,
and it depends on nothing internal, which keeps the package layering acyclic.
"""

from __future__ import annotations

from mcbench.core.task import USERNAME, KitItem, RunConfig, Task
from mcbench.core.trace import FinalState, Trace, TraceEvent, parse_event_line
from mcbench.core.slot import ServerConfig, Slot

__all__ = [
    "USERNAME",
    "KitItem",
    "RunConfig",
    "Task",
    "FinalState",
    "Trace",
    "TraceEvent",
    "parse_event_line",
    "ServerConfig",
    "Slot",
]
