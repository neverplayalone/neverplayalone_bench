from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from npabench.agents.base import AgentSpec
from npabench.config import DEFAULT_BASE_GAME_PORT, DEFAULT_BASE_RCON_PORT, RESULTS_DIR
from npabench.evaluation.batch_runner import run_batch_evaluation
from npabench.evaluation.reference_world import ReferenceWorldBuilder
from npabench.evaluation.run_slot import AgentRunSlot
from npabench.evaluation.single_runner import run_single_evaluation
from npabench.missions.base import MissionConfig
from npabench.missions.registry import get_mission


class AgentMode(Enum):
    SANDBOXED = "sandboxed"
    HOST = "host"


@dataclass(frozen=True)
class AgentRunReport:
    agent_name: str
    agent_kind: str | None
    mission_id: str
    task_id: str
    seed: int
    minecraft_seed: int
    score: float
    max_score: float
    status: Literal["ok", "agent_never_spawned", "timeout", "error"]
    output_dir: Path
    trace_path: Path
    recording_path: Path | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class AgentBatchReport:
    mission_id: str
    task_id: str
    seed: int
    minecraft_seed: int
    agents: dict[str, AgentRunReport]
    output_dir: Path


def evaluate_single_agent(
    agent: AgentSpec | str | Path,
    *,
    mission_id: str = "resource_gathering",
    seed: int = 0,
    config_path: Path | None = None,
    output_dir: Path | None = None,
    record: bool = True,
    agent_mode: AgentMode = AgentMode.SANDBOXED,
    base_game_port: int = DEFAULT_BASE_GAME_PORT,
    base_rcon_port: int = DEFAULT_BASE_RCON_PORT,
    sidecar_containers: tuple[str, ...] = (),
) -> AgentRunReport:
    mission = get_mission(mission_id)
    base_config = _load_mission_config(mission, config_path)
    task = mission.generate_task(base_config, seed)
    run_agent = _normalize_agent(agent)
    root_output_dir = _default_run_output_dir(output_dir)
    root_output_dir.mkdir(parents=True, exist_ok=True)
    task = mission.materialize_task(base_config, task, root_output_dir)
    mission_config = mission.build_mission_config(base_config, task)
    (root_output_dir / "task.json").write_text(task.model_dump_json(indent=2))
    reference_world_dir = ReferenceWorldBuilder().build(
        mission,
        mission_config,
        root_output_dir / "reference_world",
        base_game_port=base_game_port,
        base_rcon_port=base_rcon_port,
    )
    agent_output_dir = root_output_dir / "agents" / safe_name(run_agent.name)
    agent_run_slot = AgentRunSlot.allocate(
        slot_id=0,
        base_game_port=base_game_port,
        base_rcon_port=base_rcon_port,
        data_root=agent_output_dir / "_slot",
        sidecar_containers=sidecar_containers,
    )
    return run_single_evaluation(
        mission,
        mission_config,
        agent_run_slot,
        run_agent,
        reference_world_dir=reference_world_dir,
        recording=record,
        agent_mode=agent_mode,
        output_dir=agent_output_dir,
        task_seed=task.seed,
    )


