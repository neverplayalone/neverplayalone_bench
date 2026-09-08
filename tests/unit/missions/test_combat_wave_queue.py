from __future__ import annotations

import re
from collections import Counter
from contextlib import contextmanager
from typing import Callable

import pytest

from npabench.evaluation.run_slot import ServerEndpoint
from npabench.missions.combat import CombatMission
from npabench.missions.combat.config_schema import CombatMissionConfig, CombatWave
from npabench.missions.combat.task import generate_task
from npabench.missions.combat.waves import (
    LIVE_OBJECTIVE,
    MAX_SPAWN_ATTEMPTS,
    CombatWaveController,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.after_wait: Callable[[], None] = lambda: None

    def monotonic(self) -> float:
        return self.now


class SimulatedEvent:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.stopped = False

    def is_set(self) -> bool:
        return self.stopped

    def set(self) -> None:
        self.stopped = True

    def wait(self, duration: float) -> bool:
        if not self.stopped:
            self.clock.now += duration
            self.clock.after_wait()
        return self.stopped


class QueueRcon:
    def __init__(self, config: CombatMissionConfig, clock: Clock) -> None:
        self.config = config
        self.clock = clock
        self.commands: list[str] = []
        self.scores: dict[tuple[str, str], int] = {}
        self.alive: Counter[str] = Counter()
        self.dying: Counter[str] = Counter()
        self.spawned: list[tuple[float, str]] = []
        self.peak = 0
        self.fail_summons = False
        self.fail_local_guard_empty = False
        self.mute_success_feedback = False
        self.summon_reply: str | None = None
        self.count_reply: str | None = None
        self.kill_reply: str | None = None
        self.ignore_count_store = False
        self.on_summon: Callable[[], None] = lambda: None

    def kill_one(self, *, credited: bool = True) -> None:
        key = next((key for key, count in self.alive.items() if count > 0), None)
        if key is None:
            return
        self.alive[key] -= 1
        self.dying[key] += 1
        if credited:
            target = next(target for target in self.config.targets if target.key == key)
            score = (self.config.username, target.objective)
            self.scores[score] = self.scores.get(score, 0) + 1

    def command(self, command: str) -> str:
        self.commands.append(command)
        parts = command.split()
        if command.startswith("scoreboard players set"):
            self.scores[(parts[3], parts[4])] = int(parts[5])
            return "Set score"
        if command.startswith("scoreboard players add"):
            score = (parts[3], parts[4])
            self.scores[score] = self.scores.get(score, 0) + int(parts[5])
            return "Added score"
        if command.startswith("scoreboard players get"):
            holder, objective = parts[3:5]
            value = self.scores.get((holder, objective))
            if objective == LIVE_OBJECTIVE and value != -1 and self.count_reply is not None:
                return self.count_reply
            if objective != LIVE_OBJECTIVE and self.kill_reply is not None:
                return self.kill_reply
            if value is None:
                return f"Can't get value of {objective} for {holder}; none is set"
            return f"{holder} has {value} [{objective}]"
        if command.startswith("execute store result score"):
            assert "nbt={DeathTime:0s}" in command
            assert "tag=npabench_wave" in command
            assert "distance=" not in command
            holder, objective = parts[4:6]
            match = re.search(r"tag=ncb_([a-z0-9_]+)", command)
            count = self.alive[match[1]] if match else sum(self.alive.values())
            if not self.ignore_count_store:
                self.scores[(holder, objective)] = count
            return f"Test passed, count: {count}" if count else "Test failed"
        if "summon minecraft:" in command:
            self.on_summon()
            if self.summon_reply is not None:
                return self.summon_reply
            assert command.startswith("execute store success score #summon ncb_live run ")
            if self.fail_local_guard_empty and "if block" in command:
                # Paper returns empty and does not invoke store success for zero branches.
                return ""
            if self.fail_summons:
                self.scores[("#summon", LIVE_OBJECTIVE)] = 0
                return "Unable to summon entity"
            key = re.search(r'"ncb_(\w+)"\],PersistenceRequired', command)[1]
            self.alive[key] += 1
            self.peak = max(self.peak, sum(self.alive.values()))
            self.spawned.append((self.clock.now, key))
            self.scores[("#summon", LIVE_OBJECTIVE)] = 1
            return "" if self.mute_success_feedback else "Summoned new mob"
        return ""


def harness(monkeypatch, seed: int = 0, *, baselines: bool = False):
    mission = CombatMission()
    base = mission.load_config(mission.default_config_path())
    config = mission.build_mission_config(base, generate_task(base, seed))
    clock = Clock()
    rcon = QueueRcon(config, clock)
    setup = {"kill_baselines": {target.key: 7 for target in config.targets}} if baselines else {}
    if baselines:
        for target in config.targets:
            rcon.scores[(config.username, target.objective)] = 7
    controller = CombatWaveController(ServerEndpoint(), config, setup)
    controller._stop = SimulatedEvent(clock)

    @contextmanager
    def session():
        yield rcon

    monkeypatch.setattr(controller, "_rcon", session)
    monkeypatch.setattr("npabench.missions.combat.waves.time.monotonic", clock.monotonic)
    monkeypatch.setattr("npabench.missions.combat.waves.time.time", clock.monotonic)
    return controller, rcon, clock


@pytest.mark.parametrize("seed", [0, 1, 42, 81])
def test_complete_targets_without_exceeding_cap(monkeypatch, seed: int) -> None:
    controller, rcon, clock = harness(monkeypatch, seed, baselines=True)
    clock.after_wait = rcon.kill_one
    controller._run()
    report = controller.report()
    assert report["status"] == "complete"
    assert report["errors"] == []
    assert report["max_active_mobs"] == 3
    assert rcon.peak == report["peak_active_mobs"] <= 3
    assert report["pending_mobs"] == 0
    assert report["target_shortfalls"] == {}
    assert report["spawned_by_target"] == {
        target.key: target.target_count for target in controller.config.targets
    }
    assert report["queued_mobs"] == report["spawned_mobs"] + report["fulfilled_skips"]
    assert report["fulfilled_skips"] > 0
    assert report["released_waves"] == report["scheduled_waves"] == 8
    assert 1 <= report["spawned_waves"] <= report["released_waves"]
    assert clock.now == controller.config.duration_seconds
    assert not any(command.startswith("kill ") for command in rcon.commands)


def test_no_combat_progress_leaves_deferred_jobs_not_runtime_error(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    controller._run()
    report = controller.report()
    assert report["status"] == "complete"
    assert report["errors"] == []
    assert report["spawned_mobs"] == rcon.peak == 3
    assert report["pending_mobs"] == report["queued_mobs"] - 3
    assert sum(report["deferred_by_target"].values()) == report["pending_mobs"]
    assert report["failed_spawns"] == 0
    assert len(report["target_shortfalls"]) == len(controller.config.targets)
    assert report["released_waves"] == 8
    assert report["spawned_waves"] < report["released_waves"]


def test_quota_covered_reserve_does_not_block_other_targets(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    controller._begin_combat()
    for wave in controller.config.waves:
        controller._spawn_wave(wave)
    first = controller.config.targets[0]
    rcon.scores[(controller.config.username, first.objective)] = first.target_count - 1
    rcon.alive[first.key] = 1
    controller._drain_pending()
    assert all(key != first.key for _, key in rcon.spawned)
    assert len(rcon.spawned) == 2
    assert controller.report()["pending_by_target"][first.key] == first.spawn_count
    assert controller.report()["fulfilled_skips"] == 0
    rcon.kill_one()
    controller._drain_pending()
    assert first.key not in controller.report()["pending_by_target"]
    assert controller.report()["fulfilled_skips_by_target"][first.key] == first.spawn_count


def test_uncredited_death_releases_capacity_and_uses_pending_reserve(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    controller._begin_combat()
    first = controller.config.targets[0]
    rcon.scores[(controller.config.username, first.objective)] = first.target_count - 1
    for wave in controller.config.waves:
        if wave.spawns and wave.spawns[0].tier == "easy":
            controller._spawn_wave(wave)
    controller._drain_pending()
    assert len(rcon.spawned) == 1
    pending = controller.report()["pending_mobs"]
    rcon.kill_one(credited=False)
    assert sum(rcon.dying.values()) == 1
    controller._drain_pending()
    assert len(rcon.spawned) == 2
    assert controller.report()["pending_mobs"] == pending - 1
    assert rcon.peak == 1
    assert controller.report()["fulfilled_skips"] == 0


def test_staged_release_times_prevent_early_tiers(monkeypatch) -> None:
    controller, rcon, clock = harness(monkeypatch)
    clock.after_wait = rcon.kill_one
    controller._run()
    release = {
        tier: controller.config.phase.preparation_seconds
        + min(
            wave.offset_seconds
            for wave in controller.config.waves
            if any(spawn.tier == tier for spawn in wave.spawns)
        )
        for tier in ("easy", "medium", "hard")
    }
    targets = {target.key: target for target in controller.config.targets}
    for at, key in rcon.spawned:
        assert release[targets[key].tier] <= at < controller.config.duration_seconds
    events = [event for event in controller.report()["events"] if event["kind"] == "wave"]
    assert [event["wave_index"] for event in events] == [
        wave.index for wave in controller.config.waves
    ]
    assert [event["at"] for event in events] == [
        controller.config.phase.preparation_seconds + wave.offset_seconds
        for wave in controller.config.waves
    ]


@pytest.mark.parametrize("reply", ["", "Unknown command", "#active has -2 [ncb_live]", "0"])
def test_malformed_or_negative_alive_count_fails_closed(monkeypatch, reply: str) -> None:
    controller, rcon, _ = harness(monkeypatch)
    rcon.count_reply = reply
    controller._run()
    assert controller.report()["status"] == "error"
    assert controller.report()["errors"]
    assert rcon.spawned == []


def test_failed_count_store_cannot_reuse_previous_zero(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    rcon.scores[("#active", LIVE_OBJECTIVE)] = 0
    rcon.ignore_count_store = True
    controller._run()
    assert controller.report()["status"] == "error"
    assert rcon.spawned == []


def test_unreadable_kill_objective_cannot_allow_surplus(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    rcon.kill_reply = "No value"
    controller._run()
    assert controller.report()["status"] == "error"
    assert rcon.spawned == []


def test_confirmed_summon_failures_retry_at_most_three_times_per_job(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    rcon.fail_summons = True
    controller._run()
    report = controller.report()
    assert report["status"] == "error"
    assert "controlled wave spawn shortfall" in report["errors"][0]
    assert report["failed_spawns"] == report["queued_mobs"]
    assert report["spawn_attempt_failures"] == report["queued_mobs"] * MAX_SPAWN_ATTEMPTS
    assert sum("summon minecraft:" in command for command in rcon.commands) == (
        report["queued_mobs"] * MAX_SPAWN_ATTEMPTS * 2
    )
    assert all(job.attempts == MAX_SPAWN_ATTEMPTS for job in controller._pending)


def test_ambiguous_summon_response_does_not_issue_duplicate_fallback(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    rcon.summon_reply = "Malformed response"
    controller._run()
    assert controller.report()["status"] == "error"
    assert sum("summon minecraft:" in command for command in rcon.commands) == 1


def test_empty_guard_feedback_with_verified_zero_safely_uses_surface_fallback(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    controller._begin_combat()
    rcon.fail_local_guard_empty = True
    spawn = controller.config.waves[0].spawns[0]
    assert controller._spawn_one(rcon, spawn, *spawn.offsets[0]) is True
    commands = [command for command in rcon.commands if "summon minecraft:" in command]
    assert len(commands) == 2
    assert "if block" in commands[0]
    assert "positioned over motion_blocking_no_leaves" in commands[1]
    assert len(rcon.spawned) == 1


def test_success_latch_with_empty_feedback_prevents_duplicate_fallback(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    controller._begin_combat()
    rcon.mute_success_feedback = True
    spawn = controller.config.waves[0].spawns[0]
    assert controller._spawn_one(rcon, spawn, *spawn.offsets[0]) is True
    assert len(rcon.spawned) == 1
    assert sum("summon minecraft:" in command for command in rcon.commands) == 1


def test_textual_success_without_stored_success_fails_closed(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    rcon.summon_reply = "Summoned new mob"
    controller._run()
    assert controller.report()["status"] == "error"
    assert "inconsistent controlled summon result" in controller.report()["errors"][0]
    assert sum("summon minecraft:" in command for command in rcon.commands) == 1


def test_unverifiable_summon_result_fails_closed_even_with_empty_feedback(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    rcon.summon_reply = ""
    rcon.on_summon = lambda: rcon.scores.update({("#summon", LIVE_OBJECTIVE): -1})
    controller._run()
    assert controller.report()["status"] == "error"
    assert "could not verify controlled summon result" in controller.report()["errors"][0]
    assert sum("summon minecraft:" in command for command in rcon.commands) == 1


def test_summon_timeout_is_not_blindly_retried(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)

    def timeout():
        raise TimeoutError("response lost after possible summon")

    rcon.on_summon = timeout
    controller._run()
    assert controller.report()["status"] == "error"
    assert sum("summon minecraft:" in command for command in rcon.commands) == 1


def test_confirmed_transient_failure_is_retried_on_later_poll(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    controller._begin_combat()
    controller._spawn_wave(controller.config.waves[0])
    rcon.fail_summons = True
    controller._drain_pending()
    assert rcon.spawned == []
    assert all(job.attempts == 1 for job in controller._pending)
    rcon.fail_summons = False
    controller._drain_pending()
    assert len(rcon.spawned) == min(3, controller.report()["queued_mobs"])
    assert controller.report()["failed_spawns"] == 0
    assert controller.report()["errors"] == []


@pytest.mark.parametrize("end", ["stop", "deadline"])
def test_stop_or_deadline_blocks_next_summon_in_same_drain(monkeypatch, end: str) -> None:
    controller, rcon, clock = harness(monkeypatch)
    controller._begin_combat()
    controller._combat_deadline = 10.0
    controller._spawn_wave(controller.config.waves[0])

    def finish():
        if end == "stop":
            controller._stop.set()
        else:
            clock.now = 10.0

    rcon.on_summon = finish
    controller._drain_pending()
    assert len(rcon.spawned) == 1
    controller._drain_pending()
    assert len(rcon.spawned) == 1


def test_stop_between_failed_local_summon_and_fallback(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    controller._begin_combat()
    controller._spawn_wave(controller.config.waves[0])
    rcon.fail_summons = True
    rcon.on_summon = controller._stop.set
    controller._drain_pending()
    assert sum("summon minecraft:" in command for command in rcon.commands) == 1


def test_stop_during_preparation_prevents_combat_and_queue_release(monkeypatch) -> None:
    controller, rcon, clock = harness(monkeypatch)
    clock.after_wait = controller._stop.set
    controller._run()
    assert controller.report()["released_waves"] == 0
    assert rcon.spawned == []
    assert "time set midnight" not in rcon.commands


def test_global_loaded_tagged_mobs_outside_target_list_still_consume_capacity(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    controller._begin_combat()
    controller._spawn_wave(controller.config.waves[0])
    rcon.alive["different_controlled_target"] = 3
    controller._drain_pending()
    assert rcon.spawned == []
    assert controller.report()["last_active_mobs"] == 3


def test_reloaded_mobs_above_cap_prevent_any_new_summon(monkeypatch) -> None:
    controller, rcon, _ = harness(monkeypatch)
    controller._begin_combat()
    controller._spawn_wave(controller.config.waves[0])
    rcon.alive[controller.config.targets[0].key] = 5
    controller._drain_pending()
    assert rcon.spawned == []
    assert controller.report()["last_active_mobs"] == 5
    assert controller.report()["peak_active_mobs"] == 5


def test_failed_reserve_does_not_invalidate_legitimate_cap_deferral(monkeypatch) -> None:
    controller, _, _ = harness(monkeypatch)
    for wave in controller.config.waves:
        controller._spawn_wave(wave)
    first = controller.config.targets[0]
    job = next(job for job in controller._pending if job.spawn.target_key == first.key)
    job.attempts = MAX_SPAWN_ATTEMPTS
    controller._failed_by_target[first.key] = 1
    controller._validate_spawn_guarantees()
    assert controller.report()["errors"] == []
    assert controller.report()["deferred_by_target"][first.key] == first.spawn_count - 1


def test_exhausted_failures_only_invalidate_when_remaining_spawn_pool_is_insufficient(
    monkeypatch,
) -> None:
    controller, _, _ = harness(monkeypatch)
    for wave in controller.config.waves:
        controller._spawn_wave(wave)
    first = controller.config.targets[0]
    failed = first.spawn_count - first.target_count + 1
    for job in [job for job in controller._pending if job.spawn.target_key == first.key][:failed]:
        job.attempts = MAX_SPAWN_ATTEMPTS
    controller._failed_by_target[first.key] = failed
    controller._validate_spawn_guarantees()
    assert controller.report()["status"] == "error"
    assert "controlled wave spawn shortfall" in controller.report()["errors"][0]


def test_unreleased_reserves_count_as_viable_opportunities_on_early_stop(monkeypatch) -> None:
    controller, _, _ = harness(monkeypatch)
    controller._spawn_wave(controller.config.waves[0])
    first = controller.config.targets[0]
    job = next(job for job in controller._pending if job.spawn.target_key == first.key)
    job.attempts = MAX_SPAWN_ATTEMPTS
    controller._failed_by_target[first.key] = 1
    controller._validate_spawn_guarantees()
    assert controller.report()["errors"] == []


def test_releasing_same_wave_twice_does_not_duplicate_jobs(monkeypatch) -> None:
    controller, _, _ = harness(monkeypatch)
    wave: CombatWave = controller.config.waves[0]
    controller._spawn_wave(wave)
    controller._spawn_wave(wave)
    assert controller.report()["queued_mobs"] == sum(spawn.count for spawn in wave.spawns)
    assert controller.report()["released_waves"] == 1
