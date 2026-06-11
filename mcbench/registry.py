"""Competition registry: maps a competition id to its Competition plugin.

Adding a competition = implement a Competition under mcbench/competitions/ and
register an instance here. The generic ``mcbench run --competition <id>`` CLI
needs no other change.
"""

from __future__ import annotations

from .competitions.resource_gathering import ResourceGatheringCompetition
from .core.competition import Competition

_COMPETITIONS: list[Competition] = [
    ResourceGatheringCompetition(),
]

COMPETITIONS: dict[str, Competition] = {c.id: c for c in _COMPETITIONS}


def get_competition(competition_id: str) -> Competition:
    try:
        return COMPETITIONS[competition_id]
    except KeyError:
        known = ", ".join(sorted(COMPETITIONS))
        raise ValueError(
            f"unknown competition {competition_id!r}; known competitions: {known}"
        ) from None
