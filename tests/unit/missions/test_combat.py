from __future__ import annotations

from collections import Counter
from contextlib import contextmanager

import pytest

from npabench.evaluation.run_slot import ServerEndpoint
from npabench.evaluation.run_trace import AgentRunTrace, FinalAgentState
from npabench.missions.combat import CombatMission
from npabench.missions.combat.config_schema import TIER_ORDER, CombatMissionConfig
from npabench.missions.combat.environment import configure_combat_world, setup_combat_agent
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
        self.runtime_scores: dict[tuple[str, str], int] = {}

    def command(self, command: str) -> str:
        self.commands.append(command)
        if command.startswith("scoreboard players set") and command.split()[4] == "ncb_live":
            holder, objective, value = command.split()[3:6]
            self.runtime_scores[holder, objective] = int(value)
            return "Set score"
        if command.startswith("scoreboard players get"):
            holder, objective = command.split()[3:5]
            value = self.runtime_scores.get((holder, objective), self.scores.get(objective, 0))
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
        if "summon minecraft:" in command:
            response = self.summon_responses.pop(0) if self.summon_responses else "Summoned new mob"
            if command.startswith("execute store success score"):
                holder, objective = command.split()[4:6]
                self.runtime_scores[holder, objective] = int("Summoned" in response)
            return response
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
    assert config.duration_seconds == 1080
    assert config.phase.preparation_seconds == 600
    assert config.phase.combat_seconds == 480
    assert config.phase.max_active_mobs == 3
    assert config.keep_inventory is True
    assert config.phase.spawn_mobs_naturally is False
    assert config.phase.wave_tiers == [
        ["easy"],
        ["easy"],
        ["easy"],
        ["medium"],
        ["medium"],
        ["medium"],
        ["hard"],
        ["hard"],
    ]
    assert config.scoring.death_penalty_points == 10.0
    assert config.duration_seconds == (
        config.phase.preparation_seconds + config.phase.combat_seconds
    )
    assert {tier: config.tier_rules[tier].points for tier in TIER_ORDER} == {
        "easy": 30.0,
        "medium": 40.0,
        "hard": 30.0,
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
    assert {
        tier: {entry.spawn_reserve_multiplier for entry in config.menu[tier].mobs.values()}
        for tier in TIER_ORDER
    } == {"easy": {1.25}, "medium": {1.5}, "hard": {1.5}}


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
    assert len(task.waves) == len(config.phase.wave_offsets_seconds) == 8
    for target in task.targets:
        assert spawned[target.key] == target.spawn_count
        assert target.spawn_count > target.target_count
    assert [{spawn.tier for spawn in wave.spawns} for wave in task.waves] == [
        {"easy"},
        {"easy"},
        {"easy"},
        {"medium"},
        {"medium"},
        {"medium"},
        {"hard"},
        {"hard"},
    ]
    assert task.waves == generate_task(config, 9).waves


def test_build_config_removes_menu_and_preserves_task() -> None:
    mission, base = mission_and_config()
    task = generate_task(base, 4)
    config = mission.build_mission_config(base, task)
    assert config.id == task.task_id
    assert config.seed == task.minecraft_seed
    assert config.biome is None
    assert config.menu is None
    assert config.keep_inventory is True
    assert config.phase.spawn_mobs_naturally is False
    assert [target.key for target in config.targets] == [target.key for target in task.targets]
    assert config.waves == task.waves


def test_prompt_briefly_introduces_preparation_and_kill_objectives() -> None:
    task, _ = built_config()
    prompt = fallback_prompt(task)
    assert prompt.startswith(("Prepare your gear", "Gather resources", "Get equipped for battle"))
    assert "kill " in prompt
    assert "waves" in prompt
    assert "stay alive until the mission ends" in prompt
    assert prompt.endswith(".")
    assert "\n" not in prompt
    assert len(prompt.split()) <= 55
    for target in task.targets:
        name = target.display_name
        if target.target_count != 1:
            name = {"Drowned": "Drowned", "Witch": "Witches", "Enderman": "Endermen"}.get(
                name, f"{name}s"
            )
        assert f"{target.target_count} {name}" in prompt
    assert PROMPT_SCHEMA_VERSION == "combat.v5"


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


def test_world_keeps_inventory_and_disables_natural_spawning() -> None:
    _, config = built_config()
    rcon = FakeRcon()
    configure_combat_world(rcon, config)
    assert "gamerule keep_inventory true" in rcon.commands
    assert "gamerule spawn_mobs false" in rcon.commands


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
        {"easy": 30.0, "medium": 40.0, "hard": 30.0}
    )
    assert sum(row["points"] for row in report["resources"]) == pytest.approx(report["score"])


