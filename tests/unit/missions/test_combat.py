from __future__ import annotations

from collections import Counter
from contextlib import contextmanager

import pytest

from npabench.evaluation.run_slot import ServerEndpoint
from npabench.evaluation.run_trace import AgentRunTrace, FinalAgentState
from npabench.missions.combat import CombatMission
from npabench.missions.combat.config_schema import TIER_ORDER, CombatMissionConfig
from npabench.missions.combat.environment import setup_combat_agent
from npabench.missions.combat.final_state import collect_combat_state
from npabench.missions.combat.prompting import PROMPT_SCHEMA_VERSION, fallback_prompt
from npabench.missions.combat.scoring import score_combat_run
from npabench.missions.combat.task import CombatTask, generate_task
from npabench.missions.combat.waves import CombatWaveController
from npabench.missions.registry import get_mission


class FakeRcon:
    def __init__(
        self,
        *,
        scores: dict[str, int] | None = None,
        inventory: dict[str, int] | None = None,
        summon_responses: list[str] | None = None,
    ) -> None:
        self.commands: list[str] = []
        self.scores = scores or {}
        self.inventory = inventory or {}
        self.summon_responses = list(summon_responses or [])

    def command(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("scoreboard players get"):
            objective = command.rsplit(" ", 1)[-1]
            return f"npabench_agent has {self.scores.get(objective, 0)} [{objective}]"
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
        if "summon minecraft:" in command:
            return self.summon_responses.pop(0) if self.summon_responses else "Summoned new mob"
        return ""


def mission_and_config() -> tuple[CombatMission, CombatMissionConfig]:
    mission = CombatMission()
    return mission, mission.load_config(mission.default_config_path())


def built_config(seed: int = 42) -> tuple[CombatTask, CombatMissionConfig]:
    mission, base = mission_and_config()
    task = generate_task(base, seed)
    return task, mission.build_mission_config(base, task)


def trace_for(config: CombatMissionConfig) -> AgentRunTrace:
    return AgentRunTrace(
        task_id=config.id,
        agent_name="fighter",
        started_at=0.0,
        agent_ready_at=1.0,
        ended_at=100.0,
        final_state=FinalAgentState(position=(2.5, 70.0, -1.5), health=20.0),
    )


def test_combat_is_registered() -> None:
    assert isinstance(get_mission("combat"), CombatMission)


def test_default_config_has_two_phases_and_exact_tier_points() -> None:
    _, config = mission_and_config()
    assert config.id == "combat"
    assert config.duration_seconds == 900
    assert config.phase.preparation_seconds == 480
    assert config.phase.combat_seconds == 420
    assert config.scoring.death_penalty_points == 5.0
    assert config.scoring.maximum_death_penalty == 25.0
    assert config.duration_seconds == (
        config.phase.preparation_seconds + config.phase.combat_seconds
    )
    assert {tier: config.tier_rules[tier].points for tier in TIER_ORDER} == {
        "easy": 20.0,
        "medium": 35.0,
        "hard": 45.0,
    }
    assert sum(rule.points for rule in config.tier_rules.values()) == 100.0


def test_mob_catalog_matches_approved_tiers() -> None:
    _, config = mission_and_config()
    assert config.menu is not None
    assert set(config.menu["easy"].mobs) == {"zombie", "spider"}
    assert set(config.menu["medium"].mobs) == {"skeleton", "husk", "drowned", "stray"}
    assert set(config.menu["hard"].mobs) == {
        "creeper",
        "cave_spider",
        "witch",
        "enderman",
    }


def test_task_generation_is_deterministic_and_varies_by_seed() -> None:
    _, config = mission_and_config()
    assert generate_task(config, 42) == generate_task(config, 42)
    selections = {
        tuple(target.key for target in generate_task(config, seed).targets) for seed in range(30)
    }
    assert len(selections) >= 10


def test_every_task_has_fixed_tier_shape_counts_and_100_points() -> None:
    _, config = mission_and_config()
    for seed in range(100):
        task = generate_task(config, seed)
        tiers = Counter(target.tier for target in task.targets)
        assert tiers == Counter({"easy": 1, "medium": 2, "hard": 2})
        assert sum(target.points for target in task.targets) == pytest.approx(100.0)
        counts = {
            tier: sum(target.target_count for target in task.targets if target.tier == tier)
            for tier in TIER_ORDER
        }
        assert 6 <= counts["easy"] <= 8
        assert 4 <= counts["medium"] <= 6
        assert 2 <= counts["hard"] <= 4


def test_weighted_count_selection_keeps_each_tier_near_its_budget() -> None:
    _, config = mission_and_config()
    observed: dict[str, list[float]] = {tier: [] for tier in TIER_ORDER}
    for seed in range(1000):
        task = generate_task(config, seed)
        for tier in TIER_ORDER:
            units = sum(
                target.target_count * target.difficulty_weight
                for target in task.targets
                if target.tier == tier
            )
            observed[tier].append(units)

    assert min(observed["easy"]) >= 6.0 and max(observed["easy"]) <= 8.0
    assert min(observed["medium"]) >= 4.6 and max(observed["medium"]) <= 6.3 + 1e-9
    assert min(observed["hard"]) >= 3.0 and max(observed["hard"]) <= 4.5
    assert all(len(set(values)) > 1 for values in observed.values())


def test_waves_spawn_each_targets_reserve_count_and_are_stable() -> None:
    _, config = mission_and_config()
    task = generate_task(config, 9)
    spawned = Counter(
        spawn.target_key for wave in task.waves for spawn in wave.spawns for _ in range(spawn.count)
    )
    assert len(task.waves) == len(config.phase.wave_offsets_seconds) == 4
    for target in task.targets:
        assert spawned[target.key] == target.spawn_count
        assert target.spawn_count > target.target_count
    assert task.waves == generate_task(config, 9).waves


def test_build_config_removes_menu_and_preserves_task() -> None:
    mission, base = mission_and_config()
    task = generate_task(base, 4)
    config = mission.build_mission_config(base, task)
    assert config.id == task.task_id
    assert config.seed == task.minecraft_seed
    assert config.biome is None
    assert config.menu is None
    assert [target.key for target in config.targets] == [target.key for target in task.targets]
    assert config.waves == task.waves


def test_prompt_explains_phases_targets_and_score() -> None:
    task, _ = built_config()
    prompt = fallback_prompt(task)
    assert "first 8 minutes" in prompt
    assert "lasts 7 minutes" in prompt
    assert "exactly 100 points" in prompt
    assert "crafting itself gives no points" in prompt
    assert "Each death subtracts 5 points" in prompt
    assert "maximum death penalty of 25 points" in prompt
    for target in task.targets:
        assert target.display_name in prompt
        assert str(target.target_count) in prompt
    assert PROMPT_SCHEMA_VERSION == "combat.v1"


def test_setup_starts_empty_disables_prep_mobs_and_tracks_kills() -> None:
    _, config = built_config()
    rcon = FakeRcon()
    setup = setup_combat_agent(rcon, config)
    assert any(command == "clear npabench_agent" for command in rcon.commands)
    assert not any(command.startswith("give ") for command in rcon.commands)
    assert len(setup["kill_baselines"]) == len(config.targets)
    for target in config.targets:
        criterion = f"minecraft.killed:minecraft.{target.entity_type}"
        assert any(criterion in command for command in rcon.commands)


def test_final_state_reads_kill_deltas_and_drops() -> None:
    _, config = built_config()
    scores = {target.objective: index + 3 for index, target in enumerate(config.targets)}
    scores["ncb_deaths"] = 2
    inventory = {item: 2 for target in config.targets for item in target.drop_items}
    setup = {
        "kill_baselines": {target.key: 1 for target in config.targets},
        "death_baseline": 1,
        "spawn": (0, 70, 0),
    }
    snapshot = collect_combat_state(
        FakeRcon(scores=scores, inventory=inventory),
        config,
        setup,
    )
    for index, target in enumerate(config.targets):
        assert snapshot["kills"][target.key] == index + 2
    assert snapshot["deaths"] == 1
    assert snapshot["alive"] is True
    assert snapshot["drops"] == inventory


def test_full_kills_score_exactly_100() -> None:
    _, config = built_config()
    kills = {target.key: target.target_count for target in config.targets}
    report = score_combat_run(config, trace_for(config), {"kills": kills, "alive": True})
    assert report["score"] == pytest.approx(100.0)
    assert report["max_score"] == 100.0
    assert {tier["tier"]: tier["score"] for tier in report["tiers"]} == pytest.approx(
        {"easy": 20.0, "medium": 35.0, "hard": 45.0}
    )
    assert sum(row["points"] for row in report["resources"]) == pytest.approx(report["score"])


def test_partial_kills_are_linear_and_overproduction_is_capped() -> None:
    _, config = built_config()
    easy = next(target for target in config.targets if target.tier == "easy")
    kills = {target.key: 0 for target in config.targets}
    kills[easy.key] = easy.target_count // 2
    report = score_combat_run(config, trace_for(config), {"kills": kills})
    expected = 20.0 * (easy.target_count // 2) / easy.target_count
    assert report["score"] == pytest.approx(expected)

    kills[easy.key] = easy.target_count + 100
    capped = score_combat_run(config, trace_for(config), {"kills": kills})
    assert capped["score"] == pytest.approx(20.0)


def test_non_target_drops_and_distance_do_not_change_kill_score() -> None:
    _, config = built_config()
    target = config.targets[0]
    kills = {target.key: target.target_count}
    report = score_combat_run(
        config,
        trace_for(config),
        {
            "kills": kills,
            "drops": {"diamond": 64},
            "deaths": 0,
            "alive": False,
            "distance_from_spawn": 10000,
        },
    )
    assert report["score"] == pytest.approx(config.tier_rules[target.tier].points)


@pytest.mark.parametrize(
    ("deaths", "expected_penalty", "expected_score"),
    [(0, 0.0, 100.0), (1, 5.0, 95.0), (2, 10.0, 90.0), (5, 25.0, 75.0), (7, 25.0, 75.0)],
)
def test_death_penalty_is_five_points_and_capped_at_25(
    deaths: int,
    expected_penalty: float,
    expected_score: float,
) -> None:
    _, config = built_config()
    kills = {target.key: target.target_count for target in config.targets}
    report = score_combat_run(config, trace_for(config), {"kills": kills, "deaths": deaths})
    assert report["kill_score"] == pytest.approx(100.0)
    assert report["death_penalty"] == pytest.approx(expected_penalty)
    assert report["score"] == pytest.approx(expected_score)


def test_death_penalty_cannot_make_score_negative() -> None:
    _, config = built_config()
    easy = next(target for target in config.targets if target.tier == "easy")
    report = score_combat_run(
        config,
        trace_for(config),
        {"kills": {easy.key: easy.target_count}, "deaths": 5},
    )
    assert report["kill_score"] == pytest.approx(20.0)
    assert report["death_penalty"] == pytest.approx(25.0)
    assert report["score"] == 0.0


def test_runtime_error_fails_closed() -> None:
    _, config = built_config()
    kills = {target.key: target.target_count for target in config.targets}
    report = score_combat_run(
        config,
        trace_for(config),
        {"kills": kills, "runtime": {"status": "error", "errors": ["RCON failed"]}},
    )
    assert report["score"] == 0.0
    assert report["status"] == "error"


def test_never_spawned_fails_closed_even_with_kill_snapshot() -> None:
    _, config = built_config()
    trace = trace_for(config)
    trace.agent_ready_at = None
    kills = {target.key: target.target_count for target in config.targets}
    report = score_combat_run(config, trace, {"kills": kills})
    assert report["score"] == 0.0
    assert report["status"] == "agent_never_spawned"


def test_spawn_command_uses_player_relative_safe_position_and_tags() -> None:
    _, config = built_config()
    controller = CombatWaveController(ServerEndpoint(), config)
    spawn = config.waves[0].spawns[0]
    rcon = FakeRcon(summon_responses=["No blocks passed", "Summoned new mob"])
    assert controller._spawn_one(rcon, spawn, *spawn.offsets[0]) is True
    assert len(rcon.commands) == 2
    assert f"at {config.username}" in rcon.commands[0]
    assert "npabench_wave" in rcon.commands[0]
    assert "positioned over motion_blocking_no_leaves" in rcon.commands[1]


def test_controller_combat_transition_enables_natural_mobs(monkeypatch) -> None:
    _, config = built_config()
    rcon = FakeRcon()

    @contextmanager
    def fake_session(*args, **kwargs):
        yield rcon

    monkeypatch.setattr("npabench.missions.combat.waves.rcon_session", fake_session)
    controller = CombatWaveController(ServerEndpoint(), config)
    controller._begin_combat()
    assert "time set midnight" in rcon.commands
    assert "gamerule spawn_mobs true" in rcon.commands
    assert controller.report()["status"] == "combat"


def test_controller_fails_closed_when_completed_waves_have_spawn_shortfall() -> None:
    _, config = built_config()
    controller = CombatWaveController(ServerEndpoint(), config)
    controller._events = [{"kind": "wave"} for _ in config.waves]
    controller._spawned_by_target = {target.key: target.target_count for target in config.targets}
    short_target = config.targets[0]
    controller._spawned_by_target[short_target.key] = short_target.target_count - 1

    controller._validate_spawn_guarantees()

    report = controller.report()
    assert report["status"] == "error"
    assert "controlled wave spawn shortfall" in report["errors"][0]
