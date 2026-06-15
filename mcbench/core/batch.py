"""Batch orchestration: one instance, one world template, slots run in parallel.

Task-agnostic: the instance, world configuration, and scoring all come
from the :class:`Task` carried on the batch.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console

from mcbench.minecraft.rcon import rcon_session
from mcbench.minecraft.server import wait_for_ready
from mcbench.paths import RESULTS_DIR
from mcbench.recording.recorder import RecordOptions
from mcbench.core.base_task import Task, RunConfig
from mcbench.core.container import _start_slot, _stop_slot
from mcbench.core.runner import run_task
from mcbench.core.slot import Slot

# Imported lazily inside functions to avoid an import cycle: mcbench.agents pulls
# in mcbench.core.trace, which triggers this package's __init__.
if TYPE_CHECKING:
    from mcbench.agents import Agent, AgentSpec

console = Console()

AGENT_MODES = ("subprocess", "docker")


def make_agent(
    spec: AgentSpec, *, mode: str, slot: Slot, image: str | None = None
) -> Agent:
    """Build the agent for one slot in the requested execution mode.

    ``subprocess`` runs the agent directly on the host (fast, for trusted local
    development). ``docker`` runs it inside a sandboxed container (for untrusted /
    submitted agent code). This is the single place an agent is constructed.
    """
    from mcbench.agents import DockerAgent, SubprocessAgent

    if mode == "docker":
        return DockerAgent(
            spec,
            container_name=f"mcbench-agent-{slot.slot_id}",
            network_name=slot.network_name,
            server_host=slot.container_name,
            image=image,
        )
    if mode == "subprocess":
        return SubprocessAgent(spec)
    raise ValueError(f"unknown agent mode {mode!r}; expected one of {AGENT_MODES}")


@dataclass(frozen=True)
class EvaluationSlot:
    slot: Slot
    agent_spec: AgentSpec
    result_dir: Path


@dataclass(frozen=True)
class EvaluationBatch:
    task: Task
    instance: Any
    base_config: RunConfig
    agents: list[AgentSpec]
    slots: list[EvaluationSlot]
    output_dir: Path
    world_template_dir: Path


class WorldTemplateBuilder:
    """Create one canonical server data directory for a batch."""

    def __init__(self, slot: Slot):
        self.slot = slot

    def build(self, task: Task, cfg: RunConfig, output_dir: Path) -> Path:
        output_dir = output_dir.resolve()
        shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        console.log(
            f"Building world template with seed {cfg.seed} on slot {self.slot.slot_id}..."
        )
        _start_slot(self.slot, cfg)
        try:
            server = self.slot.server_config()
            wait_for_ready(server, timeout=600)
            with rcon_session(
                server.host, server.rcon_port, server.rcon_password, socket_timeout=20
            ) as mcr:
                task.configure_world(mcr, cfg)
                mcr.command("save-all flush")
        finally:
            _stop_slot(self.slot, quiet=True)
        shutil.copytree(self.slot.data_dir, output_dir)
        return output_dir


class ParallelEvaluator:
    """Run all agent slots for one batch and write an aggregate report."""

    def __init__(
        self,
        batch: EvaluationBatch,
        record: bool = False,
        agent_mode: str = "docker",
    ):
        self.batch = batch
        self.record = record
        self.agent_mode = agent_mode
        self._agent_image: str | None = None

    def run(self) -> dict[str, Any]:
        self.batch.output_dir.mkdir(parents=True, exist_ok=True)
        instance_path = self.batch.output_dir / "generated_instance.json"
        instance_path.write_text(self.batch.instance.model_dump_json(indent=2))
        cfg = self.batch.instance.to_run_config(self.batch.base_config)
        # Build the sandbox image once, before slots fan out, so parallel slots
        # don't each trigger a concurrent build.
        if self.agent_mode == "docker":
            from mcbench.agents import ensure_agent_image

            self._agent_image = ensure_agent_image()
        results: list[dict[str, Any]] = []
        started_at = time.time()

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(self.batch.slots)
        ) as executor:
            futures = {
                executor.submit(self._run_slot, cfg, slot): slot
                for slot in self.batch.slots
            }
            for future in concurrent.futures.as_completed(futures):
                slot = futures[future]
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append(
                        {
                            "agent": slot.agent_spec.name,
                            "slot": slot.slot.slot_id,
                            "score": 0.0,
                            "error": str(e),
                        }
                    )

        report = {
            "instance": self.batch.instance.model_dump(),
            "started_at": started_at,
            "ended_at": time.time(),
            "results": sorted(results, key=lambda r: str(r.get("agent"))),
        }
        (self.batch.output_dir / "batch_report.json").write_text(
            json.dumps(report, indent=2)
        )
        return report

    def _run_slot(self, cfg: RunConfig, slot: EvaluationSlot) -> dict[str, Any]:
        agent = make_agent(
            slot.agent_spec,
            mode=self.agent_mode,
            slot=slot.slot,
            image=self._agent_image,
        )
        record = RecordOptions(target_username=cfg.username) if self.record else None
        report = run_task(
            self.batch.task,
            cfg,
            agent,
            slot=slot.slot,
            out_dir=slot.result_dir,
            record=record,
            world_template=self.batch.world_template_dir,
        )
        return {
            "agent": slot.agent_spec.name,
            "slot": slot.slot.slot_id,
            "result_dir": str(slot.result_dir),
            **report,
        }


def create_evaluation_batch(
    *,
    task: Task,
    base_cfg: RunConfig,
    agents: list[AgentSpec],
    seed: int,
    output_dir: Path | None = None,
    instance_id: str | None = None,
    base_game_port: int = 25665,
    base_rcon_port: int = 25675,
) -> EvaluationBatch:
    if not agents:
        raise ValueError("evaluation batch requires at least one agent")
    instance = task.generate_instance(base_cfg, seed, instance_id=instance_id)
    output = (
        output_dir
        if output_dir is not None
        else RESULTS_DIR / "batches" / instance.instance_id
    ).resolve()
    world_template = output / "world_template"
    slots = [
        EvaluationSlot(
            slot=Slot(
                slot_id=i,
                base_game_port=base_game_port,
                base_rcon_port=base_rcon_port,
                data_root=output / "slots",
            ),
            agent_spec=agent,
            result_dir=output / "agents" / f"{_safe_name(agent.name)}__slot{i}",
        )
        for i, agent in enumerate(agents)
    ]
    return EvaluationBatch(
        task=task,
        instance=instance,
        base_config=base_cfg,
        agents=agents,
        slots=slots,
        output_dir=output,
        world_template_dir=world_template,
    )


def run_evaluation_batch(
    batch: EvaluationBatch,
    record: bool = False,
    keep_slots: bool = False,
    agent_mode: str = "docker",
) -> dict[str, Any]:
    cfg = batch.instance.to_run_config(batch.base_config)
    batch.output_dir.mkdir(parents=True, exist_ok=True)
    template_slot = Slot(
        slot_id=999,
        base_game_port=batch.slots[0].slot.base_game_port,
        base_rcon_port=batch.slots[0].slot.base_rcon_port,
        container_prefix="mcbench-template",
        data_root=batch.output_dir / "template_slot",
    )
    WorldTemplateBuilder(template_slot).build(batch.task, cfg, batch.world_template_dir)
    try:
        return ParallelEvaluator(batch, record=record, agent_mode=agent_mode).run()
    finally:
        if keep_slots:
            console.log(
                "[yellow]--keep-slots[/]: leaving per-slot world copies under "
                f"{batch.output_dir}"
            )
        else:
            _cleanup_slot_worlds(batch)


def _cleanup_slot_worlds(batch: EvaluationBatch) -> None:
    """Delete the throwaway per-slot + template world copies a batch leaves behind.

    Scores, traces, recordings, and the canonical world_template are kept; only
    the running copies (hundreds of MB each) are removed.
    """
    removed: list[str] = []
    for target in (batch.output_dir / "slots", batch.output_dir / "template_slot"):
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            removed.append(target.name)
    if removed:
        console.log(f"Cleaned up slot world copies: {', '.join(removed)}")


def parse_agent_assignment(raw: str) -> AgentSpec:
    from mcbench.agents import AgentSpec

    if "=" in raw:
        name, path_raw = raw.split("=", 1)
        if not name:
            raise ValueError(f"invalid agent assignment {raw!r}: missing name")
    else:
        path_raw = raw
        name = Path(path_raw).name
    path = Path(path_raw)
    if not path.exists():
        raise ValueError(f"agent path does not exist: {path}")
    return AgentSpec(name=name, path=str(path))


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "agent"
