"""Batch resource-gathering evaluation for validator-style runs.

A batch has one generated challenge and one canonical world template. Each miner
gets an isolated slot copied from that template, then all slots run in parallel.
"""

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

import yaml
from pydantic import BaseModel, field_validator
from rich.console import Console

from .agents import AgentSpec, SubprocessAgent
from .competition import (
    COMPETITION_RESULTS_DIR,
    CompetitionSlot,
    ResourceCompetitionConfig,
    ResourceTarget,
    _configure_world_start,
    _start_slot,
    _stop_slot,
    run_resource_gathering_competition,
)
from .rcon import rcon_session
from .recorder import RecordOptions
from .server import wait_for_ready

console = Console()


class ResourceCatalogEntry(BaseModel):
    """One logical resource that can be selected for a challenge."""

    items: list[str]
    target_range: tuple[int, int]
    points: float = 100.0
    display_name: str | None = None

    @field_validator("items")
    @classmethod
    def items_must_not_be_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("catalog resource must count at least one item")
        return value

    @field_validator("target_range")
    @classmethod
    def target_range_must_be_valid(cls, value: tuple[int, int]) -> tuple[int, int]:
        lo, hi = value
        if lo <= 0 or hi <= 0 or lo > hi:
            raise ValueError("target_range must be positive and increasing")
        return value


class ResourceCatalog(BaseModel):
    resources: dict[str, ResourceCatalogEntry]

    @field_validator("resources")
    @classmethod
    def resources_must_not_be_empty(
        cls, value: dict[str, ResourceCatalogEntry]
    ) -> dict[str, ResourceCatalogEntry]:
        if not value:
            raise ValueError("resource catalog cannot be empty")
        return value


class GeneratedChallenge(BaseModel):
    """The frozen task all miners in one batch receive."""

    challenge_id: str
    seed: int
    world_seed: int
    resource: str
    items: list[str]
    target_count: int
    points: float
    goal: str
    duration_seconds: int
    minecraft_version: str
    world_type: str
    generate_structures: bool
    difficulty: str
    spawn_time: int

    def to_competition_config(
        self, base_cfg: ResourceCompetitionConfig
    ) -> ResourceCompetitionConfig:
        data = base_cfg.model_dump()
        data.update(
            {
                "id": self.challenge_id,
                "seed": self.world_seed,
                "minecraft_version": self.minecraft_version,
                "world_type": self.world_type,
                "generate_structures": self.generate_structures,
                "difficulty": self.difficulty,
                "duration_seconds": self.duration_seconds,
                "spawn_time": self.spawn_time,
                "goal": self.goal,
                "resources": [
                    ResourceTarget(
                        item=self.resource,
                        items=self.items,
                        target_count=self.target_count,
                        points=self.points,
                    ).model_dump()
                ],
            }
        )
        return ResourceCompetitionConfig.model_validate(data)


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


def load_resource_catalog(path: str | Path) -> ResourceCatalog:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return ResourceCatalog.model_validate(raw)


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


def run_evaluation_batch(batch: EvaluationBatch, record: bool = False) -> dict[str, Any]:
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
    return ParallelEvaluator(batch, record=record).run()


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
