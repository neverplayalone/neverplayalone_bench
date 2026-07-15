from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console

from npabench.agents import create_agent, ensure_agent_image
from npabench.agents.base import Agent, AgentRunContext, AgentSpec
from npabench.evaluation.reference_world import (
    cleanup_run_worlds,
    start_agent_run_slot,
    stop_agent_run_slot,
)
from npabench.evaluation.run_slot import AgentRunSlot, ServerEndpoint
from npabench.evaluation.run_trace import AgentRunTrace, TraceEvent
from npabench.minecraft.rcon_client import rcon_session
from npabench.minecraft.server_probe import wait_for_ready
from npabench.missions.base import Mission, MissionConfig
from npabench.recording.recorder import (
    Recorder,
    RecordingOptions,
    is_available as recorder_available,
    wait_for_settle,
)
from npabench.recording.replay_exporter import export_mcpr

console = Console()

if TYPE_CHECKING:
    from npabench.evaluation.evaluate import AgentMode, AgentRunReport


@dataclass
class _AgentPhaseState:
    setup_done: bool = False
    timed_out: bool = False
    setup_state: Any = None
    status: str = "ok"


def run_single_evaluation(
    mission: Mission,
    mission_config: MissionConfig,
    agent_run_slot: AgentRunSlot,
    agent_spec: AgentSpec,
    *,
    reference_world_dir: Path,
    recording: bool,
    agent_mode: AgentMode,
    output_dir: Path,
    task_seed: int | None = None,
) -> "AgentRunReport":
    from npabench.evaluation.evaluate import AgentRunReport

    output_dir = _prepare_output_dir(output_dir)
    agent_run_trace = AgentRunTrace(
        task_id=mission_config.id,
        agent_name=agent_spec.name,
        started_at=time.time(),
    )
    phase = _AgentPhaseState()
    recorder: Recorder | None = None
    final_snapshot: dict[str, Any] | None = None
    agent = create_agent(agent_spec, agent_mode=agent_mode, agent_run_slot=agent_run_slot)
    run_error: Exception | None = None

    try:
        console.log(f"Running {agent_spec.name} on {mission.id}:{mission_config.id}")
        if getattr(agent_mode, "value", agent_mode) == "sandboxed":
            ensure_agent_image()
        start_agent_run_slot(agent_run_slot, mission_config, reference_world_dir=reference_world_dir)
        try:
            server_endpoint = _wait_for_slot_ready(agent_run_slot)
            _configure_mission_world(mission, mission_config, server_endpoint)
            recorder = _prepare_recorder(
                recording,
                output_dir,
                server_endpoint,
                mission_config,
                agent_run_trace,
            )
            if recorder is not None:
                _start_recorder(
                    recorder,
                    server_endpoint,
                    mission_config,
                    agent_run_trace,
                )
            _run_agent_protocol(
                mission=mission,
                mission_config=mission_config,
                agent=agent,
                server_endpoint=server_endpoint,
                recorder=recorder,
                agent_run_trace=agent_run_trace,
                phase=phase,
                task_seed=task_seed,
            )
        finally:
            agent_run_trace.ended_at = time.time()
            agent_run_trace.timed_out = phase.timed_out
            final_snapshot = _capture_final_state(
                mission,
                mission_config,
                agent_run_slot,
                phase,
                agent_run_trace,
            )
            agent.stop()
            _stop_recorder_and_export(recorder, agent_run_trace)

    except Exception as exc:
        # A single server, RCON, recorder, or agent failure must not abort the
        # enclosing batch.  Persist a normal zero-score report so validators can
        # upload the result and continue evaluating the remaining entries.
        run_error = exc
        phase.status = "error"
        agent_run_trace.append(
            TraceEvent(kind="error", data={"msg": f"evaluation failed: {exc}"})
        )
    finally:
        stop_agent_run_slot(agent_run_slot, quiet=True)
        cleanup_run_worlds(agent_run_slot.data_root)

    raw_report = _score_and_write_artifacts(
        mission,
        mission_config,
        agent_run_trace,
        final_snapshot,
        output_dir,
    )
    if run_error is not None:
        raw_report.update(
            {
                "score": 0.0,
                "ranking_score": 0.0,
                "status": "error",
                "error": str(run_error),
            }
        )
        (output_dir / "raw_report.json").write_text(json.dumps(raw_report, indent=2))
    status = _report_status(raw_report, phase)
    report = AgentRunReport(
        agent_name=agent_spec.name,
        agent_kind=agent_spec.kind,
        mission_id=mission.id,
        task_id=mission_config.id,
        task_prompt=mission.prompt_text(mission_config),
        seed=mission_config.seed if task_seed is None else task_seed,
        minecraft_seed=mission_config.seed,
        score=float(raw_report.get("ranking_score", raw_report.get("score", 0.0))),
        max_score=float(raw_report.get("max_score", 0.0)),
        status=status,
        output_dir=output_dir,
        trace_path=output_dir / "trace.json",
        recording_path=(output_dir / "recording.mcpr")
        if (output_dir / "recording.mcpr").exists()
        else None,
        raw=raw_report,
    )
    _write_agent_run_report(report, output_dir / "report.json")
    return report


