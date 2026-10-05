from __future__ import annotations

from collections import Counter

import pytest
from npabench.evaluation.run_slot import ServerEndpoint
from npabench.evaluation.run_trace import AgentRunTrace, FinalAgentState
from npabench.missions.farming import FarmingMission
from npabench.missions.farming.config_schema import FarmingMissionConfig
from npabench.missions.farming.environment import configure_farming_world, setup_farming_agent
from npabench.missions.farming.final_state import collect_farming_state
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
        self.scores = scores or {}
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
            return f"{holder} has {self.scores.get((holder, objective), 0)} [{objective}]"
        if command.startswith("clear ") and command.endswith(" 0"):
            item = parts[2].removeprefix("minecraft:")
            return f"Found {self.inventory.get(item, 0)} matching item(s) on player npabench_agent"
        if command.endswith(" Pos"):
            return "npabench_agent has the following entity data: [2.5d, 70.0d, -1.5d]"
        if command.endswith(" Health"):
            return "20.0f"
        if command.endswith(" foodLevel"):
            return "18"
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
        "inventory": {target.item: target.target_count for target in config.targets},
        "alive": True,
        "plots": [],
    }


def test_farming_is_registered() -> None:
    assert isinstance(get_mission("farming"), FarmingMission)


def test_default_config_is_random_world_100_point_farming() -> None:
    _, config = mission_and_config()
    assert config.duration_seconds == 1200
    assert config.difficulty == "peaceful"
    assert config.keep_inventory is True
    assert config.generate_structures is False
    assert config.sampling.model_dump() == {"easy_targets": 2, "medium_targets": 2}
    assert config.environment.random_tick_speed == 3
    assert "hub_radius" not in type(config.environment).model_fields
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
    for seed in range(1000):
        task = generate_task(config, seed)
        selections.add(tuple(target.key for target in task.targets))
        worlds.add(task.minecraft_seed)
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


def test_prompt_describes_natural_spawn_and_final_inventory_scoring() -> None:
    task, config = built_config()
    assert "empty inventory" in task.prompt
    assert "natural spawn of a random world" in task.prompt
    assert "supply barrel two blocks east of spawn" in task.prompt
    assert "an empty bucket" in task.prompt
    assert "2x2 water pool four blocks south of spawn" in task.prompt
    assert "No farm or plots are prepared" in task.prompt
    assert "inventory at the end" in task.prompt
    assert "Starter supplies and items collected anywhere" in task.prompt
    assert "no initial-item subtraction" in task.prompt
    assert "containers or on the ground do not count" in task.prompt
    assert "20 minutes" in task.prompt
    assert "natural random-tick mechanics" in task.prompt
    for target in config.targets:
        assert target.display_name in task.prompt
        assert str(target.target_count) in task.prompt
        assert f"minecraft:{target.item}" in task.prompt


def test_mission_needs_no_plugin_or_runtime(tmp_path) -> None:
    mission, config = mission_and_config()
    task = mission.generate_task(config, 3)
    built = mission.build_mission_config(config, task)
    mission.prepare_reference_world(tmp_path, built)
    assert not (tmp_path / "plugins").exists()
    assert mission.start_runtime(ServerEndpoint(), built, {}) is None


def test_setup_does_not_require_harvest_scoreboards() -> None:
    _, config = built_config()
    rcon = FakeRcon()
    setup_farming_agent(rcon, config)
    assert not any("nff_" in command for command in rcon.commands)


def test_world_configuration_preserves_natural_day_weather_and_growth() -> None:
    _, config = built_config()
    rcon = FakeRcon()
    configure_farming_world(rcon, config)
    assert "gamerule advance_time true" in rcon.commands
    assert "gamerule advance_weather true" in rcon.commands
    assert "gamerule random_tick_speed 3" in rcon.commands
    assert "difficulty peaceful" in rcon.commands


