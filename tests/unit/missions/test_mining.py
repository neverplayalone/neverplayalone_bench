from __future__ import annotations

from npabench.evaluation.run_trace import AgentRunTrace, FinalAgentState, TraceEvent
from npabench.missions.mining import MiningMission
from npabench.missions.mining.config_schema import MiningMissionConfig, ResourceSpec
from npabench.missions.mining.environment import ORE_META, configure_mining_world
from npabench.missions.mining.scoring import distance_from_spawn_3d, score_mining_run
from npabench.missions.mining.task import ESSENTIAL_TARGET_KEYS, generate_task
from npabench.missions.registry import get_mission


# --- config + task generation ------------------------------------------------

def test_mining_is_registered() -> None:
    assert isinstance(get_mission("mining"), MiningMission)


def test_bundled_config_loads() -> None:
    mission = MiningMission()
    config = mission.load_config(mission.default_config_path())
    assert config.id == "mining"
    assert config.minecraft_version == "1.21.11"
    assert config.menu is not None
    assert {"coal", "iron", "gold"} <= set(config.menu.resources)
    assert config.deposit.enabled is False  # natural ores by default


def test_generate_task_is_deterministic() -> None:
    mission = MiningMission()
    config = mission.load_config(mission.default_config_path())
    first = generate_task(config, seed=7)
    second = generate_task(config, seed=7)
    assert first == second
    assert len(first.targets) == 5


def test_generate_task_has_three_essentials_two_optionals_all_20_points() -> None:
    mission = MiningMission()
    config = mission.load_config(mission.default_config_path())
    task = generate_task(config, seed=13)
    keys = [t.key for t in task.targets]

    assert keys[:3] == list(ESSENTIAL_TARGET_KEYS) == ["coal", "iron", "gold"]
    assert len(task.targets) == 5
    assert len(set(keys)) == 5
    assert all(t.role == "essential" for t in task.targets[:3])
    assert all(t.role == "optional" for t in task.targets[3:])
    assert set(keys[3:]) <= {"redstone", "diamond", "lapis", "copper"}
    # every target is worth 20 -> a clean 100-point ceiling
    assert all(t.points == 20.0 for t in task.targets)
    assert sum(t.points for t in task.targets) == 100.0


def test_gold_target_count_is_small() -> None:
    mission = MiningMission()
    config = mission.load_config(mission.default_config_path())
    # gold is a rare essential -> small guaranteed count across many seeds
    for seed in range(50):
        gold = next(t for t in generate_task(config, seed=seed).targets if t.key == "gold")
        assert 2 <= gold.target_count <= 4


def test_build_mission_config_expands_targets() -> None:
    mission = MiningMission()
    base = mission.load_config(mission.default_config_path())
    task = generate_task(base, seed=3)
    config = mission.build_mission_config(base, task)

    assert config.id == task.task_id
    assert config.seed == task.minecraft_seed
    assert len(config.resources) == 5
    assert config.menu is None  # catalog dropped from the built config
    assert config.deposit.enabled is False  # deposit settings carry through


# --- scoring ------------------------------------------------------------------

def _config_with_targets() -> MiningMissionConfig:
    return MiningMissionConfig(
        id="mining_test",
        duration_seconds=720,
        resources=[
            ResourceSpec(item="coal", items=["coal"], target_count=20, points=20, role="essential"),
            ResourceSpec(item="iron", items=["raw_iron"], target_count=10, points=20, role="essential"),
            ResourceSpec(item="gold", items=["raw_gold"], target_count=3, points=20, role="essential"),
            ResourceSpec(item="diamond", items=["diamond"], target_count=3, points=20, role="optional"),
            ResourceSpec(item="redstone", items=["redstone"], target_count=12, points=20, role="optional"),
        ],
    )


def _trace(inventory: dict[str, int], position, *, done: bool = True) -> AgentRunTrace:
    trace = AgentRunTrace(
        task_id="mining_test",
        agent_name="miner",
        started_at=0.0,
        agent_ready_at=1.0,
        ended_at=300.0,
        final_state=FinalAgentState(inventory=dict(inventory), position=position, health=20.0),
    )
    if done:
        trace.events.append(TraceEvent(kind="done"))
    return trace


