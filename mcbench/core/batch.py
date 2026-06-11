"""Batch orchestration: one challenge, one world template, slots run in parallel.

Competition-agnostic: the challenge, world configuration, and scoring all come
from the :class:`Competition` carried on the batch.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from ..agents import AgentSpec, SubprocessAgent
from ..minecraft.rcon import rcon_session
from ..minecraft.server import wait_for_ready
from ..paths import COMPETITION_RESULTS_DIR
from ..recording.recorder import RecordOptions
from .competition import Competition, RunConfig
from .container import _start_slot, _stop_slot
from .runner import run_competition
from .slot import CompetitionSlot

console = Console()


@dataclass(frozen=True)
class EvaluationSlot:
    slot: CompetitionSlot
    agent_spec: AgentSpec
    result_dir: Path


@dataclass(frozen=True)
class EvaluationBatch:
    competition: Competition
    challenge: Any
    base_config: RunConfig
    agents: list[AgentSpec]
    slots: list[EvaluationSlot]
    output_dir: Path
    world_template_dir: Path


class WorldTemplateBuilder:
    """Create one canonical server data directory for a batch."""

    def __init__(self, slot: CompetitionSlot):
        self.slot = slot

    def build(self, competition: Competition, cfg: RunConfig, output_dir: Path) -> Path:
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
                competition.configure_world(mcr, cfg)
                mcr.command("save-all flush")
        finally:
            _stop_slot(self.slot, quiet=True)
        shutil.copytree(self.slot.data_dir, output_dir)
        return output_dir


class ParallelEvaluator:
    """Run all miner slots for one batch and write an aggregate report."""

    def __init__(self, batch: EvaluationBatch, record: bool = False):
        self.batch = batch
        self.record = record

    def run(self) -> dict[str, Any]:
        self.batch.output_dir.mkdir(parents=True, exist_ok=True)
        challenge_path = self.batch.output_dir / "generated_challenge.json"
        challenge_path.write_text(self.batch.challenge.model_dump_json(indent=2))
        cfg = self.batch.challenge.to_run_config(self.batch.base_config)
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
                            "miner": slot.agent_spec.name,
                            "slot": slot.slot.slot_id,
                            "score": 0.0,
                            "error": str(e),
                        }
                    )

        report = {
            "challenge": self.batch.challenge.model_dump(),
            "started_at": started_at,
            "ended_at": time.time(),
            "results": sorted(results, key=lambda r: str(r.get("miner"))),
        }
        (self.batch.output_dir / "batch_report.json").write_text(
            json.dumps(report, indent=2)
        )
        return report

    def _run_slot(self, cfg: RunConfig, slot: EvaluationSlot) -> dict[str, Any]:
        agent = SubprocessAgent(slot.agent_spec)
        record = RecordOptions(target_username=cfg.username) if self.record else None
        report = run_competition(
            self.batch.competition,
            cfg,
            agent,
            slot=slot.slot,
            out_dir=slot.result_dir,
            record=record,
            world_template=self.batch.world_template_dir,
        )
        return {
            "miner": slot.agent_spec.name,
            "slot": slot.slot.slot_id,
            "result_dir": str(slot.result_dir),
            **report,
        }


def create_evaluation_batch(
    *,
    competition: Competition,
    base_cfg: RunConfig,
    agents: list[AgentSpec],
    seed: int,
    output_dir: Path | None = None,
    challenge_id: str | None = None,
    base_game_port: int = 25665,
    base_rcon_port: int = 25675,
) -> EvaluationBatch:
    if not agents:
        raise ValueError("evaluation batch requires at least one agent")
    challenge = competition.generate_challenge(base_cfg, seed, challenge_id=challenge_id)
    output = (
        output_dir
        if output_dir is not None
        else COMPETITION_RESULTS_DIR / "batches" / challenge.challenge_id
    ).resolve()
    world_template = output / "world_template"
    slots = [
        EvaluationSlot(
            slot=CompetitionSlot(
                slot_id=i,
                base_game_port=base_game_port,
                base_rcon_port=base_rcon_port,
                data_root=output / "slots",
            ),
            agent_spec=agent,
            result_dir=output / "miners" / f"{_safe_name(agent.name)}__slot{i}",
        )
        for i, agent in enumerate(agents)
    ]
    return EvaluationBatch(
        competition=competition,
        challenge=challenge,
        base_config=base_cfg,
        agents=agents,
        slots=slots,
        output_dir=output,
        world_template_dir=world_template,
    )


def run_evaluation_batch(
    batch: EvaluationBatch, record: bool = False, keep_slots: bool = False
) -> dict[str, Any]:
    cfg = batch.challenge.to_run_config(batch.base_config)
    batch.output_dir.mkdir(parents=True, exist_ok=True)
    template_slot = CompetitionSlot(
        slot_id=999,
        base_game_port=batch.slots[0].slot.base_game_port,
        base_rcon_port=batch.slots[0].slot.base_rcon_port,
        container_prefix="mcbench-template",
        data_root=batch.output_dir / "template_slot",
    )
    WorldTemplateBuilder(template_slot).build(batch.competition, cfg, batch.world_template_dir)
    try:
        return ParallelEvaluator(batch, record=record).run()
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
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "miner"