def evaluate_multiple_agents(
    agents: list[AgentSpec | str | Path],
    *,
    mission_id: str = "resource_gathering",
    seed: int = 0,
    config_path: Path | None = None,
    output_dir: Path | None = None,
    record: bool = True,
    agent_mode: AgentMode = AgentMode.SANDBOXED,
    max_parallel: int = 1,
    base_game_port: int = DEFAULT_BASE_GAME_PORT,
    base_rcon_port: int = DEFAULT_BASE_RCON_PORT,
    sidecar_containers: tuple[str, ...] = (),
) -> AgentBatchReport:
    if not agents:
        raise ValueError("evaluate_multiple_agents requires at least one agent")
    mission = get_mission(mission_id)
    base_config = _load_mission_config(mission, config_path)
    task = mission.generate_task(base_config, seed)
    normalized_agents = [_normalize_agent(agent) for agent in agents]
    _assert_unique_agent_names(normalized_agents)
    root_output_dir = _default_run_output_dir(output_dir)
    root_output_dir.mkdir(parents=True, exist_ok=True)
    task = mission.materialize_task(base_config, task, root_output_dir)
    mission_config = mission.build_mission_config(base_config, task)
    (root_output_dir / "task.json").write_text(task.model_dump_json(indent=2))
    reference_world_dir = ReferenceWorldBuilder().build(
        mission,
        mission_config,
        root_output_dir / "reference_world",
        base_game_port=base_game_port,
        base_rcon_port=base_rcon_port,
    )
    agent_run_slots = [
        AgentRunSlot.allocate(
            slot_id=index,
            base_game_port=base_game_port,
            base_rcon_port=base_rcon_port,
            data_root=(root_output_dir / "agents" / safe_name(agent_spec.name) / "_slot"),
            sidecar_containers=sidecar_containers,
        )
        for index, agent_spec in enumerate(normalized_agents)
    ]
    reports = run_batch_evaluation(
        mission,
        mission_config,
        agent_run_slots,
        normalized_agents,
        reference_world_dir=reference_world_dir,
        recording=record,
        agent_mode=agent_mode,
        output_dir=root_output_dir / "agents",
        max_parallel=max_parallel,
        task_seed=task.seed,
    )
    batch_report = AgentBatchReport(
        mission_id=mission_id,
        task_id=task.task_id,
        seed=seed,
        minecraft_seed=task.minecraft_seed,
        agents=reports,
        output_dir=root_output_dir,
    )
    (root_output_dir / "batch_report.json").write_text(
        json.dumps(
            {
                "mission_id": batch_report.mission_id,
                "task_id": batch_report.task_id,
                "seed": batch_report.seed,
                "minecraft_seed": batch_report.minecraft_seed,
                "agents": {
                    agent_name: {
                        "agent_name": report.agent_name,
                        "agent_kind": report.agent_kind,
                        "mission_id": report.mission_id,
                        "task_id": report.task_id,
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
                    }
                    for agent_name, report in batch_report.agents.items()
                },
            },
            indent=2,
        )
    )
    return batch_report


def parse_agent_assignment(raw: str) -> AgentSpec:
    if "=" in raw:
        name, path_raw = raw.split("=", 1)
        if not name:
            raise ValueError(f"invalid agent assignment {raw!r}: missing name")
    else:
        path_raw = raw
        name = Path(path_raw).name
    path = Path(path_raw).resolve()
    if not path.exists():
        raise ValueError(f"agent path does not exist: {path}")
    return AgentSpec(name=name, path=path)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "agent"


def _load_mission_config(mission, config_path: Path | None) -> MissionConfig:
    selected_config_path = config_path or mission.default_config_path()
    if not selected_config_path.exists():
        raise ValueError(f"config file does not exist: {selected_config_path}")
    return mission.load_config(selected_config_path)


def _default_run_output_dir(output_dir: Path | None) -> Path:
    if output_dir is not None:
        return output_dir.resolve()
    return (RESULTS_DIR / _run_id_now()).resolve()


def _run_id_now() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _normalize_agent(agent: AgentSpec | str | Path) -> AgentSpec:
    if isinstance(agent, AgentSpec):
        return AgentSpec(
            name=agent.name,
            path=agent.path.resolve(),
            extra_args=agent.extra_args,
            kind=agent.kind,
            env=agent.env,
        )
    if isinstance(agent, str):
        return parse_agent_assignment(agent)
    path = agent.resolve()
    if not path.exists():
        raise ValueError(f"agent path does not exist: {path}")
    return AgentSpec(name=path.name, path=path)


def _assert_unique_agent_names(agent_specs: list[AgentSpec]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    seen_output_names: set[str] = set()
    duplicate_output_names: set[str] = set()
    for agent_spec in agent_specs:
        if agent_spec.name in seen:
            duplicates.add(agent_spec.name)
        seen.add(agent_spec.name)
        output_name = safe_name(agent_spec.name)
        if output_name in seen_output_names:
            duplicate_output_names.add(output_name)
        seen_output_names.add(output_name)
    if duplicates:
        names = ", ".join(sorted(duplicates))
        raise ValueError(f"duplicate agent names are not allowed: {names}")
    if duplicate_output_names:
        names = ", ".join(sorted(duplicate_output_names))
        raise ValueError(f"agent names must have unique output-safe names: {names}")
