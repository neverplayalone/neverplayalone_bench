from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from mcrcon import MCRcon

from npabench.evaluation.run_trace import AgentRunTrace
from npabench.missions.base import Mission, MissionConfig, Task
from npabench.missions.crafting.config_schema import CraftingMissionConfig, RecipeSpec
from npabench.missions.crafting.environment import (
    configure_crafting_world,
    setup_crafting_agent,
)
from npabench.missions.crafting.final_state import collect_crafting_state
from npabench.missions.crafting.prompting import materialize_task_prompt
from npabench.missions.crafting.scoring import score_crafting_run
from npabench.missions.crafting.task import CraftingTask, generate_task

_CONFIG_DIR = Path(__file__).resolve().parent / "configs"


class CraftingMission(Mission):
    id = "crafting"

    def default_config_path(self) -> Path:
        return _CONFIG_DIR / "default.yaml"

    def load_config(self, path: str | Path) -> CraftingMissionConfig:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return CraftingMissionConfig.model_validate(raw)

    def generate_task(
        self,
        base_config: MissionConfig,
        seed: int,
        task_id: str | None = None,
    ) -> Task:
        return generate_task(
            CraftingMissionConfig.model_validate(base_config.model_dump()),
            seed,
            task_id=task_id,
        )

    def materialize_task(
        self,
        base_config: MissionConfig,
        task: Task,
        output_dir: Path,
    ) -> Task:
        return materialize_task_prompt(CraftingTask.model_validate(task.model_dump()), output_dir)

    def build_mission_config(
        self,
        base_config: MissionConfig,
        task: Task,
    ) -> CraftingMissionConfig:
        crafting_task = CraftingTask.model_validate(task.model_dump())
        typed_base_config = CraftingMissionConfig.model_validate(base_config.model_dump())
        mission_data = typed_base_config.model_dump(exclude={"menu"})
        mission_data.update(
            {
                "id": crafting_task.task_id,
                "seed": crafting_task.minecraft_seed,
                # Unlike the other two missions, crafting pins the biome: from an
                # empty inventory a treeless spawn is an unrecoverable zero. This
                # switches the server to the single-biome world preset.
                "biome": crafting_task.biome,
                "prompt": crafting_task.prompt,
                "recipes": [
                    RecipeSpec(
                        item=target.key,
                        items=target.items,
                        display_name=target.display_name,
                        band=target.band,
                        target_count=target.target_count,
                        points=target.points,
                    ).model_dump()
                    for target in crafting_task.targets
                ],
            }
        )
        return CraftingMissionConfig.model_validate(mission_data)

    def configure_world(self, rcon: MCRcon, mission_config: MissionConfig) -> None:
        configure_crafting_world(
            rcon,
            CraftingMissionConfig.model_validate(mission_config.model_dump()),
        )

    def setup_agent(self, rcon: MCRcon, mission_config: MissionConfig) -> Any:
        return setup_crafting_agent(
            rcon,
            CraftingMissionConfig.model_validate(mission_config.model_dump()),
        )

    def prompt_text(self, mission_config: MissionConfig) -> str:
        return mission_config.prompt

    def collect_final_state(
        self,
        rcon: MCRcon,
        mission_config: MissionConfig,
        setup_state: Any,
    ) -> dict[str, Any]:
        return collect_crafting_state(
            rcon,
            CraftingMissionConfig.model_validate(mission_config.model_dump()),
            setup_state,
        )

    def score(
        self,
        mission_config: MissionConfig,
        agent_run_trace: AgentRunTrace,
        final_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        return score_crafting_run(
            CraftingMissionConfig.model_validate(mission_config.model_dump()),
            agent_run_trace,
            final_snapshot,
        )
