from __future__ import annotations

from mcbench.missions.base import StartingItem
from mcbench.missions.resource_gathering.environment import starting_item_stack
from mcbench.missions.resource_gathering import ResourceGatheringMission
from mcbench.missions.resource_gathering.task import generate_task


def test_bundled_config_loads() -> None:
    mission = ResourceGatheringMission()
    mission_config = mission.load_config(mission.default_config_path())
    assert mission_config.id == "resource_gathering"
    assert mission_config.minecraft_version == "1.21.11"
    assert mission_config.menu is not None
    assert "logs" in mission_config.menu.resources


def test_generate_task_is_deterministic() -> None:
    mission = ResourceGatheringMission()
    mission_config = mission.load_config(mission.default_config_path())
    first = generate_task(mission_config, seed=7)
    second = generate_task(mission_config, seed=7)
    assert first == second
    assert first.task_id == second.task_id


def test_task_converts_to_selected_mission_config() -> None:
    mission = ResourceGatheringMission()
    base_config = mission.load_config(mission.default_config_path())
    task = generate_task(base_config, seed=3)
    mission_config = mission.build_mission_config(base_config, task)

    assert mission_config.id == task.task_id
    assert mission_config.seed == task.minecraft_seed
    assert mission_config.prompt == task.prompt
    assert len(mission_config.resources) == 1


def test_starting_item_stack_serializes_enchantments() -> None:
    starting_item = StartingItem(
        item="diamond_pickaxe",
        enchantments=["efficiency:4", "fortune:3"],
    )

    item_stack = starting_item_stack(starting_item)

    assert item_stack.startswith("minecraft:diamond_pickaxe[")
    assert '"minecraft:efficiency":4' in item_stack
    assert '"minecraft:fortune":3' in item_stack
