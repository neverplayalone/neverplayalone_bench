from __future__ import annotations

import json
import re
from collections import Counter

import pytest

from npabench.config import DATAPACK_FORMAT
from npabench.evaluation.run_slot import ServerEndpoint
from npabench.evaluation.run_trace import AgentRunTrace, FinalAgentState
from npabench.missions.farming import FarmingMission
from npabench.missions.farming.config_schema import FarmingMissionConfig, FarmingTargetSpec
from npabench.missions.farming.datapack import (
    HARVEST_OBJECTIVE,
    READY_OBJECTIVE,
    SYSTEM_OBJECTIVE,
    write_farming_datapack,
)
from npabench.missions.farming.environment import (
    _build_crop_plot,
    configure_farming_world,
    setup_farming_agent,
)
from npabench.missions.farming.final_state import collect_farming_state
from npabench.missions.farming.runtime import FarmingController
from npabench.missions.farming.scoring import score_farming_run
from npabench.missions.farming.task import FarmingTask, generate_task
from npabench.missions.registry import get_mission


class FakeRcon:
    def __init__(
        self,
        *,
        scores: dict[tuple[str, str], int] | None = None,
        inventory: dict[str, int] | None = None,
    ) -> None:
        self.commands: list[str] = []
        self.scores = scores or {("#pack", SYSTEM_OBJECTIVE): 1}
        self.inventory = inventory or {}

    def command(self, command: str) -> str:
        self.commands.append(command)
        parts = command.split()
        if command.startswith("scoreboard players set"):
            self.scores[(parts[3], parts[4])] = int(parts[5])
            return "Set score"
        if command.startswith("scoreboard players add"):
            holder, objective, amount = parts[3:6]
            key = (holder, objective)
            self.scores[key] = self.scores.get(key, 0) + int(amount)
            return "Added score"
        if command.startswith("scoreboard players get"):
            holder, objective = parts[3:5]
            value = self.scores.get((holder, objective), 0)
            return f"{holder} has {value} [{objective}]"
        if command.startswith("clear ") and command.endswith(" 0"):
            item = command.split()[2].removeprefix("minecraft:")
            count = self.inventory.get(item, 0)
            return f"Found {count} matching item(s) on player npabench_agent"
        if command.endswith(" Pos"):
            return "npabench_agent has the following entity data: [2.5d, 70.0d, -1.5d]"
        if command.endswith(" Health"):
            return "20.0f"
        if command.endswith(" foodLevel"):
            return "18"
        if command.startswith("summon minecraft:marker"):
            return "Summoned new Marker"
        return ""


def mission_and_config() -> tuple[FarmingMission, FarmingMissionConfig]:
    mission = FarmingMission()
    return mission, mission.load_config(mission.default_config_path())


def built_config(seed: int = 42) -> tuple[FarmingTask, FarmingMissionConfig]:
    mission, base = mission_and_config()
    task = generate_task(base, seed)
    return task, mission.build_mission_config(base, task)


def trace_for(config: FarmingMissionConfig) -> AgentRunTrace:
    return AgentRunTrace(
        task_id=config.id,
        agent_name="farmer",
        started_at=0.0,
        agent_ready_at=1.0,
        ended_at=100.0,
        final_state=FinalAgentState(position=(2.5, 70.0, -1.5), health=20.0),
    )


def full_snapshot(config: FarmingMissionConfig) -> dict:
    return {
        "harvests": {target.key: target.target_count for target in config.targets},
        "runtime": {"status": "stopped", "harvests": {}},
        "alive": True,
    }


def spec_from_menu(key: str, index: int, config: FarmingMissionConfig) -> FarmingTargetSpec:
    assert config.menu is not None
    entry = config.menu.crops[key]
    return FarmingTargetSpec(
        key=key,
        display_name=entry.display_name,
        difficulty=entry.difficulty,
        template=entry.template,
        target_count=entry.target_range[0],
        points=entry.points,
        item=entry.item,
        starter_item=entry.starter_item,
        starter_count=entry.starter_count,
        growth_step_seconds=entry.growth_step_seconds,
        planted_block=entry.planted_block,
        mature_block=entry.mature_block,
        harvest_mode=entry.harvest_mode,
        max_age=entry.max_age,
        generated_block=entry.generated_block,
        harvest_holder=f"#farm{index:02d}",
        plot_tag=f"nff_plot_{index:02d}",
    )


def test_farming_is_registered() -> None:
    assert isinstance(get_mission("farming"), FarmingMission)


