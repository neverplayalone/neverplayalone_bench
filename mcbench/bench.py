"""Config-driven benchmark runner: generate a seeded task suite, run an agent
against it, and aggregate the results into one report.

A run is fully described by a YAML config (commit it to pin/reproduce a suite).
`mcbench bench` reads it; --agent / --out override the volatile bits.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Literal, Union

import yaml
from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table

from .agents import AgentSpec, SubprocessAgent
from .config import TaskConfig, load_task
from .grader import grade
from .recorder import RecordOptions
from .runner import RECORDING_DIR, RESULTS_DIR, run_task
from .taskgen import STYLES, generate

console = Console()

class AgentRef(BaseModel):
    path: str
    name: str | None = None


class Defaults(BaseModel):
    difficulty: Literal["simple", "hard"] = "simple"
    count: int = 5
    seed_base: int = 0
    reset: bool = True
    record: bool = False  # capture a ReplayMod .mcpr per task into recording/


class StyleEntry(BaseModel):
    """A `{style: ..., count: ..., difficulty: ...}` entry overriding the defaults."""

    style: str
    count: int | None = None
    difficulty: Literal["simple", "hard"] | None = None
    seed_base: int | None = None


class BenchConfig(BaseModel):
    agent: AgentRef | None = None  # may be supplied via --agent instead
    defaults: Defaults = Field(default_factory=Defaults)
    styles: list[Union[str, StyleEntry]] = Field(default_factory=list)
    tasks: list[str] = Field(default_factory=list)  # frozen task files/dirs to also run
    output: str = "results/bench"


def load_bench_config(path: str | Path) -> BenchConfig:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return BenchConfig.model_validate(raw)


def _resolve_entry(entry: str | StyleEntry, d: Defaults) -> tuple[str, int, str, int]:
    if isinstance(entry, str):
        return entry, d.count, d.difficulty, d.seed_base
    return (
        entry.style,
        entry.count if entry.count is not None else d.count,
        entry.difficulty or d.difficulty,
        entry.seed_base if entry.seed_base is not None else d.seed_base,
    )


def build_suite(cfg: BenchConfig) -> list[TaskConfig]:
    """Expand the config into the concrete list of tasks to run."""
    tasks: list[TaskConfig] = []
    for entry in cfg.styles:
        style_id, count, difficulty, seed_base = _resolve_entry(entry, cfg.defaults)
        if style_id not in STYLES:
            raise ValueError(f"unknown style {style_id!r}; known: {sorted(STYLES)}")
        for i in range(count):
            tasks.append(generate(style_id, seed_base + i, difficulty))
    for spec in cfg.tasks:
        p = Path(spec)
        files = sorted(p.rglob("*.yaml")) if p.is_dir() else [p]
        tasks.extend(load_task(f) for f in files)
    return tasks


def _aggregate(rows: list[dict]) -> dict:
    def group(key: str) -> dict:
        buckets: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            buckets[r[key]].append(r)
        return {
            k: {
                "n": len(v),
                "pass_rate": sum(x["outcome"] == "pass" for x in v) / len(v),
                "mean_score": sum(x["score"] for x in v) / len(v),
            }
            for k, v in sorted(buckets.items())
        }

    n = len(rows)
    overall = {
        "n": n,
        "pass_rate": (sum(r["outcome"] == "pass" for r in rows) / n) if n else 0.0,
        "mean_score": (sum(r["score"] for r in rows) / n) if n else 0.0,
    }
    return {"overall": overall, "by_category": group("category"), "by_style": group("style")}


def _print_report(agent_name: str, summary: dict, valid_mode: bool = False) -> None:
    rate_label = "valid rate" if valid_mode else "pass rate"
    for title, key in (("By category", "by_category"), ("By style", "by_style")):
        table = Table(title=f"{title} — {'validation' if valid_mode else 'agent'}: {agent_name}")
        table.add_column(title.split()[-1].capitalize())
        table.add_column("n", justify="right")
        table.add_column(rate_label, justify="right")
        table.add_column("mean score", justify="right")
        for name, s in summary[key].items():
            table.add_row(name, str(s["n"]), f"{s['pass_rate']:.0%}", f"{s['mean_score']:.2f}")
        console.print(table)
    o = summary["overall"]
    console.print(
        f"[bold]Overall[/]: {o['n']} tasks | "
        f"{rate_label} {o['pass_rate']:.0%} | mean score {o['mean_score']:.2f}"
    )


def run_bench(
    cfg: BenchConfig,
    agent_path: str | None = None,
    out_dir: str | Path | None = None,
    valid_mode: bool = False,
) -> dict:
    # In validation mode the selected agent receives the success rules, so a
    # pass means the task is solvable by that validation agent. Recording is
    # forced on so each task can be reviewed.
    if valid_mode:
        agent_path = agent_path or (cfg.agent.path if cfg.agent else None)
        if not agent_path:
            raise ValueError("validation mode requires `agent.path` in the config or --agent")
        agent_name = (cfg.agent.name if cfg.agent and cfg.agent.name else None) or Path(agent_path).name
    else:
        agent_path = agent_path or (cfg.agent.path if cfg.agent else None)
        if not agent_path:
            raise ValueError("no agent specified — set `agent.path` in the config or pass --agent")
        agent_name = (cfg.agent.name if cfg.agent and cfg.agent.name else None) or Path(agent_path).name

    record_on = valid_mode or cfg.defaults.record
    summary_name = "validation_summary.json" if valid_mode else "bench_summary.json"

    # Both folders are static (always exist); a round replaces their contents.
    # results/ is always cleared so it holds exactly this round's tasks.
    # recording/ is only cleared when this round actually records, so a
    # non-recording round doesn't throw away a prior recorded run's replays.
    shutil.rmtree(RESULTS_DIR, ignore_errors=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if record_on:
        shutil.rmtree(RECORDING_DIR, ignore_errors=True)
    RECORDING_DIR.mkdir(parents=True, exist_ok=True)

    out = Path(out_dir or cfg.output)
    out.mkdir(parents=True, exist_ok=True)

    tasks = build_suite(cfg)
    mode = "Validate" if valid_mode else "Bench"
    console.log(f"[bold cyan]{mode}[/]: {len(tasks)} tasks | agent [bold]{agent_name}[/]")

    rows: list[dict] = []
    for idx, task in enumerate(tasks, 1):
        console.rule(f"[{idx}/{len(tasks)}] {task.id}")
        outcome, score = "error", 0.0
        try:
            agent = SubprocessAgent(AgentSpec(name=agent_name, path=str(agent_path)))
            rec = RecordOptions(target_username="BenchmarkBot") if record_on else None
            trace = run_task(
                task, agent, reset=cfg.defaults.reset, record=rec, expose_rules=valid_mode
            )
            report = grade(task, trace)
            outcome, score = report["outcome"], float(report["score"])
        except Exception as e:  # one bad task shouldn't abort the whole suite
            console.log(f"[red]task errored[/]: {e}")
        rows.append({
            "id": task.id,
            "style": task.metadata.get("style", "?"),
            "category": task.metadata.get("category", "?"),
            "difficulty": task.difficulty,
            "outcome": outcome,
            "score": score,
        })

    summary = _aggregate(rows)
    (out / summary_name).write_text(
        json.dumps({"agent": agent_name, "valid_mode": valid_mode, "tasks": rows, "summary": summary}, indent=2)
    )
    _print_report(agent_name, summary, valid_mode)
    if valid_mode:
        suspect = [r["id"] for r in rows if r["outcome"] != "pass"]
        if suspect:
            console.print(f"[yellow]Suspect tasks (validation agent did not solve — review):[/] {len(suspect)}")
            for tid in suspect:
                console.print(f"  - {tid}")
        else:
            console.print("[green]All tasks solved by the validation agent — suite looks valid.[/]")
    console.log(f"Summary written: {out / summary_name}")
    return summary
