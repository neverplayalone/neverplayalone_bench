from pathlib import Path

import yaml

from npabench.missions.base import Mission

from .config_schema import AnimalHusbandryConfig
from .environment import configure_world, setup_agent
from .final_state import collect_state
from .plugin import install_plugin
from .scoring import score_run
from .task import AnimalTask, generate_task


class AnimalHusbandryMission(Mission):
    id = "animal_husbandry"

    def default_config_path(self):
        return Path(__file__).parent / "configs/default.yaml"

    def load_config(self, path):
        return AnimalHusbandryConfig.model_validate(yaml.safe_load(Path(path).read_text()) or {})

    def generate_task(self, base_config, seed, task_id=None):
        return generate_task(
            AnimalHusbandryConfig.model_validate(base_config.model_dump()), seed, task_id
        )

    def build_mission_config(self, base_config, task):
        task = AnimalTask.model_validate(task.model_dump())
        return AnimalHusbandryConfig.model_validate(
            {
                **base_config.model_dump(),
                "id": task.task_id,
                "seed": task.minecraft_seed,
                "prompt": task.prompt,
                "targets": [t.model_dump() for t in task.targets],
                "starter_crops": task.starter_crops,
            }
        )

    def prepare_reference_world(self, data_dir, mission_config):
        install_plugin(data_dir, mission_config)

    def configure_world(self, rcon, mission_config):
        configure_world(rcon, mission_config)

    def setup_agent(self, rcon, mission_config):
        return setup_agent(rcon, mission_config)

    def prompt_text(self, mission_config):
        return mission_config.prompt

    def collect_final_state(self, rcon, mission_config, setup_state):
        return collect_state(rcon, mission_config, setup_state)

    def score(self, mission_config, agent_run_trace, final_snapshot):
        return score_run(mission_config, agent_run_trace, final_snapshot)
