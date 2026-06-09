"""Resource-gathering competition runner.

A resource competition run evaluates one miner in one isolated Minecraft world
for a fixed wall-clock duration, then scores resources kept in the miner's
inventory if the miner returns near spawn.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from mcrcon import MCRcon
from pydantic import BaseModel, Field, field_validator
from rich.console import Console

from .agents import Agent
from .agents.base import AgentRunContext
from .recorder import Recorder, RecordOptions, is_available as recorder_available, wait_for_settle
from .rcon import rcon_session
from .replay_tool import export_mcpr
from .server import DOCKER_DIR, REPO_ROOT, ServerConfig, wait_for_ready
from .trace import FinalState, Trace, TraceEvent

console = Console()

USERNAME = "BenchmarkBot"
COMPETITION_RESULTS_DIR = REPO_ROOT / "results" / "resource_gathering"
RETURN_RADIUS_BLOCKS = 20.0
SPAWN_SEARCH_RADIUS = 16
SPAWN_SEARCH_UP = 4
SPAWN_SEARCH_DOWN = 8
SPAWN_SEARCH_MAX_CANDIDATES = 512
AIR_BLOCKS = ("minecraft:air", "minecraft:cave_air", "minecraft:void_air")
SAFE_SPAWN_GROUND_BLOCKS = (
    "minecraft:grass_block",
    "minecraft:dirt",
    "minecraft:coarse_dirt",
    "minecraft:podzol",
    "minecraft:stone",
    "minecraft:deepslate",
    "minecraft:sand",
    "minecraft:red_sand",
    "minecraft:gravel",
    "minecraft:snow_block",
)
BAD_SPAWN_BLOCKS = (
    "minecraft:water",
    "minecraft:lava",
    "minecraft:powder_snow",
    "minecraft:fire",
    "minecraft:soul_fire",
    "minecraft:cactus",
)


class KitItem(BaseModel):
    item: str
    count: int = 1
    enchantments: list[str] = Field(default_factory=list)
    slot: str | None = None

    @field_validator("count")
    @classmethod
    def count_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("kit item count must be positive")
        return value


class ResourceTarget(BaseModel):
    item: str
    items: list[str] = Field(default_factory=list)
    target_count: int
    points: float = 100.0

    @field_validator("target_count")
    @classmethod
    def target_count_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("resource target_count must be positive")
        return value


class CompetitionScoringConfig(BaseModel):
    survival_points: float = 50.0
    efficiency_points: float = 50.0
    efficiency_min_resource_score: float = 100.0


class ResourceCompetitionConfig(BaseModel):
    id: str = "resource_gathering_v1"
    seed: int = 0
    minecraft_version: str = "1.21.11"
    world_type: str = "normal"
    generate_structures: bool = True
    difficulty: Literal["peaceful", "easy", "normal", "hard"] = "normal"
    memory: str = "2G"
    duration_seconds: int = 1200
    spawn_time: int = 0
    username: str = USERNAME
    goal: str = "Gather the requested resource before sunset."
    kit: list[KitItem] = Field(default_factory=list)
    resources: list[ResourceTarget] = Field(default_factory=list)
    scoring: CompetitionScoringConfig = Field(default_factory=CompetitionScoringConfig)

    @field_validator("duration_seconds")
    @classmethod
    def duration_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("duration_seconds must be positive")
        return value


@dataclass(frozen=True)
class CompetitionSlot:
    """One isolated evaluation slot.

    Parallelism later is just multiple slots with different ids/ports/data dirs.
    """

    slot_id: int = 0
    host: str = "127.0.0.1"
    base_game_port: int = 25565
    base_rcon_port: int = 25575
    rcon_password: str = "mcbench"
    container_prefix: str = "mcbench-resource"
    data_root: Path = COMPETITION_RESULTS_DIR / "slots"

    @property
    def game_port(self) -> int:
        return self.base_game_port + self.slot_id

    @property
    def rcon_port(self) -> int:
        return self.base_rcon_port + self.slot_id

    @property
    def container_name(self) -> str:
        return f"{self.container_prefix}-{self.slot_id}"

    @property
    def data_dir(self) -> Path:
        return self.data_root / f"slot-{self.slot_id}" / "data"

    def server_config(self) -> ServerConfig:
        return ServerConfig(
            host=self.host,
            game_port=self.game_port,
            rcon_port=self.rcon_port,
            rcon_password=self.rcon_password,
        )


def load_resource_competition_config(path: str | Path) -> ResourceCompetitionConfig:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return ResourceCompetitionConfig.model_validate(raw)


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


def score_resource_gathering(
    cfg: ResourceCompetitionConfig,
    trace: Trace,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = snapshot or {}
    inventory = trace.final_state.inventory
    resources: list[dict[str, Any]] = []
    resource_score = 0.0
    max_resource_score = 0.0
    within_return_radius = bool(snapshot.get("within_return_radius", True))

    for spec in cfg.resources:
        count = _resource_count(inventory, spec)
        achieved = min(count, spec.target_count) if within_return_radius else 0
        score = spec.points * achieved / spec.target_count
        max_resource_score += spec.points
        resource_score += score
        resources.append(
            {
                "item": spec.item,
                "items": _counted_items(spec),
                "count": count,
                "target_count": spec.target_count,
                "achieved": achieved,
                "points": score,
                "max_points": spec.points,
            }
        )

    deaths = int(snapshot.get("deaths", 0) or 0)
    alive = bool(snapshot.get("alive", False))
    survival_score = 0.0
    if alive and deaths == 0:
        survival_score = cfg.scoring.survival_points
    elif resource_score > 0 and deaths > 0:
        survival_score = cfg.scoring.survival_points / 2

    elapsed = max(0.0, (trace.ended_at or time.time()) - trace.started_at)
    efficiency_score = 0.0
    finished_early = not trace.timed_out and any(e.kind == "done" for e in trace.events)
    if finished_early and resource_score >= cfg.scoring.efficiency_min_resource_score:
        remaining_ratio = max(0.0, (cfg.duration_seconds - elapsed) / cfg.duration_seconds)
        efficiency_score = cfg.scoring.efficiency_points * remaining_ratio

    max_score = (
        max_resource_score + cfg.scoring.survival_points + cfg.scoring.efficiency_points
    )
    total = resource_score + survival_score + efficiency_score
    return {
        "competition_id": cfg.id,
        "agent": trace.agent_name,
        "seed": cfg.seed,
        "score": total,
        "max_score": max_score,
        "resource_score": resource_score,
        "survival_score": survival_score,
        "efficiency_score": efficiency_score,
        "elapsed_seconds": elapsed,
        "timed_out": trace.timed_out,
        "alive": alive,
        "deaths": deaths,
        "resources": resources,
        "final_position": trace.final_state.position,
        "final_health": trace.final_state.health,
        "spawn": snapshot.get("spawn"),
        "distance_from_spawn": snapshot.get("distance_from_spawn"),
        "return_radius": snapshot.get("return_radius", RETURN_RADIUS_BLOCKS),
        "within_return_radius": within_return_radius,
    }


def _start_slot(
    slot: CompetitionSlot,
    cfg: ResourceCompetitionConfig,
    world_template: Path | None = None,
) -> None:
    _stop_slot(slot, quiet=True)
    shutil.rmtree(slot.data_dir, ignore_errors=True)
    if world_template is not None:
        if not world_template.exists():
            raise RuntimeError(f"world template does not exist: {world_template}")
        shutil.copytree(world_template, slot.data_dir)
    else:
        slot.data_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        slot.container_name,
        "-p",
        f"{slot.game_port}:25565",
        "-p",
        f"{slot.rcon_port}:25575",
        "-v",
        f"{slot.data_dir}:/data",
        "-v",
        f"{DOCKER_DIR / 'bukkit.yml'}:/data/bukkit.yml:ro",
        "-e",
        "EULA=TRUE",
        "-e",
        "TYPE=PAPER",
        "-e",
        f"VERSION={cfg.minecraft_version}",
        "-e",
        f"MEMORY={cfg.memory}",
        "-e",
        "ONLINE_MODE=FALSE",
        "-e",
        "ENABLE_RCON=TRUE",
        "-e",
        f"RCON_PASSWORD={slot.rcon_password}",
        "-e",
        "RCON_PORT=25575",
        "-e",
        "MODE=survival",
        "-e",
        f"DIFFICULTY={cfg.difficulty}",
        "-e",
        f"LEVEL_TYPE={cfg.world_type}",
        "-e",
        f"GENERATE_STRUCTURES={str(cfg.generate_structures).upper()}",
        "-e",
        "SPAWN_PROTECTION=0",
        "-e",
        "VIEW_DISTANCE=10",
        "-e",
        "ALLOW_FLIGHT=TRUE",
        "-e",
        f"SEED={cfg.seed}",
        "itzg/minecraft-server:latest",
    ]
    _run(cmd, f"starting competition slot {slot.slot_id}")


def _stop_slot(slot: CompetitionSlot, quiet: bool = False) -> None:
    result = subprocess.run(
        ["docker", "rm", "-f", slot.container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and not quiet and "No such container" not in result.stderr:
        raise RuntimeError(
            f"docker rm -f {slot.container_name} failed\n"
            f"--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
        )


def _run(cmd: list[str], label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"{label} failed (exit {result.returncode})\n"
            f"command: {' '.join(cmd)}\n"
            f"--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
        )
    return result


def _configure_world_start(server: ServerConfig, cfg: ResourceCompetitionConfig) -> None:
    with rcon_session(server.host, server.rcon_port, server.rcon_password) as mcr:
        mcr.command("gamerule keep_inventory false")
        mcr.command("gamerule advance_time true")
        mcr.command("gamerule advance_weather true")
        mcr.command(f"difficulty {cfg.difficulty}")
        mcr.command(f"time set {cfg.spawn_time}")


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


def _prepare_playable_spawn(
    mcr: MCRcon,
    username: str,
) -> tuple[int, int, int]:
    pos = _parse_pos(mcr.command(f"data get entity {username} Pos"))
    if pos is None:
        raise RuntimeError(f"could not read spawn position for {username}")
    origin = (math.floor(pos[0]), math.floor(pos[1]), math.floor(pos[2]))
    spawn_pos = origin
    if not _is_playable_spawn(mcr, *spawn_pos):
        for candidate in _nearby_spawn_candidates(*origin):
            if _is_playable_spawn(mcr, *candidate):
                spawn_pos = candidate
                break
        else:
            console.log(
                "[yellow]Could not find a better local spawn; using Minecraft's spawn.[/]"
            )
    _set_exact_player_spawn(mcr, username, spawn_pos)
    return spawn_pos


def _set_exact_player_spawn(
    mcr: MCRcon,
    username: str,
    spawn_pos: tuple[int, int, int],
) -> None:
    x, y, z = spawn_pos
    mcr.command("gamerule spawnRadius 0")
    mcr.command(f"setworldspawn {x} {y} {z}")
    mcr.command(f"spawnpoint {username} {x} {y} {z}")
    mcr.command(f"tp {username} {x + 0.5} {y} {z + 0.5} 0 0")


def _nearby_spawn_candidates(x: int, y: int, z: int) -> list[tuple[int, int, int]]:
    candidates: list[tuple[int, int, int]] = []
    for dx in range(-SPAWN_SEARCH_RADIUS, SPAWN_SEARCH_RADIUS + 1):
        for dz in range(-SPAWN_SEARCH_RADIUS, SPAWN_SEARCH_RADIUS + 1):
            for cy in range(y + SPAWN_SEARCH_UP, y - SPAWN_SEARCH_DOWN - 1, -1):
                candidates.append((x + dx, cy, z + dz))
    candidates.sort(
        key=lambda pos: (
            (pos[0] - x) ** 2 + (pos[2] - z) ** 2,
            abs(pos[1] - y),
            -pos[1],
        )
    )
    return candidates[:SPAWN_SEARCH_MAX_CANDIDATES]


def _is_playable_spawn(mcr: MCRcon, x: int, y: int, z: int) -> bool:
    return (
        _is_air(mcr, x, y, z)
        and _is_air(mcr, x, y + 1, z)
        and _is_safe_spawn_ground(mcr, x, y - 1, z)
        and not _has_bad_spawn_block_nearby(mcr, x, y, z)
    )


def _is_air(mcr: MCRcon, x: int, y: int, z: int) -> bool:
    return any(_block_matches(mcr, x, y, z, block) for block in AIR_BLOCKS)


def _is_safe_spawn_ground(mcr: MCRcon, x: int, y: int, z: int) -> bool:
    return any(
        _block_matches(mcr, x, y, z, block)
        for block in SAFE_SPAWN_GROUND_BLOCKS
    )


def _has_bad_spawn_block_nearby(mcr: MCRcon, x: int, y: int, z: int) -> bool:
    positions = (
        (x, y, z),
        (x, y - 1, z),
        (x + 1, y, z),
        (x - 1, y, z),
        (x, y, z + 1),
        (x, y, z - 1),
    )
    return any(
        _block_matches(mcr, px, py, pz, block)
        for px, py, pz in positions
        for block in BAD_SPAWN_BLOCKS
    )


def _block_matches(mcr: MCRcon, x: int, y: int, z: int, block: str) -> bool:
    raw = mcr.command(f"execute if block {x} {y} {z} {block} run time query gametime")
    return _rcon_test_passed(raw)


def _rcon_test_passed(raw: str) -> bool:
    text = raw.strip().lower()
    return "time is" in text


def _give_kit_item(mcr: MCRcon, username: str, kit: KitItem) -> None:
    item = _kit_item_stack(kit)
    if kit.slot:
        mcr.command(f"item replace entity {username} {kit.slot} with {item} {kit.count}")
    else:
        mcr.command(f"give {username} {item} {kit.count}")


def _kit_item_stack(kit: KitItem) -> str:
    item = f"minecraft:{kit.item}"
    if not kit.enchantments:
        return item
    levels = {f"minecraft:{name}": level for name, level in _enchants(kit)}
    enchantments = json.dumps(levels, separators=(",", ":"))
    return f"{item}[minecraft:enchantments={enchantments}]"


def _enchants(kit: KitItem) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for raw in kit.enchantments:
        name, _, level_raw = raw.partition(":")
        level = int(level_raw or "1")
        out.append((name, level))
    return out


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
        "return_radius": RETURN_RADIUS_BLOCKS,
        "within_return_radius": (
            distance_from_spawn is not None
            and distance_from_spawn <= RETURN_RADIUS_BLOCKS
        ),
    }


def _count_item(mcr: MCRcon, username: str, item: str) -> int:
    raw = mcr.command(f"clear {username} minecraft:{item} 0")
    m = re.search(r"\b(\d+)\b", raw)
    return int(m.group(1)) if (m and "found" in raw.lower()) else 0


def _horizontal_distance_from_spawn(
    position: tuple[float, float, float] | None,
    spawn_pos: tuple[int, int, int] | None,
) -> float | None:
    if position is None or spawn_pos is None:
        return None
    return math.sqrt((position[0] - spawn_pos[0]) ** 2 + (position[2] - spawn_pos[2]) ** 2)


def _counted_items(resource: ResourceTarget) -> list[str]:
    return resource.items or [resource.item]


def _resource_count(inventory: dict[str, int], resource: ResourceTarget) -> int:
    if resource.item in inventory:
        return inventory.get(resource.item, 0)
    return sum(inventory.get(item, 0) for item in _counted_items(resource))


def _read_score(mcr: MCRcon, username: str, objective: str) -> int:
    raw = mcr.command(f"scoreboard players get {username} {objective}")
    m = re.search(r"has (-?\d+)", raw)
    return int(m.group(1)) if m else 0


_NUM = r"-?\d+(?:\.\d+)?"


def _parse_pos(raw: str) -> tuple[float, float, float] | None:
    m = re.search(rf"\[({_NUM})d?, ({_NUM})d?, ({_NUM})d?\]", raw)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


def _parse_scalar(raw: str) -> float | None:
    m = re.search(rf"({_NUM})[a-zA-Z]?\s*$", raw.strip())
    return float(m.group(1)) if m else None


def _goal_text(cfg: ResourceCompetitionConfig) -> str:
    return cfg.goal
