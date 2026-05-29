"""Runner: glue the server, agent, and grader together for a single task run."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from mcrcon import MCRcon
from rich.console import Console

from .agents import Agent
from .agents.base import AgentRunContext
from .config import TaskConfig
from .grader import grade
from .rcon import rcon_session, run_commands
from .recorder import Recorder, RecordOptions, is_available as recorder_available, wait_for_settle
from .replay_tool import export_mcpr
from .server import ServerConfig, clean_world_inplace, wait_for_ready
from .trace import FinalState, Trace, TraceEvent

console = Console()

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "results"
RECORDING_DIR = REPO_ROOT / "recording"

USERNAME = "BenchmarkBot"


def _setup_commands_for_agent(commands: list[str], username: str) -> list[str]:
    """Keep sidecar players such as RecorderCam from matching task-local @p."""
    return [cmd.replace("@p", username) for cmd in commands]


def _stat_objectives(task: TaskConfig) -> list[tuple[str, str, str, str]]:
    """Server-side statistic objectives needed to grade a task's rules.

    Returns (objective_name, criterion, rule_kind, target) per rule that must be
    measured from the server rather than the agent's self-report. This is what
    makes blocks_broken/blocks_placed/entities_killed trustworthy — the agent
    can't inflate a statistic it doesn't control.
    """
    out: list[tuple[str, str, str, str]] = []
    for i, rule in enumerate(task.success.rules):
        if rule.kind == "blocks_broken" and rule.block:
            mc_id = rule.block.split(":")[-1]
            out.append((f"mcb_mined_{i}", f"minecraft.mined:minecraft.{mc_id}", rule.kind, rule.block))
        elif rule.kind == "blocks_placed" and rule.block:
            mc_id = rule.block.split(":")[-1]
            out.append((f"mcb_used_{i}", f"minecraft.used:minecraft.{mc_id}", rule.kind, rule.block))
        elif rule.kind == "entities_killed" and rule.entity:
            mc_id = rule.entity.split(":")[-1]
            out.append((f"mcb_kill_{i}", f"minecraft.killed:minecraft.{mc_id}", rule.kind, rule.entity))
    return out


def _read_score(mcr: MCRcon, username: str, objective: str) -> int:
    """Read a player's scoreboard value; 0 when unset/unknown."""
    raw = mcr.command(f"scoreboard players get {username} {objective}")
    m = re.search(r"has (-?\d+)", raw)
    return int(m.group(1)) if m else 0


def _snapshot_final_state(
    mcr: MCRcon,
    username: str,
    stat_objs: list[tuple[str, str, str, str]],
    stat_baseline: dict[str, int],
) -> FinalState:
    """Pull a final-state snapshot from the server via RCON."""
    state = FinalState()

    # Position via /data get
    raw = mcr.command(f"data get entity {username} Pos")
    state.position = _parse_pos(raw)

    raw = mcr.command(f"data get entity {username} Health")
    state.health = _parse_scalar(raw)

    raw = mcr.command(f"data get entity {username} foodLevel")
    state.food = _parse_scalar(raw)

    # Inventory
    raw = mcr.command(f"data get entity {username} Inventory")
    state.inventory = _parse_inventory(raw)

    # Server-authoritative counters: episode delta = final stat - baseline at setup.
    # Statistic objectives mirror the player's lifetime stats, which persist across
    # runs, so we measure the per-episode change rather than the absolute value.
    for name, _criterion, kind, target in stat_objs:
        delta = max(0, _read_score(mcr, username, name) - stat_baseline.get(name, 0))
        if kind == "blocks_broken":
            state.blocks_broken[target] = delta
        elif kind == "blocks_placed":
            state.blocks_placed[target] = delta
        elif kind == "entities_killed":
            state.entities_killed[target] = delta

    return state


def _fill_inventory_from_agent_events(trace: Trace) -> None:
    if trace.final_state.inventory:
        return
    for event in reversed(trace.events):
        inventory = event.data.get("inventory")
        if not isinstance(inventory, dict):
            continue
        parsed: dict[str, int] = {}
        for key, value in inventory.items():
            if isinstance(key, str) and isinstance(value, int):
                parsed[key] = value
        if parsed:
            trace.final_state.inventory = parsed
            trace.append(
                TraceEvent(
                    kind="info",
                    data={"msg": "used agent-reported inventory fallback"},
                )
            )
            return


_NUM = r"-?\d+(?:\.\d+)?"


