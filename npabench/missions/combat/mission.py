from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from mcrcon import MCRcon

from npabench.evaluation.run_slot import ServerEndpoint
from npabench.evaluation.run_trace import AgentRunTrace
from npabench.missions.base import Mission, MissionConfig, MissionRuntime, Task
from npabench.missions.combat.config_schema import CombatMissionConfig
from npabench.missions.combat.environment import configure_combat_world, setup_combat_agent
from npabench.missions.combat.final_state import collect_combat_state
from npabench.missions.combat.prompting import materialize_task_prompt
from npabench.missions.combat.scoring import score_combat_run
from npabench.missions.combat.task import CombatTask, generate_task, target_specs
from npabench.missions.combat.waves import CombatWaveController

_CONFIG_DIR = Path(__file__).resolve().parent / "configs"


class CombatMission(Mission):
    id = "combat"

    def default_config_path(self) -> Path:
        return _CONFIG_DIR / "default.yaml"

    def load_config(self, path: str | Path) -> CombatMissionConfig:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return CombatMissionConfig.model_validate(raw)

    def generate_task(
        self,
        base_config: MissionConfig,
        seed: int,
        task_id: str | None = None,
    ) -> CombatTask:
        return generate_task(
            CombatMissionConfig.model_validate(base_config.model_dump()),
            seed,
            task_id=task_id,
        )

    def materialize_task(
        self,
        base_config: MissionConfig,
        task: Task,
        output_dir: Path,
    ) -> CombatTask:
        del base_config
        return materialize_task_prompt(CombatTask.model_validate(task.model_dump()), output_dir)

    def build_mission_config(
        self,
        base_config: MissionConfig,
        task: Task,
    ) -> CombatMissionConfig:
        combat_task = CombatTask.model_validate(task.model_dump())
        typed_base = CombatMissionConfig.model_validate(base_config.model_dump())
        mission_data = typed_base.model_dump(exclude={"menu"})
        mission_data.update(
            {
                "id": combat_task.task_id,
                "seed": combat_task.minecraft_seed,
                "biome": None,
                "prompt": combat_task.prompt,
                "duration_seconds": (combat_task.preparation_seconds + combat_task.combat_seconds),
                "keep_inventory": combat_task.keep_inventory,
                "targets": [target.model_dump() for target in target_specs(combat_task.targets)],
                "waves": [wave.model_dump() for wave in combat_task.waves],
                "phase": {
                    **typed_base.phase.model_dump(),
                    "preparation_seconds": combat_task.preparation_seconds,
                    "combat_seconds": combat_task.combat_seconds,
                    "spawn_mobs_naturally": combat_task.spawn_mobs_naturally,
                },
                "scoring": {
                    "death_penalty_points": combat_task.death_penalty_points,
                },
            }
        )
        return CombatMissionConfig.model_validate(mission_data)

    def configure_world(self, rcon: MCRcon, mission_config: MissionConfig) -> None:
        configure_combat_world(
            rcon,
            CombatMissionConfig.model_validate(mission_config.model_dump()),
        )

    def setup_agent(self, rcon: MCRcon, mission_config: MissionConfig) -> Any:
        return setup_combat_agent(
            rcon,
            CombatMissionConfig.model_validate(mission_config.model_dump()),
        )

    def start_runtime(
        self,
        server_endpoint: ServerEndpoint,
        mission_config: MissionConfig,
        setup_state: Any,
    ) -> MissionRuntime:
        del setup_state
        controller = CombatWaveController(
            server_endpoint,
            CombatMissionConfig.model_validate(mission_config.model_dump()),
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
        return collect_combat_state(
            rcon,
            CombatMissionConfig.model_validate(mission_config.model_dump()),
            setup_state,
        )

    def score(
        self,
        mission_config: MissionConfig,
        agent_run_trace: AgentRunTrace,
        final_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        return score_combat_run(
            CombatMissionConfig.model_validate(mission_config.model_dump()),
            agent_run_trace,
            final_snapshot,
        )
