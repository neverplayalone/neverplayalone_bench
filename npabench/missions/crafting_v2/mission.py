from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from mcrcon import MCRcon

from npabench.evaluation.run_trace import AgentRunTrace
from npabench.missions.base import Mission, MissionConfig, Task

# Reused from v1, which is frozen: these are band-agnostic (they read recipes,
# inventory and distance, never the band structure), so sharing them avoids
# duplicating hard-won scoring logic while keeping the two missions independent.
from npabench.missions.crafting.final_state import collect_crafting_state
from npabench.missions.crafting.scoring import score_crafting_run
from npabench.missions.crafting_v2.config_schema import CraftingV2MissionConfig, RecipeSpec
from npabench.missions.crafting_v2.environment import (
    configure_crafting_v2_world,
    setup_crafting_v2_agent,
)
from npabench.missions.crafting_v2.prompting import materialize_task_prompt
from npabench.missions.crafting_v2.task import CraftingV2Task, generate_task

_CONFIG_DIR = Path(__file__).resolve().parent / "configs"


class CraftingV2Mission(Mission):
    """Iron tier. The agent starts with a stone-tool kit and must reach iron:
    mine iron ore, run a second smelting stage, and craft iron tools and
    functional items. Its own module (config, sampling, environment, prompting);
    only v1's stable scoring/inventory helpers are shared."""

    id = "crafting_v2"

    def default_config_path(self) -> Path:
        return _CONFIG_DIR / "iron.yaml"

    def load_config(self, path: str | Path) -> CraftingV2MissionConfig:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        return CraftingV2MissionConfig.model_validate(raw)

    def generate_task(
        self,
        base_config: MissionConfig,
        seed: int,
        task_id: str | None = None,
    ) -> Task:
        return generate_task(
            CraftingV2MissionConfig.model_validate(base_config.model_dump()),
            seed,
            task_id=task_id,
        )

    def materialize_task(
        self,
        base_config: MissionConfig,
        task: Task,
        output_dir: Path,
    ) -> Task:
        return materialize_task_prompt(
            CraftingV2Task.model_validate(task.model_dump()), output_dir
        )

    def build_mission_config(
        self,
        base_config: MissionConfig,
        task: Task,
    ) -> CraftingV2MissionConfig:
        crafting_task = CraftingV2Task.model_validate(task.model_dump())
        typed_base_config = CraftingV2MissionConfig.model_validate(base_config.model_dump())
        mission_data = typed_base_config.model_dump(exclude={"menu"})
        mission_data.update(
            {
                "id": crafting_task.task_id,
                "seed": crafting_task.minecraft_seed,
                # Pin the biome (single-biome preset): even with tools, a treeless
                # spawn is an unrecoverable zero (sticks/handles/fuel need wood).
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
        return CraftingV2MissionConfig.model_validate(mission_data)

    def configure_world(self, rcon: MCRcon, mission_config: MissionConfig) -> None:
        configure_crafting_v2_world(
            rcon,
            CraftingV2MissionConfig.model_validate(mission_config.model_dump()),
        )

    def setup_agent(self, rcon: MCRcon, mission_config: MissionConfig) -> Any:
        return setup_crafting_v2_agent(
            rcon,
            CraftingV2MissionConfig.model_validate(mission_config.model_dump()),
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
            CraftingV2MissionConfig.model_validate(mission_config.model_dump()),
            setup_state,
        )

    def score(
        self,
        mission_config: MissionConfig,
        agent_run_trace: AgentRunTrace,
        final_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        return score_crafting_run(
            CraftingV2MissionConfig.model_validate(mission_config.model_dump()),
            agent_run_trace,
            final_snapshot,
        )