def test_max_score_is_100_and_essentials_only_scores_60() -> None:
    config = _config_with_targets()
    essentials = {"coal": 20, "iron": 10, "gold": 3}
    report = score_mining_run(
        config,
        _trace(essentials, (5.0, 64.0, 0.0)),
        final_snapshot={"alive": True, "deaths": 0, "distance_from_spawn": 5.0},
    )
    assert report["max_score"] == 100
    assert report["score"] == 60.0  # 3 essentials * 20, surfaced (x1.0)


def test_full_haul_surfaced_scores_100() -> None:
    config = _config_with_targets()
    full = {"coal": 20, "iron": 10, "gold": 3, "diamond": 3, "redstone": 12}
    report = score_mining_run(
        config,
        _trace(full, (3.0, 64.0, 0.0)),
        final_snapshot={"alive": True, "deaths": 0, "distance_from_spawn": 3.0},
    )
    assert report["score"] == 100.0
    assert report["distance_multiplier"] == 1.0


def test_full_haul_still_underground_is_penalized_by_3d_return() -> None:
    # The mining-specific rule: distance is 3D, so an agent that mined everything
    # but never climbed back out is penalized for not surfacing.
    config = _config_with_targets()
    full = {"coal": 20, "iron": 10, "gold": 3, "diamond": 3, "redstone": 12}
    deep = distance_from_spawn_3d((3.0, -55.0, 0.0), (0, 64, 0))
    assert deep is not None and deep > 100  # ~119 blocks below spawn
    report = score_mining_run(
        config,
        _trace(full, (3.0, -55.0, 0.0)),
        final_snapshot={"alive": True, "deaths": 0, "distance_from_spawn": deep},
    )
    assert report["distance_multiplier"] < 1.0
    assert report["score"] < 100.0  # mined it all, but didn't surface


def test_distance_from_spawn_3d_includes_vertical() -> None:
    assert distance_from_spawn_3d(None, (0, 64, 0)) is None
    assert distance_from_spawn_3d((3.0, 64.0, 4.0), (0, 64, 0)) == 5.0  # horizontal only
    below = distance_from_spawn_3d((0.0, 4.0, 0.0), (0, 64, 0))
    assert below == 60.0  # straight down counts fully


# --- deterministic deposit placement -----------------------------------------

class _RecordingRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return ""


def test_deposit_disabled_places_no_ores() -> None:
    mission = MiningMission()
    base = mission.load_config(mission.default_config_path())
    config = mission.build_mission_config(base, generate_task(base, seed=1))
    rcon = _RecordingRcon()
    configure_mining_world(rcon, config)
    assert not any(command.startswith("fill ") for command in rcon.commands)


def test_deposit_enabled_places_one_vein_per_target() -> None:
    mission = MiningMission()
    base = mission.load_config(mission.default_config_path())
    config = mission.build_mission_config(base, generate_task(base, seed=42))
    config = config.model_copy(update={"deposit": config.deposit.model_copy(update={"enabled": True})})

    rcon = _RecordingRcon()
    configure_mining_world(rcon, config)
    fills = [command for command in rcon.commands if command.startswith("fill ")]

    assert len(fills) == len(config.resources)
    for fill in fills:
        assert "minecraft:" in fill and "_ore" in fill
    # every target ore has placement metadata
    for resource in config.resources:
        assert resource.item in ORE_META


def test_deposit_placement_is_deterministic() -> None:
    mission = MiningMission()
    base = mission.load_config(mission.default_config_path())
    config = mission.build_mission_config(base, generate_task(base, seed=99))
    config = config.model_copy(update={"deposit": config.deposit.model_copy(update={"enabled": True})})

    first = _RecordingRcon()
    second = _RecordingRcon()
    configure_mining_world(first, config)
    configure_mining_world(second, config)
    assert first.commands == second.commands
