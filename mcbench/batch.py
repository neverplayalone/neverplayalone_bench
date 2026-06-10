"""Batch orchestration: generate a challenge, build a world template, run slots in parallel."""

from __future__ import annotations

import concurrent.futures
import json
import random
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from .agents import AgentSpec, SubprocessAgent
from .container import _start_slot, _stop_slot
from .minecraft.rcon import rcon_session
from .minecraft.server import wait_for_ready
from .minecraft.world import _configure_world_start
from .models.challenge import GeneratedChallenge, ResourceCatalog
from .models.competition import ResourceCompetitionConfig, ResourceTarget
from .paths import COMPETITION_RESULTS_DIR
from .recording.recorder import RecordOptions
from .runner import run_resource_gathering_competition
from .slot import CompetitionSlot

console = Console()

@dataclass(frozen=True)
class EvaluationSlot:
    slot: CompetitionSlot
    agent_spec: AgentSpec
    result_dir: Path


@dataclass(frozen=True)
class EvaluationBatch:
    challenge: GeneratedChallenge
    base_config: ResourceCompetitionConfig
    agents: list[AgentSpec]
    slots: list[EvaluationSlot]
    output_dir: Path
    world_template_dir: Path


def generate_challenge(
    catalog: ResourceCatalog,
    base_cfg: ResourceCompetitionConfig,
    seed: int,
    challenge_id: str | None = None,
) -> GeneratedChallenge:
    rng = random.Random(seed)
    resource = rng.choice(sorted(catalog.resources))
    entry = catalog.resources[resource]
    target_count = rng.randint(*entry.target_range)
    world_seed = rng.randint(-(2**31), 2**31 - 1)
    display_name = entry.display_name or resource.replace("_", " ")
    challenge_id = challenge_id or f"resource_{seed}_{resource}_{target_count}"
    goal = (
        f"Before sunset, gather {target_count} {display_name}. "
        "Keep the items in your inventory and finish within 20 blocks of spawn."
    )
    return GeneratedChallenge(
        challenge_id=challenge_id,
        seed=seed,
        world_seed=world_seed,
        resource=resource,
        items=entry.items,
        target_count=target_count,
        points=entry.points,
        goal=goal,
        duration_seconds=base_cfg.duration_seconds,
        minecraft_version=base_cfg.minecraft_version,
        world_type=base_cfg.world_type,
        generate_structures=base_cfg.generate_structures,
        difficulty=base_cfg.difficulty,
        spawn_time=base_cfg.spawn_time,
        biome=entry.biome,
    )


class WorldTemplateBuilder:
    """Create one canonical server data directory for a batch."""

    def __init__(self, slot: CompetitionSlot):
        self.slot = slot

    def build(
        self,
        cfg: ResourceCompetitionConfig,
        output_dir: Path,
    ) -> Path:
        output_dir = output_dir.resolve()
        shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        console.log(
            f"Building world template with seed {cfg.seed} "
            f"on slot {self.slot.slot_id}..."
        )
        _start_slot(self.slot, cfg)
        try:
            server = self.slot.server_config()
            wait_for_ready(server, timeout=600)
            _configure_world_start(server, cfg)
            with rcon_session(
                server.host,
                server.rcon_port,
                server.rcon_password,
                socket_timeout=20,
            ) as mcr:
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
        cfg = self.batch.challenge.to_competition_config(self.batch.base_config)
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

    def _run_slot(
        self, cfg: ResourceCompetitionConfig, slot: EvaluationSlot
    ) -> dict[str, Any]:
        agent = SubprocessAgent(slot.agent_spec)
        record = RecordOptions(target_username=cfg.username) if self.record else None
        report = run_resource_gathering_competition(
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
    catalog: ResourceCatalog,
    base_cfg: ResourceCompetitionConfig,
    agents: list[AgentSpec],
    seed: int,
    output_dir: Path | None = None,
    challenge_id: str | None = None,
    base_game_port: int = 25665,
    base_rcon_port: int = 25675,
) -> EvaluationBatch:
    if not agents:
        raise ValueError("evaluation batch requires at least one agent")
    challenge = generate_challenge(catalog, base_cfg, seed, challenge_id=challenge_id)
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
    cfg = batch.challenge.to_competition_config(batch.base_config)
    batch.output_dir.mkdir(parents=True, exist_ok=True)
    template_slot = CompetitionSlot(
        slot_id=999,
        base_game_port=batch.slots[0].slot.base_game_port,
        base_rcon_port=batch.slots[0].slot.base_rcon_port,
        container_prefix="mcbench-template",
        data_root=batch.output_dir / "template_slot",
    )
    WorldTemplateBuilder(template_slot).build(cfg, batch.world_template_dir)
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
    """Delete the throwaway world copies a batch leaves behind.

    Every slot copies the full world template (hundreds of MB each) into its own
    data dir to run, and the template builder leaves its own copy too. Those are
    disposable once the run is scored, and they accumulate fast across repeated
    local runs. Scores, traces, recordings, and the canonical world_template are
    kept; only the running copies are removed.
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