def test_default_config_defines_crop_only_twenty_minute_100_point_shape() -> None:
    _, config = mission_and_config()
    assert config.duration_seconds == 1200
    assert config.difficulty == "peaceful"
    assert config.keep_inventory is True
    assert config.generate_structures is False
    assert config.sampling.model_dump() == {"easy_targets": 2, "medium_targets": 2}
    assert config.menu is not None
    assert len(config.menu.crops) == 16
    assert Counter(entry.difficulty for entry in config.menu.crops.values()) == {
        "easy": 8,
        "medium": 8,
    }


def test_task_generation_is_deterministic_random_and_always_100_points() -> None:
    _, config = mission_and_config()
    assert generate_task(config, 42) == generate_task(config, 42)
    selections = set()
    worlds = set()
    layouts = set()
    for seed in range(1000):
        task = generate_task(config, seed)
        selections.add(tuple(target.key for target in task.targets))
        worlds.add(task.minecraft_seed)
        layouts.add(task.layout_seed)
        assert Counter(target.difficulty for target in task.targets) == {
            "easy": 2,
            "medium": 2,
        }
        assert [target.points for target in task.targets].count(20.0) == 2
        assert [target.points for target in task.targets].count(30.0) == 2
        assert sum(target.points for target in task.targets) == pytest.approx(100.0)
        assert len({target.key for target in task.targets}) == 4
    assert len(selections) >= 100
    assert len(worlds) == 1000
    assert len(layouts) == 1000


def test_build_config_removes_catalog_and_preserves_layout_and_targets() -> None:
    mission, base = mission_and_config()
    task = generate_task(base, 8)
    config = mission.build_mission_config(base, task)
    assert config.menu is None
    assert config.seed == task.minecraft_seed
    assert config.layout_seed == task.layout_seed
    assert config.biome is None
    assert [target.key for target in config.targets] == [target.key for target in task.targets]
    assert sum(target.points for target in config.targets) == pytest.approx(100.0)


def test_prompt_explains_plot_scoped_mature_harvest_scoring() -> None:
    task, _ = built_config()
    assert "empty inventory" in task.prompt
    assert "20 minutes" in task.prompt
    assert "Only mature benchmark-grown harvest units" in task.prompt
    assert "do not score" in task.prompt
    for target in task.targets:
        assert target.display_name in task.prompt
        assert str(target.target_count) in task.prompt


def test_datapack_tracks_mature_and_runtime_generated_targets_differently(tmp_path) -> None:
    _, config = built_config(seed=1)
    pack_dir = write_farming_datapack(tmp_path, config)
    pack = json.loads((pack_dir / "pack.mcmeta").read_text())["pack"]
    assert pack["min_format"] == DATAPACK_FORMAT
    assert pack["max_format"] == DATAPACK_FORMAT
    tick = (pack_dir / "data/npabench/function/farming/tick.mcfunction").read_text()
    for target in config.targets:
        assert f"tag={target.plot_tag}" in tick
        assert target.harvest_holder in tick
        auto_arm = f"tag={target.plot_tag},scores={{{READY_OBJECTIVE}=0}}]" in tick
        assert auto_arm is (target.harvest_mode == "mature_state")


def test_mission_installs_datapack_in_reference_world(tmp_path) -> None:
    mission, config = mission_and_config()
    task = mission.generate_task(config, 3)
    built = mission.build_mission_config(config, task)
    mission.prepare_reference_world(tmp_path, built)
    assert (
        tmp_path / "world/datapacks/npabench_farming/data/minecraft/tags/function/tick.json"
    ).exists()


def test_world_configuration_disables_random_growth_and_mobs() -> None:
    _, config = built_config()
    rcon = FakeRcon()
    configure_farming_world(rcon, config)
    assert "gamerule spawn_mobs false" in rcon.commands
    assert "gamerule random_tick_speed 0" in rcon.commands
    assert "gamerule advance_time false" in rcon.commands
    assert "difficulty peaceful" in rcon.commands


def test_every_crop_template_builds_a_nonempty_marker_plot() -> None:
    _, config = mission_and_config()
    assert config.menu is not None
    for index, key in enumerate(config.menu.crops):
        target = spec_from_menu(key, index, config)
        rcon = FakeRcon()
        plot = _build_crop_plot(rcon, target, index, 0, 70, 0, 7, __import__("random").Random(1))
        assert plot["cells"], key
        marker_commands = [
            command for command in rcon.commands if command.startswith("summon minecraft:marker")
        ]
        assert len(marker_commands) == len(plot["cells"]), key
        assert all(target.plot_tag in command for command in marker_commands)


