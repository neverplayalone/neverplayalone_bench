from __future__ import annotations

import concurrent.futures
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from npabench.agents import ensure_agent_image
from npabench.agents.base import AgentSpec
from npabench.evaluation.run_slot import AgentRunSlot
from npabench.evaluation.single_runner import run_single_evaluation
from npabench.missions.base import Mission, MissionConfig

if TYPE_CHECKING:
    from npabench.evaluation.evaluate import AgentRunReport


log = logging.getLogger("npabench.batch_runner")


def run_batch_evaluation(
    mission: Mission,
    mission_config: MissionConfig,
    agent_run_slots: list[AgentRunSlot],
    agent_specs: list[AgentSpec],
    *,
    reference_world_dir: Path,
    recording: bool,
    agent_mode,
    output_dir: Path,
    max_parallel: int = 1,
    task_seed: int | None = None,
) -> dict[str, "AgentRunReport"]:
    if len(agent_run_slots) != len(agent_specs):
        raise ValueError("agent_run_slots and agent_specs must have the same length")
    if getattr(agent_mode, "value", agent_mode) == "sandboxed":
        ensure_agent_image()
    total = len(agent_specs)
    log.info(
        "starting batch evaluation entries=%s parallel=%s mission=%s task=%s",
        total,
        max_parallel,
        mission.id,
        mission_config.id,
    )
    if max_parallel <= 1:
        reports: dict[str, "AgentRunReport"] = {}
        for index, (agent_run_slot, agent_spec) in enumerate(
            zip(agent_run_slots, agent_specs, strict=True),
            start=1,
        ):
            report = run_single_evaluation(
                mission,
                mission_config,
                agent_run_slot,
                agent_spec,
                reference_world_dir=reference_world_dir,
                recording=recording,
                agent_mode=agent_mode,
                output_dir=output_dir / safe_agent_output_name(agent_spec.name),
                task_seed=task_seed,
            )
            reports[agent_spec.name] = report
            log.info(
                "completed entry %s/%s agent=%s status=%s score=%s",
                index,
                total,
                agent_spec.name,
                report.status,
                report.score,
            )
        return reports

    reports: dict[str, "AgentRunReport"] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as executor:
        future_to_name = {
            executor.submit(
                run_single_evaluation,
                mission,
                mission_config,
                agent_run_slot,
                agent_spec,
                reference_world_dir=reference_world_dir,
                recording=recording,
                agent_mode=agent_mode,
                output_dir=output_dir / safe_agent_output_name(agent_spec.name),
                task_seed=task_seed,
            ): agent_spec.name
            for agent_run_slot, agent_spec in zip(agent_run_slots, agent_specs, strict=True)
        }
        completed = 0
        for future in concurrent.futures.as_completed(future_to_name):
            agent_name = future_to_name[future]
            report = future.result()
            reports[agent_name] = report
            completed += 1
            log.info(
                "completed entry %s/%s agent=%s status=%s score=%s",
                completed,
                total,
                agent_name,
                report.status,
                report.score,
            )
    return reports


def safe_agent_output_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "agent"
