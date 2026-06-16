"""Generic orchestration engine — task-agnostic.

Drives any :class:`~mcbench.core.task.Task`: builds the world template, runs
agent slots in parallel, and writes the report. Depends on ``core`` + ``infra``.
"""

from __future__ import annotations

from mcbench.engine.batch import (
    EvaluationBatch,
    EvaluationSlot,
    ParallelEvaluator,
    WorldTemplateBuilder,
    create_evaluation_batch,
    make_agent,
    parse_agent_assignment,
    run_evaluation_batch,
)
from mcbench.engine.runner import run_task
from mcbench.engine.registry import TASKS, get_task

__all__ = [
    "EvaluationBatch",
    "EvaluationSlot",
    "ParallelEvaluator",
    "WorldTemplateBuilder",
    "create_evaluation_batch",
    "make_agent",
    "parse_agent_assignment",
    "run_evaluation_batch",
    "run_task",
    "TASKS",
    "get_task",
]
