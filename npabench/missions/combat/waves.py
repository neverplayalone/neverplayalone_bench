from __future__ import annotations

import json
import threading
import time
from typing import Any

from npabench.evaluation.run_slot import ServerEndpoint
from npabench.minecraft.rcon_client import command_with_retry, rcon_session
from npabench.missions.combat.config_schema import CombatMissionConfig, CombatWave, WaveSpawn


class CombatWaveController:
    """Run preparation warnings, the combat transition, and seeded mob waves."""

    def __init__(
        self,
        server_endpoint: ServerEndpoint,
        mission_config: CombatMissionConfig,
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
            return {
                "status": self._status,
                "started_at": self._started_at,
                "combat_started_at": self._combat_started_at,
                "stopped_at": self._stopped_at,
                "preparation_seconds": self.config.phase.preparation_seconds,
                "combat_seconds": self.config.phase.combat_seconds,
                "scheduled_waves": len(self.config.waves),
                "spawned_mobs": self._spawned,
                "failed_spawns": self._failed_spawns,
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
            self._begin_combat()

            for wave in self.config.waves:
                event_at = self.config.phase.preparation_seconds + wave.offset_seconds
                if self._wait_until(started, event_at):
                    return
                self._spawn_wave(wave)

            end_at = self.config.phase.preparation_seconds + self.config.phase.combat_seconds
            if self._wait_until(started, end_at):
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
        spawned = 0
        failed = 0
        spawned_by_target: dict[str, int] = {}
        failed_by_target: dict[str, int] = {}
        with self._rcon() as rcon:
            for spawn in wave.spawns:
                for x_offset, z_offset in spawn.offsets:
                    if self._spawn_one(rcon, spawn, x_offset, z_offset):
                        spawned += 1
                        spawned_by_target[spawn.target_key] = (
                            spawned_by_target.get(spawn.target_key, 0) + 1
                        )
                    else:
                        failed += 1
                        failed_by_target[spawn.target_key] = (
                            failed_by_target.get(spawn.target_key, 0) + 1
                        )
        with self._lock:
            self._spawned += spawned
            self._failed_spawns += failed
            for key, count in spawned_by_target.items():
                self._spawned_by_target[key] = self._spawned_by_target.get(key, 0) + count
            for key, count in failed_by_target.items():
                self._failed_by_target[key] = self._failed_by_target.get(key, 0) + count
            self._events.append(
                {
                    "kind": "wave",
                    "wave_index": wave.index,
                    "offset_seconds": wave.offset_seconds,
                    "scheduled": sum(spawn.count for spawn in wave.spawns),
                    "spawned": spawned,
                    "failed": failed,
                    "at": time.time(),
                }
            )

    def _validate_spawn_guarantees(self) -> None:
        with self._lock:
            completed_waves = sum(event.get("kind") == "wave" for event in self._events)
            if completed_waves < len(self.config.waves):
                return
            shortfalls = {
                target.key: {
                    "required": target.target_count,
                    "spawned": self._spawned_by_target.get(target.key, 0),
                }
                for target in self.config.targets
                if self._spawned_by_target.get(target.key, 0) < target.target_count
            }
            if shortfalls and self._status != "error":
                self._errors.append(f"controlled wave spawn shortfall: {shortfalls}")
                self._status = "error"

    def _spawn_one(self, rcon: Any, spawn: WaveSpawn, x_offset: int, z_offset: int) -> bool:
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
        response = rcon.command(local_command)
        if _summon_succeeded(response):
            return True

        surface_command = (
            f"execute at {self.config.username} positioned ~{x_offset} ~ ~{z_offset} "
            f"positioned over motion_blocking_no_leaves run summon minecraft:{spawn.entity_type} "
            f"~ ~1 ~ {nbt}"
        )
        return _summon_succeeded(rcon.command(surface_command))

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
