"""Generic benchmark engine — competition-agnostic.

Drives any :class:`Competition`: Docker slots, RCON, the agent subprocess,
recording, the run loop, and parallel batching all live here.
"""

from __future__ import annotations

from .batch import (
    EvaluationBatch,
    EvaluationSlot,
    ParallelEvaluator,
    WorldTemplateBuilder,
    create_evaluation_batch,
    parse_agent_assignment,
    run_evaluation_batch,
)
from .competition import USERNAME, Competition, KitItem, RunConfig
from .runner import run_competition
from .slot import CompetitionSlot
from .trace import FinalState, Trace, TraceEvent, parse_event_line

__all__ = [
    "Competition",
    "CompetitionSlot",
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
    "run_competition",
    "run_evaluation_batch",
]