def _prepare_output_dir(output_dir: Path) -> Path:
    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _wait_for_slot_ready(agent_run_slot: AgentRunSlot) -> ServerEndpoint:
    server_endpoint = agent_run_slot.server_endpoint()
    wait_for_ready(server_endpoint, timeout=600)
    return server_endpoint


def _configure_mission_world(
    mission: Mission,
    mission_config: MissionConfig,
    server_endpoint: ServerEndpoint,
) -> None:
    with rcon_session(
        server_endpoint.host,
        server_endpoint.rcon_port,
        server_endpoint.rcon_password,
    ) as rcon:
        mission.configure_world(rcon, mission_config)


def _prepare_recorder(
    recording: bool,
    output_dir: Path,
    server_endpoint: ServerEndpoint,
    mission_config: MissionConfig,
    agent_run_trace: AgentRunTrace,
) -> Recorder | None:
    if not recording:
        return None

    ok, reason = recorder_available()
    if not ok:
        agent_run_trace.append(
            TraceEvent(kind="info", data={"msg": "recording disabled", "reason": reason})
        )
        return None

    options = RecordingOptions(
        target_username=mission_config.username,
        packet_output=output_dir / "packets.jsonl.gz",
        packet_manifest=output_dir / "packets.manifest.json",
        replay_output=output_dir / "recording.mcpr",
        host=server_endpoint.host,
        port=server_endpoint.game_port,
    )
    return Recorder(options)


def _start_recorder(
    recorder: Recorder,
    server_endpoint: ServerEndpoint,
    mission_config: MissionConfig,
    agent_run_trace: AgentRunTrace,
) -> None:
    recorder.start()
    wait_for_settle(2.5)
    try:
        with rcon_session(
            server_endpoint.host,
            server_endpoint.rcon_port,
            server_endpoint.rcon_password,
        ) as rcon:
            rcon.command(f"op {recorder.options.recorder_username}")
            rcon.command(f"gamemode spectator {recorder.options.recorder_username}")
    except Exception as e:
        agent_run_trace.append(
            TraceEvent(kind="error", data={"msg": f"recorder setup failed: {e}"})
        )


def _run_agent_protocol(
    *,
    mission: Mission,
    mission_config: MissionConfig,
    agent: Agent,
    server_endpoint: ServerEndpoint,
    recorder: Recorder | None,
    agent_run_trace: AgentRunTrace,
    phase: _AgentPhaseState,
    task_seed: int | None,
) -> None:
    context = _agent_context(mission, mission_config, server_endpoint, task_seed=task_seed)
    for event in agent.run(context):
        agent_run_trace.append(event)
        if event.kind == "info" and event.data.get("msg") == "timeout":
            phase.timed_out = True
        if not phase.setup_done and event.kind == "ready":
            _setup_agent_after_ready(
                mission,
                mission_config,
                server_endpoint,
                recorder,
                agent_run_trace,
                phase,
            )
        if event.kind == "done":
            break


def _agent_context(
    mission: Mission,
    mission_config: MissionConfig,
    server_endpoint: ServerEndpoint,
    *,
    task_seed: int | None,
) -> AgentRunContext:
    return AgentRunContext(
        host=server_endpoint.host,
        port=server_endpoint.game_port,
        username=mission_config.username,
        prompt=mission.prompt_text(mission_config),
        timeout_seconds=mission_config.duration_seconds,
    )


