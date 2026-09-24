from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from mcrcon import MCRcon

from npabench.evaluation.run_slot import ServerEndpoint
from npabench.evaluation.run_trace import AgentRunTrace
from npabench.missions.base import Mission, MissionConfig, MissionRuntime, Task
from npabench.missions.farming.config_schema import FarmingMissionConfig
from npabench.missions.farming.datapack import write_farming_datapack
from npabench.missions.farming.environment import configure_farming_world, setup_farming_agent
from npabench.missions.farming.final_state import collect_farming_state
from npabench.missions.farming.runtime import FarmingController
from npabench.missions.farming.scoring import score_farming_run
from npabench.missions.farming.task import FarmingTask, generate_task, target_specs

_CONFIG_DIR = Path(__file__).resolve().parent / "configs"


class FarmingMission(Mission):
    id = "farming"

    def default_config_path(self) -> Path:
        return _CONFIG_DIR / "default.yaml"

    def load_config(self, path: str | Path) -> FarmingMissionConfig:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return FarmingMissionConfig.model_validate(raw)

    def generate_task(
        self,
        base_config: MissionConfig,
        seed: int,
        task_id: str | None = None,
    ) -> FarmingTask:
        return generate_task(
            FarmingMissionConfig.model_validate(base_config.model_dump()),
            seed,
            task_id=task_id,
        )

    def build_mission_config(
        self,
        base_config: MissionConfig,
        task: Task,
    ) -> FarmingMissionConfig:
        farming_task = FarmingTask.model_validate(task.model_dump())
        typed_base = FarmingMissionConfig.model_validate(base_config.model_dump())
        mission_data = typed_base.model_dump(exclude={"menu"})
        mission_data.update(
            {
                "id": farming_task.task_id,
                "seed": farming_task.minecraft_seed,
                "biome": None,
                "prompt": farming_task.prompt,
                "duration_seconds": farming_task.duration_seconds,
                "keep_inventory": farming_task.keep_inventory,
                "layout_seed": farming_task.layout_seed,
                "targets": [target.model_dump() for target in target_specs(farming_task.targets)],
            }
        )
        return FarmingMissionConfig.model_validate(mission_data)

    def prepare_reference_world(
        self,
        data_dir: Path,
        mission_config: MissionConfig,
    ) -> None:
        write_farming_datapack(
            data_dir,
            FarmingMissionConfig.model_validate(mission_config.model_dump()),
        )

    def configure_world(self, rcon: MCRcon, mission_config: MissionConfig) -> None:
        configure_farming_world(
            rcon,
            FarmingMissionConfig.model_validate(mission_config.model_dump()),
        )

    def setup_agent(self, rcon: MCRcon, mission_config: MissionConfig) -> Any:
        return setup_farming_agent(
            rcon,
            FarmingMissionConfig.model_validate(mission_config.model_dump()),
        )

    def start_runtime(
        self,
        server_endpoint: ServerEndpoint,
        mission_config: MissionConfig,
        setup_state: Any,
    ) -> MissionRuntime:
        controller = FarmingController(
            server_endpoint,
            FarmingMissionConfig.model_validate(mission_config.model_dump()),
            setup_state=setup_state,
        )
        return controller.start()

    def prompt_text(self, mission_config: MissionConfig) -> str:
        return mission_config.prompt

    def collect_final_state(
        self,
        rcon: MCRcon,
        mission_config: MissionConfig,
        setup_state: Any,
    ) -> dict[str, Any]:
        return collect_farming_state(
            rcon,
            FarmingMissionConfig.model_validate(mission_config.model_dump()),
            setup_state,
        )

    def score(
        self,
        mission_config: MissionConfig,
        agent_run_trace: AgentRunTrace,
        final_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        return score_farming_run(
            FarmingMissionConfig.model_validate(mission_config.model_dump()),
            agent_run_trace,
            final_snapshot,
        )
