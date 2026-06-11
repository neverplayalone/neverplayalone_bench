"""ResourceGatheringCompetition: wires the resource-gathering plugin to the engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from mcrcon import MCRcon

from ...core.competition import Competition, RunConfig
from ...core.trace import Trace
from .challenge import GeneratedChallenge, generate_challenge
from .config import ResourceCompetitionConfig
from .scoring import score_resource_gathering
from .world import capture, configure_world, setup_competitor

_CONFIG_DIR = Path(__file__).resolve().parent / "configs"


class ResourceGatheringCompetition(Competition):
    id = "resource_gathering_v1"

    def default_config_path(self) -> Path:
        return _CONFIG_DIR / "config.yaml"

    def load_config(self, path: str | Path) -> ResourceCompetitionConfig:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return ResourceCompetitionConfig.model_validate(raw)

    def generate_challenge(
        self,
        base_cfg: RunConfig,
        seed: int,
        challenge_id: str | None = None,
    ) -> GeneratedChallenge:
        return generate_challenge(base_cfg, seed, challenge_id=challenge_id)

    def configure_world(self, mcr: MCRcon, cfg: RunConfig) -> None:
        configure_world(mcr, cfg)

    def setup_competitor(self, mcr: MCRcon, cfg: RunConfig) -> Any:
        return setup_competitor(mcr, cfg)

    def goal_text(self, cfg: RunConfig) -> str:
        return cfg.goal

    def capture(self, mcr: MCRcon, cfg: RunConfig, setup_state: Any) -> dict[str, Any]:
        return capture(mcr, cfg, setup_state)

    def score(self, cfg: RunConfig, trace: Trace, snapshot: dict[str, Any]) -> dict[str, Any]:
        return score_resource_gathering(cfg, trace, snapshot)
