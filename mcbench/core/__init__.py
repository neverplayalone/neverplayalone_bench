"""Generic benchmark engine — task-agnostic.

Drives any :class:`Task`: Docker slots, RCON, the agent subprocess,
recording, the run loop, and parallel batching all live here.
"""

from __future__ import annotations

from mcbench.core.batch import (
    EvaluationBatch,
    EvaluationSlot,
    ParallelEvaluator,
    WorldTemplateBuilder,
    create_evaluation_batch,
    parse_agent_assignment,
    run_evaluation_batch,
)
from mcbench.core.base_task import USERNAME, Task, KitItem, RunConfig
from mcbench.core.runner import run_task
from mcbench.core.slot import Slot
from mcbench.core.trace import FinalState, Trace, TraceEvent, parse_event_line

__all__ = [
    "Task",
    "Slot",
    "EvaluationBatch",
    "EvaluationSlot",
    "FinalState",
    "KitItem",
    "ParallelEvaluator",
    "RunConfig",
    "Trace",
    "TraceEvent",
    "USERNAME",
    "WorldTemplateBuilder",
    "create_evaluation_batch",
    "parse_agent_assignment",
    "parse_event_line",
    "run_task",
    "run_evaluation_batch",
]
