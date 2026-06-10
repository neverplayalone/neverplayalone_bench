"""Loaders for the YAML run config and resource catalog."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models.challenge import ResourceCatalog
from .models.competition import ResourceCompetitionConfig


def load_resource_competition_config(path: str | Path) -> ResourceCompetitionConfig:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return ResourceCompetitionConfig.model_validate(raw)


def load_resource_catalog(path: str | Path) -> ResourceCatalog:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return ResourceCatalog.model_validate(raw)
