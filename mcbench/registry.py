"""Competition registry: maps a competition id to its default config files.

Today there is one entry (resource gathering v1). When a second competition
(crafting, hunting, ...) lands it adds an entry here; the generic
``mcbench run --competition <id>`` CLI needs no other change.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import REPO_ROOT

_CONFIG_DIR = REPO_ROOT / "configs"


@dataclass(frozen=True)
class CompetitionEntry:
    id: str
    description: str
    config_path: Path
    catalog_path: Path


COMPETITIONS: dict[str, CompetitionEntry] = {
    "resource_gathering_v1": CompetitionEntry(
        id="resource_gathering_v1",
        description="Gather a target resource and return near spawn.",
        config_path=_CONFIG_DIR / "resource_gathering" / "base.yaml",
        catalog_path=_CONFIG_DIR / "resource_gathering" / "catalog.yaml",
    ),
}


def get_competition(competition_id: str) -> CompetitionEntry:
    try:
        return COMPETITIONS[competition_id]
    except KeyError:
        known = ", ".join(sorted(COMPETITIONS))
        raise ValueError(
            f"unknown competition {competition_id!r}; known competitions: {known}"
        ) from None
