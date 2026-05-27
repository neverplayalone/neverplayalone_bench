"""Task configuration: YAML schema + loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class SetupSpec(BaseModel):
    world: Literal["flat", "default"] = "flat"
    commands: list[str] = Field(default_factory=list)


class Rule(BaseModel):
    """A rule-based success check evaluated against the final trace."""

    kind: str
    item: str | None = None
    block: str | None = None
    entity: str | None = None
    min_count: int = 1
    max_count: int | None = None


class SuccessSpec(BaseModel):
    rules: list[Rule] = Field(default_factory=list)
    llm_rubric: list[str] | None = None
    threshold: float = 1.0


class TaskConfig(BaseModel):
    id: str
    difficulty: Literal["simple", "hard"] = "simple"
    goal: str
    setup: SetupSpec = Field(default_factory=SetupSpec)
    timeout_seconds: int = 120
    success: SuccessSpec = Field(default_factory=SuccessSpec)
    metadata: dict[str, Any] = Field(default_factory=dict)


def load_task(path: str | Path) -> TaskConfig:
    raw = yaml.safe_load(Path(path).read_text())
    return TaskConfig.model_validate(raw)
