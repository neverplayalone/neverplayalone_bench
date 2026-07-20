from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from mcrcon import MCRcon

from npabench.evaluation.run_trace import AgentRunTrace
from npabench.missions.base import Mission, MissionConfig, Task
from npabench.missions.mining.config_schema import (
    MiningMissionConfig,
    ResourceSpec,
)
from npabench.missions.mining.environment import (
    configure_mining_world,
    setup_mining_agent,
)
from npabench.missions.mining.final_state import collect_mining_state
from npabench.missions.mining.prompting import materialize_task_prompt
from npabench.missions.mining.scoring import score_mining_run
from npabench.missions.mining.task import generate_task

_CONFIG_DIR = Path(__file__).resolve().parent / "configs"


class MiningMission(Mission):
    id = "mining"

    def default_config_path(self) -> Path:
        return _CONFIG_DIR / "default.yaml"

    def load_config(self, path: str | Path) -> MiningMissionConfig:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return MiningMissionConfig.model_validate(raw)

    def generate_task(
        self,
        base_config: MissionConfig,
        seed: int,
        task_id: str | None = None,
    ) -> Task:
        return generate_task(
            MiningMissionConfig.model_validate(base_config.model_dump()),
            seed,
            task_id=task_id,
        )

    def materialize_task(
        self,
        base_config: MissionConfig,
        task: Task,
        output_dir: Path,
    ) -> Task:
        return materialize_task_prompt(task, output_dir)

    def build_mission_config(
        self,
        base_config: MissionConfig,
        task: Task,
    ) -> MiningMissionConfig:
        typed_base_config = MiningMissionConfig.model_validate(base_config.model_dump())
        mission_data = typed_base_config.model_dump(exclude={"menu"})
        mission_data.update(
            {
                "id": task.task_id,
                "seed": task.minecraft_seed,
                "biome": None,
                "prompt": task.prompt,
                "resources": [
                    ResourceSpec(
                        item=target.key,
                        items=target.items,
                        display_name=target.display_name,
                        target_count=target.target_count,
                        points=target.points,
                        role=target.role,
                    ).model_dump()
                    for target in task.targets
                ],
            }
        )
        return MiningMissionConfig.model_validate(mission_data)

    def configure_world(self, rcon: MCRcon, mission_config: MissionConfig) -> None:
        configure_mining_world(
            rcon,
            MiningMissionConfig.model_validate(mission_config.model_dump()),
        )

    def setup_agent(self, rcon: MCRcon, mission_config: MissionConfig) -> Any:
        return setup_mining_agent(
            rcon,
            MiningMissionConfig.model_validate(mission_config.model_dump()),
        )

    def prompt_text(self, mission_config: MissionConfig) -> str:
        return mission_config.prompt

    def collect_final_state(
        self,
        rcon: MCRcon,
        mission_config: MissionConfig,
        setup_state: Any,
    ) -> dict[str, Any]:
        return collect_mining_state(
            rcon,
            MiningMissionConfig.model_validate(mission_config.model_dump()),
            setup_state,
        )

    def score(
        self,
        mission_config: MissionConfig,
        agent_run_trace: AgentRunTrace,
        final_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        return score_mining_run(
            MiningMissionConfig.model_validate(mission_config.model_dump()),
            agent_run_trace,
            final_snapshot,
        )
