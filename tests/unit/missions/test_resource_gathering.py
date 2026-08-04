from __future__ import annotations

from npabench.missions.base import StartingItem
from npabench.missions.resource_gathering.environment import (
    configure_resource_gathering_world,
    starting_item_stack,
)
from npabench.missions.resource_gathering import ResourceGatheringMission
from npabench.missions.resource_gathering.task import ESSENTIAL_TARGET_KEYS, generate_task


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
    assert len(first.targets) == 5


def test_generate_task_always_contains_three_essentials_and_two_optionals() -> None:
    mission = ResourceGatheringMission()
    mission_config = mission.load_config(mission.default_config_path())

    task = generate_task(mission_config, seed=13)
    target_keys = [target.key for target in task.targets]

    assert target_keys[:3] == list(ESSENTIAL_TARGET_KEYS)
    assert len(task.targets) == 5
    assert len(set(target_keys)) == 5
    assert all(target.role == "essential" for target in task.targets[:3])
    assert all(target.role == "optional" for target in task.targets[3:])


def test_task_converts_to_selected_mission_config() -> None:
    mission = ResourceGatheringMission()
    base_config = mission.load_config(mission.default_config_path())
    task = generate_task(base_config, seed=3)
    mission_config = mission.build_mission_config(base_config, task)

    assert mission_config.id == task.task_id
    assert mission_config.seed == task.minecraft_seed
    assert mission_config.prompt == task.prompt
    assert len(mission_config.resources) == 5
    assert [resource.role for resource in mission_config.resources[:3]] == [
        "essential",
        "essential",
        "essential",
    ]
    assert mission_config.resources[0].points == 25
    assert mission_config.resources[-1].points == 12.5


def test_default_config_includes_shears() -> None:
    mission = ResourceGatheringMission()
    mission_config = mission.load_config(mission.default_config_path())

    assert any(item.item == "shears" for item in mission_config.starting_items)


def test_starting_item_stack_serializes_enchantments() -> None:
    starting_item = StartingItem(
        item="diamond_pickaxe",
        enchantments=["efficiency:4", "fortune:3"],
    )

    item_stack = starting_item_stack(starting_item)

    assert item_stack.startswith("minecraft:diamond_pickaxe[")
    assert '"minecraft:efficiency":4' in item_stack
    assert '"minecraft:fortune":3' in item_stack


class _RecordingRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return ""


def test_reference_world_disables_random_spawn_radius_before_cloning() -> None:
    mission = ResourceGatheringMission()
    base_config = mission.load_config(mission.default_config_path())
    config = mission.build_mission_config(base_config, generate_task(base_config, seed=7))
    rcon = _RecordingRcon()

    configure_resource_gathering_world(rcon, config)

    assert rcon.commands[0] == "gamerule respawn_radius 0"
    assert "gamerule spawn_mobs false" in rcon.commands
    assert not any("doMobSpawning" in command for command in rcon.commands)
