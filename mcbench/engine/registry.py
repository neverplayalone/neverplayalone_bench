"""Task registry: maps a task id to its Task plugin.

Adding a task = implement a Task under mcbench/tasks/ and
register an instance here. The generic ``mcbench run --task <id>`` CLI
needs no other change.
"""

from __future__ import annotations

from mcbench.tasks.resource_gathering import ResourceGatheringTask
from mcbench.core.task import Task

_TASKS: list[Task] = [
    ResourceGatheringTask(),
]

TASKS: dict[str, Task] = {c.id: c for c in _TASKS}


def get_task(task_id: str) -> Task:
    try:
        return TASKS[task_id]
    except KeyError:
        known = ", ".join(sorted(TASKS))
        raise ValueError(
            f"unknown task {task_id!r}; known tasks: {known}"
        ) from None