def _parse_pos(raw: str) -> tuple[float, float, float] | None:
    m = re.search(rf"\[({_NUM})d?, ({_NUM})d?, ({_NUM})d?\]", raw)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


def _parse_scalar(raw: str) -> float | None:
    m = re.search(rf"({_NUM})[a-zA-Z]?\s*$", raw.strip())
    return float(m.group(1)) if m else None


def _parse_inventory(raw: str) -> dict[str, int]:
    """Very rough parser of `/data get entity ... Inventory` output.

    The server returns SNBT like:
        ... [{Slot:0b, id:"minecraft:oak_log", Count:5b}, ...]
    We pull (id, Count) pairs without trying to be exhaustive.
    """
    inv: dict[str, int] = {}
    pairs = [
        (item_id, count)
        for item_id, count in re.findall(
            r'id\s*:\s*"minecraft:([^"]+)".{0,500}?count\s*:\s*(\d+)',
            raw,
            re.IGNORECASE | re.DOTALL,
        )
    ]
    pairs.extend(
        (item_id, count)
        for count, item_id in re.findall(
            r'count\s*:\s*(\d+).{0,500}?id\s*:\s*"minecraft:([^"]+)"',
            raw,
            re.IGNORECASE | re.DOTALL,
        )
    )
    for item_id, count in pairs:
        inv[item_id] = inv.get(item_id, 0) + int(count)
    return inv


