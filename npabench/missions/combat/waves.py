from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

from npabench.evaluation.run_slot import ServerEndpoint
from npabench.minecraft.rcon_client import command_with_retry, rcon_session
from npabench.missions.combat.config_schema import CombatMissionConfig, CombatWave, WaveSpawn

LIVE_OBJECTIVE = "ncb_live"
POLL_SECONDS = 1.0
MAX_SPAWN_ATTEMPTS = 3


@dataclass
class PendingSpawn:
    wave_index: int
    spawn: WaveSpawn
    x_offset: int
    z_offset: int
    attempts: int = 0


class CombatWaveController:
    """Run preparation warnings, the combat transition, and seeded mob waves."""

    def __init__(
        self,
        server_endpoint: ServerEndpoint,
        mission_config: CombatMissionConfig,
        setup_state: dict[str, Any] | None = None,
    ) -> None:
        self.endpoint = server_endpoint
        self.config = mission_config
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._combat_started_at: float | None = None
        self._stopped_at: float | None = None
        self._status = "not_started"
        self._events: list[dict[str, Any]] = []
        self._spawned = 0
        self._failed_spawns = 0
        self._spawned_by_target: dict[str, int] = {}
        self._failed_by_target: dict[str, int] = {}
        self._errors: list[str] = []
        self._kill_baselines = dict((setup_state or {}).get("kill_baselines", {}))
        self._targets = {target.key: target for target in mission_config.targets}
        self._pending: list[PendingSpawn] = []
        self._queued = 0
        self._released_waves: set[int] = set()
        self._spawned_waves: set[int] = set()
        self._fulfilled_skips: Counter[str] = Counter()
        self._spawn_attempt_failures = 0
        self._last_kills: dict[str, int] = {}
        self._last_alive_by_target: dict[str, int] = {}
        self._last_active: int | None = None
        self._peak_active = 0
        self._combat_deadline: float | None = None

    def start(self) -> CombatWaveController:
        if self._thread is not None:
            return self
        self._started_at = time.time()
        self._status = "preparation"
        self._thread = threading.Thread(
            target=self._run,
            name="npabench-combat-waves",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            if self._thread.is_alive():
                self._record_error("combat wave controller did not stop within 10 seconds")
        self._validate_spawn_guarantees()
        self._disable_natural_spawning()
        self._stopped_at = time.time()
        if self._status not in {"complete", "error"}:
            self._status = "stopped"
        return self.report()

    def report(self) -> dict[str, Any]:
        with self._lock:
            pending = Counter(job.spawn.target_key for job in self._pending)
            deferred = Counter(
                job.spawn.target_key for job in self._pending if job.attempts < MAX_SPAWN_ATTEMPTS
            )
            shortfalls = {
                target.key: {
                    "required_kills": target.target_count,
                    "observed_kills": self._last_kills.get(target.key, 0),
                    "remaining_kills": max(
                        0, target.target_count - self._last_kills.get(target.key, 0)
                    ),
                    "observed_alive": self._last_alive_by_target.get(target.key, 0),
                    "spawned": self._spawned_by_target.get(target.key, 0),
                    "pending": pending[target.key],
                    "failed": self._failed_by_target.get(target.key, 0),
                }
                for target in self.config.targets
                if self._last_kills.get(target.key, 0) < target.target_count
            }
            return {
                "status": self._status,
                "started_at": self._started_at,
                "combat_started_at": self._combat_started_at,
                "stopped_at": self._stopped_at,
                "preparation_seconds": self.config.phase.preparation_seconds,
                "combat_seconds": self.config.phase.combat_seconds,
                "scheduled_waves": len(self.config.waves),
                "released_waves": len(self._released_waves),
                "spawned_waves": len(self._spawned_waves),
                "max_active_mobs": self.config.phase.max_active_mobs,
                "peak_active_mobs": self._peak_active,
                "last_active_mobs": self._last_active,
                "queued_mobs": self._queued,
                "pending_mobs": len(self._pending),
                "pending_by_target": dict(pending),
                "deferred_by_target": dict(deferred),
                "fulfilled_skips": sum(self._fulfilled_skips.values()),
                "fulfilled_skips_by_target": dict(self._fulfilled_skips),
                "target_shortfalls": shortfalls,
                "spawned_mobs": self._spawned,
                "failed_spawns": self._failed_spawns,
                "spawn_attempt_failures": self._spawn_attempt_failures,
                "spawned_by_target": dict(self._spawned_by_target),
                "failed_by_target": dict(self._failed_by_target),
                "events": list(self._events),
                "errors": list(self._errors),
            }

    def _run(self) -> None:
        started = time.monotonic()
        try:
            for seconds_before in self.config.phase.warning_offsets_seconds:
                event_at = self.config.phase.preparation_seconds - seconds_before
                if self._wait_until(started, event_at):
                    return
                self._announce(
                    f"Combat begins in {seconds_before} seconds. Finish your equipment and prepare."
                )

            if self._wait_until(started, self.config.phase.preparation_seconds):
                return
            self._combat_deadline = (
                started + self.config.phase.preparation_seconds + self.config.phase.combat_seconds
            )
            self._begin_combat()

            next_wave = 0
            while self._can_spawn():
                elapsed = time.monotonic() - started - self.config.phase.preparation_seconds
                while (
                    next_wave < len(self.config.waves)
                    and self.config.waves[next_wave].offset_seconds <= elapsed
                ):
                    self._spawn_wave(self.config.waves[next_wave])
                    next_wave += 1
                self._drain_pending()
                remaining = self._combat_deadline - time.monotonic()
                if self._stop.wait(min(POLL_SECONDS, max(0.0, remaining))):
                    return
            if self._stop.is_set():
                return
            self._disable_natural_spawning()
            self._validate_spawn_guarantees()
            with self._lock:
                if self._status != "error":
                    self._status = "complete"
                self._events.append({"kind": "combat_complete", "at": time.time()})
        except Exception as exc:  # noqa: BLE001 - runtime failures are reported to the scorer
            self._record_error(str(exc))

    def _wait_until(self, started: float, offset_seconds: float) -> bool:
        remaining = max(0.0, started + offset_seconds - time.monotonic())
        return self._stop.wait(remaining)

    def _announce(self, message: str) -> None:
        payload = json.dumps({"text": message, "color": "yellow"}, separators=(",", ":"))
        with self._rcon() as rcon:
            command_with_retry(rcon, f"tellraw @a {payload}")
        with self._lock:
            self._events.append({"kind": "warning", "message": message, "at": time.time()})

    def _begin_combat(self) -> None:
        with self._rcon() as rcon:
            command_with_retry(rcon, f"scoreboard objectives add {LIVE_OBJECTIVE} dummy")
            for target in self.config.targets:
                command_with_retry(
                    rcon, f"scoreboard players add {self.config.username} {target.objective} 0"
                )
            command_with_retry(rcon, "time set midnight")
            command_with_retry(
                rcon,
                "gamerule spawn_mobs "
                + ("true" if self.config.phase.spawn_mobs_naturally else "false"),
            )
            command_with_retry(rcon, f"difficulty {self.config.difficulty}")
            title = json.dumps(
                {"text": "COMBAT PHASE", "color": "red", "bold": True},
                separators=(",", ":"),
            )
            command_with_retry(rcon, f"title {self.config.username} title {title}")
        with self._lock:
            self._status = "combat"
            self._combat_started_at = time.time()
            self._events.append({"kind": "combat_started", "at": self._combat_started_at})

    def _spawn_wave(self, wave: CombatWave) -> None:
        """Release a wave's jobs; only the capped drain may summon them."""
        with self._lock:
            if wave.index in self._released_waves or not self._can_spawn():
                return
            for spawn in wave.spawns:
                for x_offset, z_offset in spawn.offsets:
                    self._pending.append(PendingSpawn(wave.index, spawn, x_offset, z_offset))
                    self._queued += 1
            self._released_waves.add(wave.index)
            self._events.append(
                {
                    "kind": "wave",
                    "wave_index": wave.index,
                    "offset_seconds": wave.offset_seconds,
                    "scheduled": sum(spawn.count for spawn in wave.spawns),
                    "spawned": 0,
                    "failed": 0,
                    "fulfilled_skips": 0,
                    "first_spawned_at": None,
                    "last_spawned_at": None,
                    "at": time.time(),
                }
            )

    def _can_spawn(self) -> bool:
        return not self._stop.is_set() and (
            self._combat_deadline is None or time.monotonic() < self._combat_deadline
        )

    def _count_alive(self, rcon: Any, holder: str, selector: str) -> int:
        # A verified sentinel prevents a failed store from reusing an old zero count.
        rcon.command(f"scoreboard players set {holder} {LIVE_OBJECTIVE} -1")
        if _read_score_strict(rcon, holder, LIVE_OBJECTIVE) != -1:
            raise RuntimeError(f"could not initialize controlled mob count for {holder}")
        rcon.command(f"execute store result score {holder} {LIVE_OBJECTIVE} if entity {selector}")
        count = _read_score_strict(rcon, holder, LIVE_OBJECTIVE)
        if count < 0:
            raise RuntimeError(f"could not count living controlled mobs for {holder}")
        return count

    def _observe(self, rcon: Any) -> tuple[int, dict[str, int], dict[str, int]]:
        active = self._count_alive(rcon, "#active", "@e[tag=npabench_wave,nbt={DeathTime:0s}]")
        alive: dict[str, int] = {}
        kills: dict[str, int] = {}
        for index, target in enumerate(self.config.targets):
            if not self._can_spawn():
                return active, alive, kills
            alive[target.key] = self._count_alive(
                rcon,
                f"#target{index}",
                f"@e[tag=npabench_wave,tag=ncb_{target.key},nbt={{DeathTime:0s}}]",
            )
            total = _read_score_strict(rcon, self.config.username, target.objective)
            kills[target.key] = max(0, total - self._kill_baselines.get(target.key, 0))
        with self._lock:
            self._last_active = active
            self._peak_active = max(self._peak_active, active)
            self._last_alive_by_target = alive
            self._last_kills = kills
        return active, alive, kills

    def _drain_pending(self) -> None:
        if not self._can_spawn():
            return
        with self._rcon() as rcon:
            active, alive, kills = self._observe(rcon)
            for job in list(self._pending):
                if not self._can_spawn():
                    return
                key = job.spawn.target_key
                quota = self._targets[key].target_count
                if kills[key] >= quota:
                    with self._lock:
                        self._pending.remove(job)
                        self._fulfilled_skips[key] += 1
                        self._wave_event(job.wave_index)["fulfilled_skips"] += 1
                    continue
                if (
                    job.attempts >= MAX_SPAWN_ATTEMPTS
                    or active >= self.config.phase.max_active_mobs
                    or kills[key] + alive[key] >= quota
                ):
                    continue
                job.attempts += 1
                succeeded = self._spawn_one(rcon, job.spawn, job.x_offset, job.z_offset)
                with self._lock:
                    if succeeded:
                        self._pending.remove(job)
                        active += 1
                        alive[key] += 1
                        self._last_active = active
                        self._peak_active = max(self._peak_active, active)
                        self._spawned += 1
                        self._spawned_by_target[key] = self._spawned_by_target.get(key, 0) + 1
                        self._spawned_waves.add(job.wave_index)
                        event = self._wave_event(job.wave_index)
                        event["spawned"] += 1
                        event["last_spawned_at"] = time.time()
                        if event["first_spawned_at"] is None:
                            event["first_spawned_at"] = event["last_spawned_at"]
                    else:
                        self._spawn_attempt_failures += 1
                        if job.attempts == MAX_SPAWN_ATTEMPTS:
                            self._failed_spawns += 1
                            self._failed_by_target[key] = self._failed_by_target.get(key, 0) + 1
                            self._wave_event(job.wave_index)["failed"] += 1

    def _wave_event(self, index: int) -> dict[str, Any]:
        return next(
            event
            for event in self._events
            if event.get("kind") == "wave" and event["wave_index"] == index
        )

    def _validate_spawn_guarantees(self) -> None:
        with self._lock:
            viable_pending = Counter(
                job.spawn.target_key for job in self._pending if job.attempts < MAX_SPAWN_ATTEMPTS
            )
            unreleased = Counter(
                spawn.target_key
                for wave in self.config.waves
                if wave.index not in self._released_waves
                for spawn in wave.spawns
                for _ in spawn.offsets
            )
            shortfalls = {
                target.key: {
                    "required": target.target_count,
                    "spawned": self._spawned_by_target.get(target.key, 0),
                    "viable_pending": viable_pending[target.key],
                    "unreleased": unreleased[target.key],
                    "failed": self._failed_by_target.get(target.key, 0),
                }
                for target in self.config.targets
                if (
                    self._spawned_by_target.get(target.key, 0)
                    + viable_pending[target.key]
                    + unreleased[target.key]
                    < target.target_count
                )
                and self._failed_by_target.get(target.key, 0) > 0
                and self._last_kills.get(target.key, 0) < target.target_count
            }
            if shortfalls and self._status != "error":
                self._errors.append(f"controlled wave spawn shortfall: {shortfalls}")
                self._status = "error"

    def _spawn_one(self, rcon: Any, spawn: WaveSpawn, x_offset: int, z_offset: int) -> bool:
        if not self._can_spawn():
            return False
        nbt = (
            '{Tags:["npabench_wave","ncb_'
            f'{spawn.tier}","ncb_{spawn.target_key}"],PersistenceRequired:1b}}'
        )
        summon = f"summon minecraft:{spawn.entity_type} ~ ~ ~ {nbt}"
        local_command = (
            f"execute at {self.config.username} positioned ~{x_offset} ~ ~{z_offset} "
            "if block ~ ~ ~ minecraft:air if block ~ ~1 ~ minecraft:air "
            f"unless block ~ ~-1 ~ minecraft:air run {summon}"
        )
        if self._summon_confirmed(rcon, local_command):
            return True
        if not self._can_spawn():
            return False

        surface_command = (
            f"execute at {self.config.username} positioned ~{x_offset} ~ ~{z_offset} "
            f"positioned over motion_blocking_no_leaves run summon minecraft:{spawn.entity_type} "
            f"~ ~1 ~ {nbt}"
        )
        return self._summon_confirmed(rcon, surface_command)

    def _summon_confirmed(self, rcon: Any, command: str) -> bool:
        if not self._can_spawn():
            return False
        holder = "#summon"
        rcon.command(f"scoreboard players set {holder} {LIVE_OBJECTIVE} 0")
        if _read_score_strict(rcon, holder, LIVE_OBJECTIVE) != 0:
            raise RuntimeError("could not initialize controlled summon result")
        if not self._can_spawn():
            return False
        # A failed guard has zero execution branches: its empty response leaves this
        # verified zero unchanged. An executed successful summon stores one.
        response = rcon.command(
            f"execute store success score {holder} {LIVE_OBJECTIVE} run {command}"
        )
        succeeded = _read_score_strict(rcon, holder, LIVE_OBJECTIVE)
        if succeeded not in {0, 1}:
            raise RuntimeError("could not verify controlled summon result; refusing another summon")
        if succeeded == 0 and _summon_succeeded(response):
            raise RuntimeError("inconsistent controlled summon result; refusing another summon")
        if (
            succeeded == 0
            and response.strip()
            and not any(
                marker in response.lower()
                for marker in (
                    "test failed",
                    "no blocks passed",
                    "unable to summon",
                    "failed to summon",
                    "no entity was found",
                )
            )
        ):
            raise RuntimeError(f"ambiguous summon response; refusing another summon: {response!r}")
        return succeeded == 1

    def _disable_natural_spawning(self) -> None:
        try:
            with self._rcon() as rcon:
                command_with_retry(rcon, "gamerule spawn_mobs false", attempts=1)
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup, retained in audit report
            self._record_error(f"failed to disable natural spawning: {exc}", fatal=False)

    def _rcon(self):
        return rcon_session(
            self.endpoint.host,
            self.endpoint.rcon_port,
            self.endpoint.rcon_password,
            connect_timeout=5,
            socket_timeout=5,
        )

    def _record_error(self, message: str, *, fatal: bool = True) -> None:
        with self._lock:
            self._errors.append(message)
            if fatal:
                self._status = "error"


def _summon_succeeded(response: str) -> bool:
    lowered = response.lower()
    return "summoned" in lowered and "unable" not in lowered and "failed" not in lowered


def _read_score_strict(rcon: Any, holder: str, objective: str) -> int:
    response = rcon.command(f"scoreboard players get {holder} {objective}")
    match = re.fullmatch(
        rf"{re.escape(holder)} has (-?\d+) \[{re.escape(objective)}\]", response.strip()
    )
    if match is None:
        raise RuntimeError(f"could not read scoreboard {holder}/{objective}: {response!r}")
    return int(match.group(1))