def test_setup_starts_empty_builds_four_plots_and_supplies_tools() -> None:
    _, config = built_config(seed=1)
    rcon = FakeRcon()
    setup = setup_farming_agent(rcon, config)
    assert "clear npabench_agent" in rcon.commands
    assert "reload" in rcon.commands
    assert len(setup["plots"]) == 4
    assert {plot["target_key"] for plot in setup["plots"]} == {
        target.key for target in config.targets
    }
    assert len({(plot["center_x"], plot["center_z"]) for plot in setup["plots"]}) == 4
    barrel_commands = [
        command for command in rcon.commands if command.startswith("item replace block")
    ]
    assert any("minecraft:stone_hoe 1" in command for command in barrel_commands)
    assert any("minecraft:bucket 1" in command for command in barrel_commands)
    assert not any(command.startswith("give ") for command in rcon.commands)
    assert setup["spawn"] == (2, 70, 2)


def test_final_state_reads_plot_harvest_ledger() -> None:
    _, config = built_config()
    scores = {
        (target.harvest_holder, HARVEST_OBJECTIVE): index + 2
        for index, target in enumerate(config.targets)
    }
    scores[("npabench_agent", "mcb_deaths")] = 3
    snapshot = collect_farming_state(
        FakeRcon(scores=scores),
        config,
        {"death_baseline": 1, "spawn": (0, 70, 0), "plots": [{"index": 0}]},
    )
    assert snapshot["harvests"] == {
        target.key: index + 2 for index, target in enumerate(config.targets)
    }
    assert snapshot["deaths"] == 2
    assert snapshot["plots"] == [{"index": 0}]


def test_full_completion_scores_exactly_100() -> None:
    _, config = built_config()
    report = score_farming_run(config, trace_for(config), full_snapshot(config))
    assert report["score"] == pytest.approx(100.0)
    assert report["max_score"] == 100.0
    assert {row["tier"]: row["score"] for row in report["tiers"]} == pytest.approx(
        {"easy": 40.0, "medium": 60.0}
    )


def test_partial_credit_is_linear_and_overproduction_is_capped() -> None:
    _, config = built_config()
    target = config.targets[0]
    achieved = target.target_count // 2
    report = score_farming_run(
        config,
        trace_for(config),
        {"harvests": {target.key: achieved}, "runtime": {"status": "stopped"}},
    )
    assert report["score"] == pytest.approx(target.points * achieved / target.target_count)
    capped = score_farming_run(
        config,
        trace_for(config),
        {
            "harvests": {target.key: target.target_count + 100},
            "runtime": {"status": "stopped"},
        },
    )
    assert capped["score"] == pytest.approx(target.points)


def test_runtime_failure_invalidates_score() -> None:
    _, config = built_config()
    snapshot = full_snapshot(config)
    snapshot["runtime"] = {"status": "error", "errors": ["datapack failed"]}
    report = score_farming_run(config, trace_for(config), snapshot)
    assert report["score"] == 0.0
    assert report["status"] == "error"


def test_age_growth_runs_in_descending_order() -> None:
    _, config = built_config(seed=0)
    target = next(item for item in config.targets if item.max_age is not None)
    plot = {
        "target_key": target.key,
        "x1": -3,
        "x2": 3,
        "z1": -3,
        "z2": 3,
        "ground_y": 70,
        "cells": [],
    }
    controller = FarmingController(ServerEndpoint(), config, {"plots": [plot]})
    rcon = FakeRcon()
    controller._advance_ages(rcon, target, plot)
    commands = [command for command in rcon.commands if command.startswith("fill ")]
    ages = [
        int(re.search(r"replace minecraft:[a-z_]+\[age=(\d+)\]", command)[1])
        for command in commands
    ]
    assert ages == list(range(int(target.max_age) - 1, -1, -1))


def test_runtime_generated_growth_arms_only_the_cell_marker() -> None:
    _, config = built_config(seed=1)
    target = next(item for item in config.targets if item.harvest_mode == "runtime_generated")
    plot = {
        "target_key": target.key,
        "cells": [
            {
                "tag": "nff_cell_test",
                "pos": (1, 71, 1),
                "source": (1, 70, 1),
            }
        ],
    }
    controller = FarmingController(ServerEndpoint(), config, {"plots": [plot]})
    rcon = FakeRcon()
    controller._generate_cells(rcon, target, plot, source_state=target.planted_block)
    command = rcon.commands[-1]
    assert "tag=nff_cell_test" in command
    assert f"store success score @s {READY_OBJECTIVE}" in command
    assert f"minecraft:{target.generated_block}" in command
