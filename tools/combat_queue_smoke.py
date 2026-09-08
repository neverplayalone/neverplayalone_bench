"""Opt-in real-Paper queue smoke test, not a full benchmark or agent evaluation.

Run from the repository with ``uv run python tools/combat_queue_smoke.py --run``.
Only a UUID-named disposable flat-world server is modified. Kill scores below are
administrative test fixtures, not kills earned by an agent. No player login is
needed: only the controller's summon position is replaced with a fixed platform.
"""

from __future__ import annotations

import argparse
import json
import socket
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from npabench.evaluation.reference_world import start_agent_run_slot, stop_agent_run_slot
from npabench.evaluation.run_slot import AgentRunSlot
from npabench.minecraft.server_probe import wait_for_ready
from npabench.missions.combat import CombatMission
from npabench.missions.combat.config_schema import WaveSpawn
from npabench.missions.combat.environment import configure_combat_world
from npabench.missions.combat.waves import (
    LIVE_OBJECTIVE,
    CombatWaveController,
    _read_score_strict,
    _summon_succeeded,
)


class FixedPlatformController(CombatWaveController):
    def _spawn_one(self, rcon: Any, spawn: WaveSpawn, x_offset: int, z_offset: int) -> bool:
        del x_offset, z_offset
        if not self._can_spawn():
            return False
        nbt = (
            '{Tags:["npabench_wave","ncb_'
            f'{spawn.tier}","ncb_{spawn.target_key}"],'
            "NoAI:1b,PersistenceRequired:1b}"
        )
        response = rcon.command(f"summon minecraft:{spawn.entity_type} 0.5 100 0.5 {nbt}")
        if not _summon_succeeded(response):
            raise RuntimeError(f"smoke fixture summon failed: {response!r}")
        return True


def _check_ports(game_port: int, rcon_port: int) -> None:
    if game_port == rcon_port or any(not 1024 <= port <= 65535 for port in (game_port, rcon_port)):
        raise ValueError("game and RCON ports must be distinct integers between 1024 and 65535")
    reservations: list[socket.socket] = []
    try:
        for port in (game_port, rcon_port):
            reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            reservations.append(reservation)
            reservation.bind(("0.0.0.0", port))
    finally:
        for reservation in reservations:
            reservation.close()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _wait_for_alive(controller: CombatWaveController, expected: int) -> None:
    deadline = time.monotonic() + 5.0
    last_count = None
    while time.monotonic() < deadline:
        with controller._rcon() as rcon:
            last_count, _, _ = controller._observe(rcon)
        if last_count == expected:
            return
        time.sleep(0.1)
    raise RuntimeError(f"expected {expected} living mobs; last observed {last_count}")


def _check_real_summon_paths(controller: CombatWaveController) -> dict[str, Any]:
    anchor = "@e[type=minecraft:armor_stand,tag=ncb_smoke_anchor,limit=1]"
    probe = CombatWaveController(
        controller.endpoint,
        controller.config.model_copy(update={"username": anchor}),
    )
    spawn = controller.config.waves[0].spawns[0]
    with controller._rcon() as rcon:
        false_condition_command = (
            "execute positioned 0 100 0 if block ~ ~ ~ minecraft:stone "
            'run summon minecraft:pig ~ ~ ~ {Tags:["ncb_smoke_unexpected"]}'
        )
        _require(
            not probe._summon_confirmed(rcon, false_condition_command),
            "known-false condition did not produce a confirmed unsuccessful summon",
        )
        _require(
            controller._count_alive(
                rcon, "#smoke_pigs", "@e[type=minecraft:pig,tag=ncb_smoke_unexpected]"
            )
            == 0,
            "known-false conditional created an unexpected pig",
        )
        response = rcon.command(
            "summon minecraft:armor_stand 0.5 100 0.5 "
            '{Tags:["ncb_smoke_anchor"],NoGravity:1b,Invulnerable:1b,Marker:1b}'
        )
        _require(_summon_succeeded(response), f"smoke anchor summon failed: {response!r}")
        _require(probe._spawn_one(rcon, spawn, 0, 0), "real local spawn path failed")
    _wait_for_alive(controller, 1)
    with controller._rcon() as rcon:
        rcon.command("kill @e[tag=npabench_wave]")
    _wait_for_alive(controller, 0)
    with controller._rcon() as rcon:
        rcon.command("setblock 2 100 0 minecraft:stone")
        _require(probe._spawn_one(rcon, spawn, 2, 0), "real surface fallback spawn failed")
    _wait_for_alive(controller, 1)
    with controller._rcon() as rcon:
        rcon.command("kill @e[tag=npabench_wave]")
        rcon.command("kill @e[tag=ncb_smoke_anchor]")
        rcon.command("setblock 2 100 0 minecraft:air")
    _wait_for_alive(controller, 0)
    return {
        "conditional_failure_confirmed_by_score": True,
        "unexpected_conditional_pigs": 0,
        "real_local_spawn_path": True,
        "real_surface_fallback_spawn_path": True,
    }