def test_setup_builds_only_small_pool_and_supplies_selected_starters() -> None:
    _, config = built_config(seed=1)
    rcon = FakeRcon()
    setup = setup_farming_agent(rcon, config)
    assert "clear npabench_agent" in rcon.commands
    assert setup["spawn"] == (2, 70, -1)
    assert setup["supply_cache"] == (4, 70, -1)
    assert setup["plots"] == []
    assert setup["sources"] == []
    assert setup["water_source"] == {
        "position": (2, 69, 3),
        "size": (2, 2),
        "depth": 2,
        "item": "bucket",
    }
    pool_commands = [command for command in rcon.commands if command.startswith("fill ")]
    assert pool_commands == [
        "fill 1 70 2 4 72 5 air",
        "fill 1 67 2 4 69 5 dirt",
        "fill 2 67 3 3 67 4 sand",
        "fill 2 68 3 3 69 4 water",
        "fill 2 69 0 2 69 2 oak_planks",
        "fill 2 70 0 2 71 2 air",
    ]
    assert not any(command.startswith("summon ") for command in rcon.commands)
    assert any(command.startswith("setblock 4 70 -1 barrel") for command in rcon.commands)
    barrel = [c for c in rcon.commands if c.startswith("item replace block")]
    assert any("minecraft:bucket 1" in command for command in barrel)
    assert not any("minecraft:water_bucket" in command for command in barrel)
    assert any("minecraft:birch_log" in command for command in barrel)
    for target in config.targets:
        assert any(
            f"minecraft:{target.starter_item} {target.starter_count}" in command
            for command in barrel
        )


def test_supply_kit_fits_barrel_across_seed_samples() -> None:
    for seed in range(100):
        _, config = built_config(seed)
        rcon = FakeRcon()
        setup_farming_agent(rcon, config)
        barrel = [c for c in rcon.commands if c.startswith("item replace block")]
        assert len(barrel) <= 27


def test_final_state_reads_inventory_without_harvest_ledger() -> None:
    _, config = built_config()
    scores = {("npabench_agent", "mcb_deaths"): 3}
    inventory = {target.item: index + 2 for index, target in enumerate(config.targets)}
    rcon = FakeRcon(scores=scores, inventory=inventory)
    snapshot = collect_farming_state(
        rcon,
        config,
        {
            "death_baseline": 1,
            "spawn": (0, 70, 0),
            "supply_cache": (2, 70, 0),
            "plots": [],
            "sources": [],
            "water_source": {"item": "bucket", "position": (0, 69, 4)},
        },
    )
    assert snapshot["inventory"] == inventory
    assert snapshot["final_state"].inventory == inventory
    assert "harvests" not in snapshot
    assert not any("nff_" in command for command in rcon.commands)
    assert snapshot["deaths"] == 2
    assert snapshot["plots"] == []
    assert snapshot["supply_cache"] == (2, 70, 0)


def test_full_completion_scores_exactly_100_without_plots() -> None:
    _, config = built_config()
    report = score_farming_run(config, trace_for(config), full_snapshot(config))
    assert report["score"] == pytest.approx(100.0)
    assert report["max_score"] == 100.0
    assert report["scoring_method"] == "final_inventory"
    assert "harvests" not in report
    assert report["plots"] == []
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
        {"inventory": {target.item: achieved}},
    )
    assert report["score"] == pytest.approx(target.points * achieved / target.target_count)
    capped = score_farming_run(
        config,
        trace_for(config),
        {
            "inventory": {target.item: target.target_count + 100},
        },
    )
    assert capped["score"] == pytest.approx(target.points)


def test_snapshot_failure_invalidates_score() -> None:
    _, config = built_config()
    snapshot = full_snapshot(config)
    snapshot["error"] = "snapshot failed: RCON unavailable"
    report = score_farming_run(config, trace_for(config), snapshot)
    assert report["score"] == 0.0
    assert report["status"] == "error"
    assert report["error"] == snapshot["error"]


def test_only_final_inventory_counts_even_with_old_harvest_data() -> None:
    _, config = built_config()
    snapshot = {
        "inventory": {},
        "harvests": {target.key: target.target_count * 10 for target in config.targets},
    }
    report = score_farming_run(config, trace_for(config), snapshot)
    assert report["score"] == 0.0
    target = config.targets[1]
    # Collecting a starter stack counts once; cycling it does not multiply credit.
    assert target.starter_item == target.item
    snapshot["inventory"] = {target.item: target.starter_count}
    first = score_farming_run(config, trace_for(config), snapshot)
    snapshot["harvests"][target.key] += 1000
    repeated = score_farming_run(config, trace_for(config), snapshot)
    assert repeated["score"] == first["score"] == pytest.approx(
        target.points * min(target.starter_count / target.target_count, 1)
    )
    assert repeated["resources"][1]["inventory_count"] == target.starter_count


def test_inventory_items_use_item_identifier_not_crop_key() -> None:
    _, config = built_config()
    target = next(target for target in config.targets if target.key == "mushroom")
    report = score_farming_run(
        config, trace_for(config), {"inventory": {"red_mushroom": target.target_count}}
    )
    assert report["score"] == target.points
    assert report["resources"][2]["achieved"] == target.target_count