def test_easy_and_medium_completion_with_two_deaths_scores_fifty() -> None:
    _, config = built_config()
    kills = {
        target.key: target.target_count
        for target in config.targets
        if target.tier in {"easy", "medium"}
    }
    report = score_combat_run(config, trace_for(config), {"kills": kills, "deaths": 2})
    assert report["kill_score"] == pytest.approx(70)
    assert report["death_penalty"] == pytest.approx(20)
    assert report["score"] == pytest.approx(50)


def test_runtime_receives_setup_kill_baselines(monkeypatch) -> None:
    mission, _ = mission_and_config()
    _, config = built_config()
    setup = {"kill_baselines": {config.targets[0].key: 7}}
    captured = {}

    class Runtime:
        def __init__(self, endpoint, mission_config, *, setup_state):
            captured["endpoint"] = endpoint
            captured["config"] = mission_config
            captured["setup"] = setup_state

        def start(self):
            return self

    monkeypatch.setattr("npabench.missions.combat.mission.CombatWaveController", Runtime)
    endpoint = ServerEndpoint()
    mission.start_runtime(endpoint, config, setup)
    assert captured["setup"] is setup
    assert captured["config"].phase.max_active_mobs == 3
    assert captured["endpoint"] == endpoint


def test_partial_kills_are_linear_and_overproduction_is_capped() -> None:
    _, config = built_config()
    easy = next(target for target in config.targets if target.tier == "easy")
    kills = {target.key: 0 for target in config.targets}
    kills[easy.key] = easy.target_count // 2
    report = score_combat_run(config, trace_for(config), {"kills": kills})
    expected = 30.0 * (easy.target_count // 2) / easy.target_count
    assert report["score"] == pytest.approx(expected)

    kills[easy.key] = easy.target_count + 100
    capped = score_combat_run(config, trace_for(config), {"kills": kills})
    assert capped["score"] == pytest.approx(30.0)


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
    [
        (0, 0.0, 100.0),
        (1, 10.0, 90.0),
        (2, 20.0, 80.0),
        (5, 50.0, 50.0),
        (10, 100.0, 0.0),
        (12, 100.0, 0.0),
    ],
)
def test_death_penalty_is_ten_points_until_score_reaches_zero(
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
    assert report["kill_score"] == pytest.approx(30.0)
    assert report["death_penalty"] == pytest.approx(30.0)
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
    rcon = FakeRcon(summon_responses=["", "Summoned new mob"])
    assert controller._spawn_one(rcon, spawn, *spawn.offsets[0]) is True
    summons = [command for command in rcon.commands if "summon minecraft:" in command]
    assert len(summons) == 2
    assert f"at {config.username}" in summons[0]
    assert "npabench_wave" in summons[0]
    assert "positioned over motion_blocking_no_leaves" in summons[1]


def test_controller_combat_transition_keeps_natural_mobs_disabled(monkeypatch) -> None:
    _, config = built_config()
    rcon = FakeRcon()

    @contextmanager
    def fake_session(*args, **kwargs):
        yield rcon

    monkeypatch.setattr("npabench.missions.combat.waves.rcon_session", fake_session)
    controller = CombatWaveController(ServerEndpoint(), config)
    controller._begin_combat()
    assert "time set midnight" in rcon.commands
    assert "gamerule spawn_mobs false" in rcon.commands
    assert controller.report()["status"] == "combat"


def test_controller_fails_closed_when_failed_summons_cause_spawn_shortfall() -> None:
    _, config = built_config()
    controller = CombatWaveController(ServerEndpoint(), config)
    controller._events = [{"kind": "wave"} for _ in config.waves]
    controller._released_waves = {wave.index for wave in config.waves}
    controller._spawned_by_target = {target.key: target.target_count for target in config.targets}
    short_target = config.targets[0]
    controller._spawned_by_target[short_target.key] = short_target.target_count - 1
    controller._failed_by_target[short_target.key] = 1

    controller._validate_spawn_guarantees()

    report = controller.report()
    assert report["status"] == "error"
    assert "controlled wave spawn shortfall" in report["errors"][0]