def _setup_agent_after_ready(
    mission: Mission,
    mission_config: MissionConfig,
    server_endpoint: ServerEndpoint,
    recorder: Recorder | None,
    agent_run_trace: AgentRunTrace,
    phase: _AgentPhaseState,
) -> None:
    agent_run_trace.agent_ready_at = time.time()
    with rcon_session(
        server_endpoint.host,
        server_endpoint.rcon_port,
        server_endpoint.rcon_password,
    ) as rcon:
        phase.setup_state = mission.setup_agent(rcon, mission_config)
        if recorder is not None:
            recorder_username = recorder.options.recorder_username
            rcon.command(f"tp {recorder_username} {mission_config.username}")
            rcon.command(f"spectate {mission_config.username} {recorder_username}")
    phase.setup_done = True


def _capture_final_state(
    mission: Mission,
    mission_config: MissionConfig,
    agent_run_slot: AgentRunSlot,
    phase: _AgentPhaseState,
    agent_run_trace: AgentRunTrace,
) -> dict[str, Any] | None:
    if not phase.setup_done:
        return None

    try:
        with rcon_session(
            agent_run_slot.host,
            agent_run_slot.rcon_port,
            agent_run_slot.rcon_password,
        ) as rcon:
            final_snapshot = mission.collect_final_state(rcon, mission_config, phase.setup_state)
            agent_run_trace.final_state = final_snapshot["final_state"]
            return final_snapshot
    except Exception as e:
        final_snapshot = {
            "error": f"snapshot failed: {e}",
            "deaths": 0,
            "alive": False,
        }
        agent_run_trace.append(TraceEvent(kind="error", data=final_snapshot))
        return final_snapshot


def _stop_recorder_and_export(
    recorder: Recorder | None,
    agent_run_trace: AgentRunTrace,
) -> None:
    if recorder is None:
        return

    recorder.stop()
    recording_options = recorder.options
    if recording_options.packet_output and recording_options.packet_output.exists():
        try:
            export_mcpr(recording_options.packet_output, output=recording_options.replay_output)
        except Exception as e:
            agent_run_trace.append(
                TraceEvent(kind="error", data={"msg": f"replay export failed: {e}"})
            )
    else:
        agent_run_trace.append(
            TraceEvent(kind="error", data={"msg": "packet recording produced no output"})
        )


def _score_and_write_artifacts(
    mission: Mission,
    mission_config: MissionConfig,
    agent_run_trace: AgentRunTrace,
    final_snapshot: dict[str, Any] | None,
    output_dir: Path,
) -> dict[str, Any]:
    raw_report = mission.score(mission_config, agent_run_trace, final_snapshot or {})
    (output_dir / "trace.json").write_text(agent_run_trace.model_dump_json(indent=2))
    (output_dir / "raw_report.json").write_text(json.dumps(raw_report, indent=2))
    (output_dir / "config.json").write_text(mission_config.model_dump_json(indent=2))
    return raw_report


def _report_status(
    raw_report: dict[str, Any],
    phase: _AgentPhaseState,
) -> str:
    if raw_report.get("status") == "error":
        return "error"
    if raw_report.get("status") == "agent_never_spawned":
        return "agent_never_spawned"
    if phase.timed_out:
        return "timeout"
    return "ok"


def _write_agent_run_report(report: AgentRunReport, output_path: Path) -> None:
    output_path.write_text(
        json.dumps(
            {
                "agent_name": report.agent_name,
                "agent_kind": report.agent_kind,
                "mission_id": report.mission_id,
                "task_id": report.task_id,
                "task_prompt": report.task_prompt,
                "seed": report.seed,
                "minecraft_seed": report.minecraft_seed,
                "score": report.score,
                "max_score": report.max_score,
                "status": report.status,
                "output_dir": str(report.output_dir),
                "trace_path": str(report.trace_path),
                "recording_path": str(report.recording_path)
                if report.recording_path is not None
                else None,
                "raw": report.raw,
            },
            indent=2,
        )
    )
