"""Single-slot run: start server, configure the world, run the agent, capture, score.

This is the generic engine loop. Everything task-specific (world rules,
kit/spawn setup, what to capture, how to score) is delegated to the supplied
:class:`Task`.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console

from mcbench.minecraft.rcon import rcon_session
from mcbench.minecraft.server import ServerConfig, wait_for_ready
from mcbench.paths import RESULTS_DIR
from mcbench.recording.recorder import (
    Recorder,
    RecordOptions,
    is_available as recorder_available,
    wait_for_settle,
)
from mcbench.recording.replay import export_mcpr
from mcbench.core.base_task import Task, RunConfig
from mcbench.core.container import _start_slot, _stop_slot
from mcbench.core.slot import Slot
from mcbench.core.trace import Trace, TraceEvent

# Imported lazily inside _agent_context to avoid an import cycle (mcbench.agents pulls
# in mcbench.core.trace, which triggers mcbench.core's __init__).
if TYPE_CHECKING:
    from mcbench.agents import Agent

console = Console()


@dataclass
class _AgentPhaseState:
    setup_done: bool = False
    timed_out: bool = False
    setup_state: Any = None


def run_task(
    task: Task,
    cfg: RunConfig,
    agent: Agent,
    slot: Slot | None = None,
    out_dir: str | Path | None = None,
    keep_server: bool = False,
    record: RecordOptions | None = None,
    world_template: str | Path | None = None,
) -> dict[str, Any]:
    slot = slot or Slot()
    run_id = f"{cfg.id}__{agent.spec.name}__seed{cfg.seed}__slot{slot.slot_id}"
    output = _prepare_output_dir(run_id, out_dir)

    trace = Trace(instance_id=cfg.id, agent_name=agent.spec.name, started_at=time.time())
    phase = _AgentPhaseState()
    slot_started = False
    recorder: Recorder | None = None
    final_snapshot: dict[str, Any] | None = None

    try:
        console.log(
            f"[bold cyan]{task.id}[/]: {run_id} | "
            f"slot {slot.slot_id} | ports {slot.game_port}/{slot.rcon_port}"
        )
        _start_slot(slot, cfg, world_template=Path(world_template) if world_template else None)
        slot_started = True
        try:
            server = _wait_for_slot_ready(slot)
            _configure_task_world(task, cfg, server)
            recorder = _prepare_recorder(record, output, server, cfg, trace)
            if recorder is not None and record is not None:
                _start_recorder(recorder, record, server, trace)
            _run_agent_protocol(
                task=task,
                cfg=cfg,
                agent=agent,
                server=server,
                record=record,
                recorder=recorder,
                trace=trace,
                phase=phase,
            )
        finally:
            trace.ended_at = time.time()
            trace.timed_out = phase.timed_out
            final_snapshot = _capture_final_state(task, cfg, slot, phase, trace)
            agent.stop()
            _stop_recorder_and_export(recorder, record, trace)

        report = _score_and_write_artifacts(task, cfg, trace, final_snapshot, output)
        _log_run_result(report, phase, output)
        return report
    finally:
        if keep_server and slot_started:
            console.log(f"[yellow]Keeping server container running:[/] {slot.container_name}")
        elif slot_started:
            _stop_slot(slot, quiet=True)


def _prepare_output_dir(run_id: str, out_dir: str | Path | None) -> Path:
    output = Path(out_dir) if out_dir else RESULTS_DIR / run_id
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True, exist_ok=True)
    return output


def _wait_for_slot_ready(slot: Slot) -> ServerConfig:
    server = slot.server_config()
    wait_for_ready(server, timeout=600)
    return server


def _configure_task_world(task: Task, cfg: RunConfig, server: ServerConfig) -> None:
    with rcon_session(server.host, server.rcon_port, server.rcon_password) as mcr:
        task.configure_world(mcr, cfg)


def _prepare_recorder(
    record: RecordOptions | None,
    output: Path,
    server: ServerConfig,
    cfg: RunConfig,
    trace: Trace,
) -> Recorder | None:
    if record is None:
        return None

    ok, reason = recorder_available()
    if not ok:
        console.log(f"[yellow]Recording disabled[/]: {reason}")
        trace.append(
            TraceEvent(kind="info", data={"msg": "recording disabled", "reason": reason})
        )
        return None

    record.packet_output = output / "packets.jsonl.gz"
    record.packet_manifest = output / "packets.manifest.json"
    record.replay_output = output / "recording.mcpr"
    record.host = server.host
    record.port = server.game_port
    record.target_username = cfg.username
    return Recorder(record)


def _start_recorder(
    recorder: Recorder,
    record: RecordOptions,
    server: ServerConfig,
    trace: Trace,
) -> None:
    console.log(f"Starting packet recorder -> {record.packet_output}")
    recorder.start()
    wait_for_settle(2.5)
    try:
        with rcon_session(server.host, server.rcon_port, server.rcon_password) as mcr:
            mcr.command(f"op {record.recorder_username}")
            mcr.command(f"gamemode spectator {record.recorder_username}")
    except Exception as e:
        trace.append(TraceEvent(kind="error", data={"msg": f"recorder setup failed: {e}"}))


def _run_agent_protocol(
    *,
    task: Task,
    cfg: RunConfig,
    agent: Agent,
    server: ServerConfig,
    record: RecordOptions | None,
    recorder: Recorder | None,
    trace: Trace,
    phase: _AgentPhaseState,
) -> None:
    ctx = _agent_context(task, cfg, server)
    console.log(f"Launching agent [bold]{agent.spec.name}[/] for {cfg.duration_seconds}s...")
    for event in agent.run(ctx):
        trace.append(event)
        if event.kind == "info" and event.data.get("msg") == "timeout":
            phase.timed_out = True
        if not phase.setup_done and event.kind == "ready":
            _setup_agent_after_ready(task, cfg, server, record, recorder, trace, phase)
        if event.kind == "done":
            console.log("Agent reported done.")
            break


def _agent_context(task: Task, cfg: RunConfig, server: ServerConfig):
    from mcbench.agents.base import AgentRunContext

    return AgentRunContext(
        host=server.host,
        port=server.game_port,
        username=cfg.username,
        goal=task.goal_text(cfg),
        timeout_seconds=cfg.duration_seconds,
    )


def _setup_agent_after_ready(
    task: Task,
    cfg: RunConfig,
    server: ServerConfig,
    record: RecordOptions | None,
    recorder: Recorder | None,
    trace: Trace,
    phase: _AgentPhaseState,
) -> None:
    trace.agent_ready_at = time.time()
    console.log("Agent spawned; applying task kit and timer setup...")
    with rcon_session(server.host, server.rcon_port, server.rcon_password) as mcr:
        phase.setup_state = task.setup_agent(mcr, cfg)
        if recorder is not None and record is not None:
            mcr.command(f"tp {record.recorder_username} {cfg.username}")
            mcr.command(f"spectate {cfg.username} {record.recorder_username}")
    phase.setup_done = True


def _capture_final_state(
    task: Task,
    cfg: RunConfig,
    slot: Slot,
    phase: _AgentPhaseState,
    trace: Trace,
) -> dict[str, Any] | None:
    if not phase.setup_done:
        return None

    console.log("Capturing task final state...")
    try:
        with rcon_session(slot.host, slot.rcon_port, slot.rcon_password) as mcr:
            final_snapshot = task.capture(mcr, cfg, phase.setup_state)
            trace.final_state = final_snapshot["final_state"]
            return final_snapshot
    except Exception as e:
        final_snapshot = {
            "error": f"snapshot failed: {e}",
            "deaths": 0,
            "alive": False,
        }
        trace.append(TraceEvent(kind="error", data=final_snapshot))
        return final_snapshot


def _stop_recorder_and_export(
    recorder: Recorder | None,
    record: RecordOptions | None,
    trace: Trace,
) -> None:
    if recorder is None or record is None:
        return

    console.log("Stopping recorder...")
    recorder.stop()
    if record.packet_output and record.packet_output.exists():
        try:
            replay_path = export_mcpr(record.packet_output, output=record.replay_output)
            console.log(f"ReplayMod recording saved: {replay_path}")
        except Exception as e:
            trace.append(TraceEvent(kind="error", data={"msg": f"replay export failed: {e}"}))
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


def _score_and_write_artifacts(
    task: Task,
    cfg: RunConfig,
    trace: Trace,
    final_snapshot: dict[str, Any] | None,
    output: Path,
) -> dict[str, Any]:
    report = task.score(cfg, trace, final_snapshot or {})
    (output / "trace.json").write_text(trace.model_dump_json(indent=2))
    (output / "score.json").write_text(json.dumps(report, indent=2))
    (output / "config.json").write_text(cfg.model_dump_json(indent=2))
    return report


def _log_run_result(report: dict[str, Any], phase: _AgentPhaseState, output: Path) -> None:
    if not phase.setup_done:
        console.log(
            "[yellow]Agent never reported `ready`[/]: no kit/spawn setup ran. "
            "Score reflects a failed agent, not a poor strategy."
        )
    console.log(f"[bold green]Score[/]: {report['score']:.1f} / {report['max_score']:.1f}")
    console.log(f"Artifacts: {output}")