def _diagnose_summon_protocol(controller: CombatWaveController) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    prefix = f"execute store success score #probe {LIVE_OBJECTIVE}"
    positioned = "positioned 0 100 0"
    false_guard = "if block ~ ~ ~ minecraft:stone"
    true_guard = "if block ~ ~ ~ minecraft:air"
    cases = [
        (
            "nested_false_noop",
            -1,
            f"{prefix} run execute {positioned} {false_guard} run time query gametime",
        ),
        ("flat_false_noop", -1, f"{prefix} {positioned} {false_guard} run time query gametime"),
        ("terminal_false_success", -1, f"{prefix} {positioned} {false_guard}"),
        (
            "terminal_false_result",
            -1,
            f"execute store result score #probe {LIVE_OBJECTIVE} {positioned} {false_guard}",
        ),
        ("terminal_true_success", -1, f"{prefix} {positioned} {true_guard}"),
        ("unguarded_success_noop", -1, f"{prefix} run time query gametime"),
        ("flat_true_noop", -1, f"{prefix} {positioned} {true_guard} run time query gametime"),
    ]
    summon = "summon minecraft:pig 0.5 100 0.5 SMOKE_NBT"
    for initial in (-1, 0):
        for name, command in (
            ("unguarded_summon", f"{prefix} run {summon}"),
            (
                "nested_false_summon",
                f"{prefix} run execute {positioned} {false_guard} run {summon}",
            ),
            ("flat_false_summon", f"{prefix} {positioned} {false_guard} run {summon}"),
            ("nested_true_summon", f"{prefix} run execute {positioned} {true_guard} run {summon}"),
            ("flat_true_summon", f"{prefix} {positioned} {true_guard} run {summon}"),
        ):
            cases.append((f"{name}_initial_{initial}", initial, command))
    with controller._rcon() as rcon:
        for index, (name, initial, command) in enumerate(cases):
            tag = f"ncb_diag_{index}"
            nbt = (
                f'{{Tags:["ncb_smoke_diag","{tag}"],'
                "NoAI:1b,NoGravity:1b,Invulnerable:1b,PersistenceRequired:1b}"
            )
            command = command.replace("SMOKE_NBT", nbt)
            rcon.command(f"scoreboard players set #probe {LIVE_OBJECTIVE} {initial}")
            _require(
                _read_score_strict(rcon, "#probe", LIVE_OBJECTIVE) == initial,
                "diagnostic score initialization failed",
            )
            response = rcon.command(command)
            score = _read_score_strict(rcon, "#probe", LIVE_OBJECTIVE)
            pigs = controller._count_alive(rcon, "#diag_pigs", f"@e[type=minecraft:pig,tag={tag}]")
            row = {
                "case": name,
                "initial_score": initial,
                "command": command,
                "command_response": response,
                "final_score": score,
                "actual_tagged_pigs": pigs,
            }
            results.append(row)
            print(json.dumps({"diagnostic_case": row}, sort_keys=True), flush=True)
        rcon.command("kill @e[tag=ncb_smoke_diag]")
    return results


