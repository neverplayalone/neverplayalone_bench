"""Single-slot resource-gathering run: start server, run agent, capture, score."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from mcrcon import MCRcon
from rich.console import Console

from .agents import Agent
from .agents.base import AgentRunContext
from .container import _start_slot, _stop_slot
from .minecraft.commands import _count_item, _parse_pos, _parse_scalar, _read_score
from .minecraft.rcon import rcon_session
from .minecraft.server import ServerConfig, wait_for_ready
from .minecraft.world import _configure_world_start, _give_kit_item, _prepare_playable_spawn
from .models.competition import ResourceCompetitionConfig
from .models.trace import FinalState, Trace, TraceEvent
from .paths import COMPETITION_RESULTS_DIR
from .recording.recorder import (
    Recorder,
    RecordOptions,
    is_available as recorder_available,
    wait_for_settle,
)
from .recording.replay import export_mcpr
from .scoring import _counted_items, _horizontal_distance_from_spawn, score_resource_gathering
from .slot import CompetitionSlot

console = Console()


def run_resource_gathering_competition(
    cfg: ResourceCompetitionConfig,
    agent: Agent,
    slot: CompetitionSlot | None = None,
    out_dir: str | Path | None = None,
    keep_server: bool = False,
    record: RecordOptions | None = None,
    world_template: str | Path | None = None,
) -> dict[str, Any]:
    slot = slot or CompetitionSlot()
    if not cfg.resources:
        raise RuntimeError("resource competition config must include at least one target resource")
    run_id = f"{cfg.id}__{agent.spec.name}__seed{cfg.seed}__slot{slot.slot_id}"
    output = Path(out_dir) if out_dir else COMPETITION_RESULTS_DIR / run_id
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True, exist_ok=True)

    trace = Trace(challenge_id=cfg.id, agent_name=agent.spec.name, started_at=time.time())
    setup_done = False
    timed_out = False
    death_baseline = 0
    spawn_pos: tuple[int, int, int] | None = None
    slot_started = False
    recorder: Recorder | None = None
    final_snapshot: dict[str, Any] | None = None

    try:
        console.log(
            f"[bold cyan]Resource gathering[/]: {run_id} | "
            f"slot {slot.slot_id} | ports {slot.game_port}/{slot.rcon_port}"
        )
        _start_slot(slot, cfg, world_template=Path(world_template) if world_template else None)
        slot_started = True
        try:
            server = slot.server_config()
            wait_for_ready(server, timeout=600)
            _configure_world_start(server, cfg)

            if record is not None:
                ok, reason = recorder_available()
                if not ok:
                    console.log(f"[yellow]Recording disabled[/]: {reason}")
                    trace.append(TraceEvent(kind="info", data={"msg": "recording disabled", "reason": reason}))
                else:
                    record.packet_output = output / "packets.jsonl.gz"
                    record.packet_manifest = output / "packets.manifest.json"
                    record.replay_output = output / "recording.mcpr"
                    record.host = server.host
                    record.port = server.game_port
                    record.target_username = cfg.username
                    recorder = Recorder(record)
                    console.log(f"Starting packet recorder -> {record.packet_output}")
                    recorder.start()
                    wait_for_settle(2.5)
                    try:
                        with rcon_session(
                            server.host, server.rcon_port, server.rcon_password
                        ) as mcr:
                            mcr.command(f"op {record.recorder_username}")
                            mcr.command(f"gamemode spectator {record.recorder_username}")
                    except Exception as e:
                        trace.append(
                            TraceEvent(kind="error", data={"msg": f"recorder setup failed: {e}"})
                        )

            ctx = AgentRunContext(
                host=server.host,
                port=server.game_port,
                username=cfg.username,
                goal=_goal_text(cfg),
                timeout_seconds=cfg.duration_seconds,
            )
            console.log(
                f"Launching agent [bold]{agent.spec.name}[/] for {cfg.duration_seconds}s..."
            )
            for event in agent.run(ctx):
                trace.append(event)
                if event.kind == "info" and event.data.get("msg") == "timeout":
                    timed_out = True
                if not setup_done and event.kind == "ready":
                    trace.agent_ready_at = time.time()
                    console.log("Agent spawned; applying competition kit and timer setup...")
                    with rcon_session(
                        server.host, server.rcon_port, server.rcon_password
                    ) as mcr:
                        death_baseline, spawn_pos = _setup_competitor(mcr, cfg)
                        if recorder is not None and record is not None:
                            mcr.command(f"tp {record.recorder_username} {cfg.username}")
                            mcr.command(f"spectate {cfg.username} {record.recorder_username}")
                    setup_done = True
                if event.kind == "done":
                    console.log("Agent reported done.")
                    break
        finally:
            trace.ended_at = time.time()
            trace.timed_out = timed_out
            if setup_done:
                console.log("Capturing competition final state...")
                try:
                    with rcon_session(slot.host, slot.rcon_port, slot.rcon_password) as mcr:
                        final_snapshot = _capture_final_snapshot(mcr, cfg, death_baseline, spawn_pos)
                        trace.final_state = final_snapshot["final_state"]
                except Exception as e:
                    final_snapshot = {
                        "error": f"snapshot failed: {e}",
                        "deaths": 0,
                        "alive": False,
                    }
                    trace.append(TraceEvent(kind="error", data=final_snapshot))
            agent.stop()
            if recorder is not None and record is not None:
                console.log("Stopping recorder...")
                recorder.stop()
                if record.packet_output and record.packet_output.exists():
                    try:
                        replay_path = export_mcpr(record.packet_output, output=record.replay_output)
                        console.log(f"ReplayMod recording saved: {replay_path}")
                    except Exception as e:
                        trace.append(
                            TraceEvent(kind="error", data={"msg": f"replay export failed: {e}"})
                        )
                        console.log(
                            f"[yellow]Replay export failed; kept packet log: {record.packet_output}[/]"
                        )
                else:
                    trace.append(TraceEvent(kind="error", data={"msg": "packet recording produced no output"}))
                    console.log("[yellow]Packet recording produced no output.[/]")
                    if recorder.stderr_log:
                        console.log("[yellow]Recorder stderr (tail):[/]")
                        for line in recorder.stderr_log[-40:]:
                            console.log(f"  {line}")

        if final_snapshot is None:
            console.log("Capturing competition final state...")
            try:
                with rcon_session(slot.host, slot.rcon_port, slot.rcon_password) as mcr:
                    final_snapshot = _capture_final_snapshot(mcr, cfg, death_baseline, spawn_pos)
                    trace.final_state = final_snapshot["final_state"]
            except Exception as e:
                final_snapshot = {"error": f"snapshot failed: {e}", "deaths": 0, "alive": False}
                trace.append(TraceEvent(kind="error", data=final_snapshot))

        report = score_resource_gathering(cfg, trace, final_snapshot)
        (output / "trace.json").write_text(trace.model_dump_json(indent=2))
        (output / "score.json").write_text(json.dumps(report, indent=2))
        (output / "config.json").write_text(cfg.model_dump_json(indent=2))

        if not setup_done:
            console.log(
                "[yellow]Agent never reported `ready`[/]: no kit/spawn setup ran. "
                "Score reflects a failed agent, not a poor strategy."
            )
        console.log(
            f"[bold green]Score[/]: {report['score']:.1f} / {report['max_score']:.1f}"
        )
        console.log(f"Artifacts: {output}")
        return report
    finally:
        if keep_server and slot_started:
            console.log(f"[yellow]Keeping server container running:[/] {slot.container_name}")
        elif slot_started:
            _stop_slot(slot, quiet=True)


def _setup_competitor(
    mcr: MCRcon,
    cfg: ResourceCompetitionConfig,
) -> tuple[int, tuple[int, int, int]]:
    mcr.command(f"op {cfg.username}")
    mcr.command(f"clear {cfg.username}")
    mcr.command("kill @e[type=item]")
    mcr.command("scoreboard objectives remove mcb_deaths")
    mcr.command("scoreboard objectives add mcb_deaths minecraft.custom:minecraft.deaths")
    death_baseline = _read_score(mcr, cfg.username, "mcb_deaths")
    spawn_pos = _prepare_playable_spawn(mcr, cfg.username)
    for kit in cfg.kit:
        _give_kit_item(mcr, cfg.username, kit)
    mcr.command(f"gamemode survival {cfg.username}")
    mcr.command(f"effect give {cfg.username} minecraft:saturation 3 10 true")
    mcr.command(f"deop {cfg.username}")
    return death_baseline, spawn_pos


def _capture_final_snapshot(
    mcr: MCRcon,
    cfg: ResourceCompetitionConfig,
    death_baseline: int,
    spawn_pos: tuple[int, int, int] | None,
) -> dict[str, Any]:
    state = FinalState()
    state.position = _parse_pos(mcr.command(f"data get entity {cfg.username} Pos"))
    state.health = _parse_scalar(mcr.command(f"data get entity {cfg.username} Health"))
    state.food = _parse_scalar(mcr.command(f"data get entity {cfg.username} foodLevel"))
    for resource in cfg.resources:
        total = 0
        for item in _counted_items(resource):
            count = _count_item(mcr, cfg.username, item)
            state.inventory[item] = count
            total += count
        state.inventory[resource.item] = total
    deaths = max(0, _read_score(mcr, cfg.username, "mcb_deaths") - death_baseline)
    distance_from_spawn = _horizontal_distance_from_spawn(state.position, spawn_pos)
    return {
        "final_state": state,
        "deaths": deaths,
        "alive": state.health is not None and state.health > 0,
        "spawn": {
            "position": spawn_pos,
        },
        "distance_from_spawn": distance_from_spawn,
    }


def _goal_text(cfg: ResourceCompetitionConfig) -> str:
    return cfg.goal