def run_task(
    task: TaskConfig,
    agent: Agent,
    server: ServerConfig | None = None,
    record: RecordOptions | None = None,
    reset: bool = True,
) -> Trace:
    server = server or ServerConfig()
    # Stable per-task dir: re-running a task overwrites its previous result
    # rather than accumulating uuid-suffixed dirs. (Generated task ids already
    # carry the seed, so distinct tasks never collide.)
    run_id = task.id
    out_dir = RESULTS_DIR / run_id
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    console.log(f"[bold cyan]Run[/]: {run_id}")
    console.log("Waiting for server ready…")
    wait_for_ready(server)
    if reset:
        # Clean terrain/entities from previous runs so they don't leak into this
        # one. Without this, blocks placed by a build task or a task's /fill setup
        # persist across runs and break reproducibility. Done in place over RCON
        # (no container restart) to keep it fast.
        console.log("Resetting world for a clean run…")
        clean_world_inplace(server, radius=task.reset_radius, ceiling=task.reset_ceiling)

    trace = Trace(task_id=task.id, agent_name=agent.spec.name, started_at=time.time())

    stat_objs = _stat_objectives(task)
    stat_baseline: dict[str, int] = {}

    # 1. World-level setup that doesn't depend on the player existing
    console.log("Setting world gamerules…")
    with rcon_session(server.host, server.rcon_port, server.rcon_password) as mcr:
        mcr.command("gamerule doDaylightCycle false")
        mcr.command("gamerule doWeatherCycle false")
        mcr.command("time set day")

    # 2. Optional: start the recorder sidecar so it's spectating before the agent acts.
    recorder: Recorder | None = None
    if record is not None:
        ok, reason = recorder_available()
        if not ok:
            console.log(f"[yellow]Recording disabled[/]: {reason}")
        else:
            # Packet stream + manifest are intermediates (kept in the task dir,
            # pruned after a successful export); the .mcpr is gathered into a
            # single top-level recording/ folder, named by task id.
            record.packet_output = out_dir / "packets.jsonl.gz"
            record.packet_manifest = out_dir / "packets.manifest.json"
            RECORDING_DIR.mkdir(parents=True, exist_ok=True)
            record.replay_output = RECORDING_DIR / f"{task.id}.mcpr"
            record.host = server.host
            record.port = server.game_port
            record.target_username = USERNAME
            recorder = Recorder(record)
            console.log(f"Starting packet recorder → {record.packet_output}")
            recorder.start()
            # Recorder needs to connect before the agent starts moving so the
            # ReplayMod file includes the initial world state.
            wait_for_settle(2.5)
            try:
                with rcon_session(server.host, server.rcon_port, server.rcon_password) as mcr:
                    mcr.command(f"op {record.recorder_username}")
                    mcr.command(f"gamemode spectator {record.recorder_username}")
            except Exception as e:
                trace.append(
                    TraceEvent(kind="error", data={"msg": f"recorder setup failed: {e}"})
                )

    # 3. Launch the agent. It must emit a {"kind":"ready"} event once spawned.
    console.log(f"Launching agent [bold]{agent.spec.name}[/] (timeout {task.timeout_seconds}s)…")
    ctx = AgentRunContext(
        host=server.host,
        port=server.game_port,
        username=USERNAME,
        goal=task.goal,
        timeout_seconds=task.timeout_seconds,
    )

    setup_done = False
    final_state_captured = False
    try:
        for event in agent.run(ctx):
            trace.append(event)

            # On the agent's first "ready" event: op the bot and run per-task setup
            # commands. These reference USERNAME, so they need the player to exist.
            if not setup_done and event.kind == "ready":
                console.log(f"Agent spawned; running {len(task.setup.commands)} setup commands…")
                try:
                    with rcon_session(
                        server.host, server.rcon_port, server.rcon_password
                    ) as mcr:
                        mcr.command(f"op {USERNAME}")
                        mcr.command(f"clear {USERNAME}")
                        mcr.command("kill @e[type=item]")
                        # Register server-authoritative counters and record their
                        # baseline so grading measures this episode's delta.
                        for name, criterion, _, _ in stat_objs:
                            mcr.command(f"scoreboard objectives add {name} {criterion}")
                        for name, _, _, _ in stat_objs:
                            stat_baseline[name] = _read_score(mcr, USERNAME, name)
                        run_commands(mcr, _setup_commands_for_agent(task.setup.commands, USERNAME))
                        mcr.command(f"gamemode survival {USERNAME}")
                        if recorder is not None:
                            mcr.command(f"tp {record.recorder_username} {USERNAME}")
                            mcr.command(f"spectate {USERNAME} {record.recorder_username}")
                        # De-op the bot for the actual run so the agent can't cheat
                        # via chat commands (e.g. /give, /kill). Setup ran as console
                        # over RCON, which keeps full permissions regardless.
                        mcr.command(f"deop {USERNAME}")
                except Exception as e:
                    trace.append(
                        TraceEvent(kind="error", data={"msg": f"setup failed: {e}"})
                    )
                setup_done = True
            if event.kind == "done":
                console.log("Agent reported done.")
                break
    finally:
        if setup_done:
            console.log("Capturing final state…")
            try:
                with rcon_session(server.host, server.rcon_port, server.rcon_password) as mcr:
                    trace.final_state = _snapshot_final_state(mcr, USERNAME, stat_objs, stat_baseline)
                final_state_captured = True
            except Exception as e:
                trace.append(TraceEvent(kind="error", data={"msg": f"snapshot failed: {e}"}))
        agent.stop()
        if recorder is not None:
            console.log("Stopping recorder…")
            recorder.stop()
            if record and record.packet_output and record.packet_output.exists():
                if record.replay_output:
                    try:
                        replay_path = export_mcpr(record.packet_output, output=record.replay_output)
                        console.log(f"ReplayMod recording saved: {replay_path}")
                        # Export succeeded → drop the raw packet intermediates.
                        record.packet_output.unlink(missing_ok=True)
                        if record.packet_manifest:
                            record.packet_manifest.unlink(missing_ok=True)
                    except Exception as e:
                        # Keep the packet log so the user can retry `replay export-mcpr`.
                        trace.append(
                            TraceEvent(kind="error", data={"msg": f"replay export failed: {e}"})
                        )
                        console.log(
                            f"[yellow]Export failed; kept packet log for retry: "
                            f"{record.packet_output}[/]"
                        )
            else:
                console.log("[yellow]Packet recording produced no output.[/]")
                if recorder.stderr_log:
                    console.log("[yellow]Recorder stderr (tail):[/]")
                    for line in recorder.stderr_log[-40:]:
                        console.log(f"  {line}")

    trace.ended_at = time.time()

    if not final_state_captured:
        console.log("Capturing final state…")
        try:
            with rcon_session(server.host, server.rcon_port, server.rcon_password) as mcr:
                trace.final_state = _snapshot_final_state(mcr, USERNAME, stat_objs, stat_baseline)
        except Exception as e:  # don't fail the whole run because of a snapshot hiccup
            trace.append(TraceEvent(kind="error", data={"msg": f"snapshot failed: {e}"}))

    _fill_inventory_from_agent_events(trace)

    # 3. Grade
    report = grade(task, trace)
    (out_dir / "trace.json").write_text(trace.model_dump_json(indent=2))
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    console.log(f"[bold green]Result[/]: {report['outcome']} (score={report['score']:.2f})")
    console.log(f"Artifacts: {out_dir}")
    return trace