def run_smoke(game_port: int, rcon_port: int, *, diagnose_summon: bool = False) -> dict[str, Any]:
    _check_ports(game_port, rcon_port)
    mission = CombatMission()
    base = mission.load_config(mission.default_config_path())
    task = mission.generate_task(base, seed=0)
    config = mission.build_mission_config(base, task)
    config = config.model_copy(
        update={
            "world_type": "flat",
            "generate_structures": False,
            "username": "ncb_smoke",
        }
    )
    prefix = f"npabench-combat-smoke-{uuid.uuid4().hex[:12]}"
    controller: FixedPlatformController | None = None
    result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="npabench-combat-queue-") as temporary_dir:
        slot = AgentRunSlot.allocate(
            container_prefix=prefix,
            base_game_port=game_port,
            base_rcon_port=rcon_port,
            data_root=Path(temporary_dir),
        )
        try:
            start_agent_run_slot(slot, config)
            wait_for_ready(slot.server_endpoint(), timeout=180.0)
            baselines = {target.key: 17 for target in config.targets}
            controller = FixedPlatformController(
                slot.server_endpoint(), config, {"kill_baselines": baselines}
            )
            controller._combat_deadline = time.monotonic() + 120.0
            with controller._rcon() as rcon:
                configure_combat_world(rcon, config)
                rcon.command("forceload add -16 -16 16 16")
                platform_deadline = time.monotonic() + 10.0
                while True:
                    rcon.command("fill -4 99 -4 4 99 4 minecraft:stone")
                    response = rcon.command(
                        "execute if block 0 99 0 minecraft:stone run time query gametime"
                    )
                    if "time is" in response.lower():
                        break
                    if time.monotonic() >= platform_deadline:
                        raise RuntimeError(f"smoke platform did not load: {response!r}")
                    time.sleep(0.2)
                rcon.command("fill -4 100 -4 4 104 4 minecraft:air")
                for target in config.targets:
                    rcon.command(
                        f"scoreboard objectives add {target.objective} "
                        f"minecraft.killed:minecraft.{target.entity_type}"
                    )
                    rcon.command(
                        f"scoreboard players set {config.username} {target.objective} "
                        f"{baselines[target.key]}"
                    )
            controller._begin_combat()
            _wait_for_alive(controller, 0)
            if diagnose_summon:
                return {
                    "status": "diagnostic_complete",
                    "kind": "isolated_real_server_protocol_characterization",
                    "minecraft_version": config.minecraft_version,
                    "cases": _diagnose_summon_protocol(controller),
                    "server_container": slot.container_name,
                    "disposable_server_and_data_cleaned": True,
                }
            real_spawn_paths = _check_real_summon_paths(controller)
            with controller._rcon() as rcon:
                _, _, initial_kills = controller._observe(rcon)
            _require(all(count == 0 for count in initial_kills.values()), "baseline not subtracted")

            scheduled = sum(spawn.count for wave in config.waves for spawn in wave.spawns)
            cap = config.phase.max_active_mobs
            _require(scheduled > cap, "fixture must release more mobs than the active cap")
            for wave in config.waves:
                controller._spawn_wave(wave)
            controller._drain_pending()
            _wait_for_alive(controller, cap)
            initial_report = controller.report()
            _require(initial_report["spawned_mobs"] == cap, "initial drain exceeded spawn cap")
            _require(initial_report["pending_mobs"] == scheduled - cap, "queued jobs were lost")

            first_target = config.targets[0]
            with controller._rcon() as rcon:
                response = rcon.command(
                    f"kill @e[tag=npabench_wave,tag=ncb_{first_target.key},limit=1,sort=arbitrary]"
                )
                _require("Killed" in response, f"fixture kill failed: {response!r}")
            _wait_for_alive(controller, cap - 1)
            controller._drain_pending()
            _wait_for_alive(controller, cap)
            replacement_report = controller.report()
            _require(replacement_report["spawned_mobs"] == cap + 1, "missing mob not replaced")
            _require(
                replacement_report["target_shortfalls"][first_target.key]["observed_kills"] == 0,
                "administrative kill unexpectedly counted toward agent quota",
            )

            with controller._rcon() as rcon:
                rcon.command(
                    f"scoreboard players set {config.username} {first_target.objective} "
                    f"{baselines[first_target.key] + first_target.target_count}"
                )
                rcon.command(f"kill @e[tag=npabench_wave,tag=ncb_{first_target.key}]")
            _wait_for_alive(controller, 0)
            controller._drain_pending()
            _wait_for_alive(controller, cap)
            final_report = controller.report()
            _require(
                final_report["fulfilled_skips_by_target"].get(first_target.key, 0) > 0,
                "completed target reserves were not suppressed",
            )
            _require(
                any(
                    count > 0
                    for key, count in final_report["spawned_by_target"].items()
                    if key != first_target.key
                ),
                "later targets did not progress past completed target reserves",
            )
            _require(final_report["peak_active_mobs"] <= cap, "observed active cap exceeded")
            _require(not final_report["errors"], "controller reported a runtime error")
            result = {
                "status": "passed",
                "kind": "isolated_real_server_queue_smoke_not_agent_evaluation",
                "minecraft_version": config.minecraft_version,
                "active_cap": cap,
                "peak_active_mobs": final_report["peak_active_mobs"],
                "released_jobs": scheduled,
                "pending_after_initial_cap": initial_report["pending_mobs"],
                "spawned_after_uncredited_loss": replacement_report["spawned_mobs"],
                "completed_target": first_target.key,
                "completed_target_reserves_skipped": final_report["fulfilled_skips_by_target"][
                    first_target.key
                ],
                "later_target_progress": True,
                "kill_score_source": "administrative fixture, not agent combat",
                "server_container": slot.container_name,
                **real_spawn_paths,
            }
        finally:
            try:
                if controller is not None:
                    controller.stop()
            finally:
                stop_agent_run_slot(slot)
    result["disposable_server_and_data_cleaned"] = True
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="explicitly start the disposable server")
    parser.add_argument("--game-port", type=int, default=26660)
    parser.add_argument("--rcon-port", type=int, default=26661)
    parser.add_argument(
        "--diagnose-summon", action="store_true", help="characterize real execute/store semantics"
    )
    args = parser.parse_args()
    if not args.run:
        parser.error("pass --run to opt in to creating a disposable Minecraft server")
    print(
        json.dumps(
            run_smoke(args.game_port, args.rcon_port, diagnose_summon=args.diagnose_summon),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
