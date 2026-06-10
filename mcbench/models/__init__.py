"""Pydantic domain models for the benchmark."""

from __future__ import annotations

from .challenge import GeneratedChallenge, ResourceCatalog, ResourceCatalogEntry
from .competition import (
    CompetitionScoringConfig,
    KitItem,
    ResourceCompetitionConfig,
    ResourceTarget,
)
from .trace import FinalState, Trace, TraceEvent, parse_event_line

__all__ = [
    "CompetitionScoringConfig",
    "FinalState",
    "GeneratedChallenge",
    "KitItem",
    "ResourceCatalog",
    "ResourceCatalogEntry",
    "ResourceCompetitionConfig",
    "ResourceTarget",
    "Trace",
    "TraceEvent",
    "parse_event_line",
]
