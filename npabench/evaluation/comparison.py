from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from npabench.agents.base import AgentSpec
from npabench.evaluation.evaluate import (
    AgentBatchReport,
    AgentMode,
    evaluate_multiple_agents,
    safe_name,
)

SCHEMA_VERSION = "1.2"
MINIMUM_PROMOTION_PAIRS = 3
MINIMUM_PROMOTION_WIN_RATE = 0.8
_IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache"}


@dataclass(frozen=True)
class ComparisonReport:
    output_dir: Path
    state: str
    trials: list[dict[str, Any]]
    summary: dict[str, Any]
    raw: dict[str, Any]


def compare_agents(
    baseline: AgentSpec,
    candidate: AgentSpec,
    *,
    mission_id: str = "resource_gathering",
    seeds: tuple[int, ...] = (0,),
    pairs_per_seed: int = 1,
    config_path: Path | None = None,
    output_dir: Path,
    record: bool = False,
    agent_mode: AgentMode = AgentMode.SANDBOXED,
    max_parallel: int = 2,
    evaluator: Callable[..., AgentBatchReport] = evaluate_multiple_agents,
) -> ComparisonReport:
    """Run paired AB/BA trials and checkpoint after every completed trial.

    Each seed/pair is evaluated twice with reversed slot order. The aggregate
    treats each completed AB/BA pair as one observation so a slot/order effect
    cannot masquerade as a candidate improvement.
    """

    if baseline.name == candidate.name:
        raise ValueError("baseline and candidate must have different names")
    if safe_name(baseline.name) == safe_name(candidate.name):
        raise ValueError("baseline and candidate must have unique output-safe names")
    if not seeds:
        raise ValueError("at least one seed is required")
    if len(set(seeds)) != len(seeds):
        raise ValueError("comparison seeds must be unique")
    if pairs_per_seed < 1:
        raise ValueError("pairs_per_seed must be at least one")
    if max_parallel < 1:
        raise ValueError("max_parallel must be at least one")

    root = output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "comparison.json"
    identity = _identity(
        baseline=baseline,
        candidate=candidate,
        mission_id=mission_id,
        seeds=seeds,
        pairs_per_seed=pairs_per_seed,
        config_path=config_path,
        record=record,
        agent_mode=agent_mode,
    )
    schedule = _schedule(seeds, pairs_per_seed, baseline.name, candidate.name)
    state = _load_or_initialize(state_path, identity, schedule)
    complete_ids = {str(trial["trial_id"]) for trial in state["trials"]}

    for scheduled in schedule:
        trial_id = str(scheduled["trial_id"])
        if trial_id in complete_ids:
            continue
        trial_root = _next_attempt_dir(root / "trials" / trial_id)
        order = [
            baseline if name == baseline.name else candidate
            for name in scheduled["order"]
        ]
        batch = evaluator(
            order,
            mission_id=mission_id,
            seed=int(scheduled["seed"]),
            config_path=config_path,
            output_dir=trial_root,
            record=record,
            agent_mode=agent_mode,
            max_parallel=min(max_parallel, 2),
        )
        trial = _trial_from_batch(
            scheduled,
            batch,
            baseline_name=baseline.name,
            candidate_name=candidate.name,
            expected_mission_id=mission_id,
        )
        state["trials"].append(trial)
        complete_ids.add(trial_id)
        state["summary"] = summarize_trials(
            state["trials"],
            baseline_name=baseline.name,
            candidate_name=candidate.name,
            expected_trials=len(schedule),
        )
        state["state"] = "running"
        _write_json_atomic(state_path, state)

    state["summary"] = summarize_trials(
        state["trials"],
        baseline_name=baseline.name,
        candidate_name=candidate.name,
        expected_trials=len(schedule),
    )
    state["state"] = "complete"
    _write_json_atomic(state_path, state)
    return ComparisonReport(
        output_dir=root,
        state=state["state"],
        trials=state["trials"],
        summary=state["summary"],
        raw=state,
    )


def summarize_trials(
    trials: list[dict[str, Any]],
    *,
    baseline_name: str,
    candidate_name: str,
    expected_trials: int,
) -> dict[str, Any]:
    ordered = sorted(trials, key=lambda trial: str(trial["trial_id"]))
    complete_pairs: list[dict[str, Any]] = []
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for trial in ordered:
        grouped.setdefault((int(trial["seed"]), int(trial["pair"])), []).append(trial)

    for (seed, pair), pair_trials in sorted(grouped.items()):
        by_order = {str(trial["order_label"]): trial for trial in pair_trials}
        if set(by_order) != {"ab", "ba"}:
            continue
        ab = by_order["ab"]
        ba = by_order["ba"]
        complete_pairs.append(
            {
                "seed": seed,
                "pair": pair,
                "ab_delta": ab["normalized_delta"],
                "ba_delta": ba["normalized_delta"],
                "mean_delta": statistics.fmean(
                    [float(ab["normalized_delta"]), float(ba["normalized_delta"])]
                ),
                "absolute_order_gap": abs(
                    float(ab["normalized_delta"]) - float(ba["normalized_delta"])
                ),
                "direction_agrees": (
                    _direction(float(ab["normalized_delta"]))
                    == _direction(float(ba["normalized_delta"]))
                ),
            }
        )

    deltas = [float(pair["mean_delta"]) for pair in complete_pairs]
    order_gaps = [float(pair["absolute_order_gap"]) for pair in complete_pairs]
    trial_deltas = [float(trial["normalized_delta"]) for trial in ordered]
    all_runs = [
        trial["agents"][name]
        for trial in ordered
        for name in (baseline_name, candidate_name)
    ]
    status_counts: dict[str, dict[str, int]] = {
        baseline_name: {},
        candidate_name: {},
    }
    for trial in ordered:
        for name in (baseline_name, candidate_name):
            status = str(trial["agents"][name]["status"])
            counts = status_counts[name]
            counts[status] = counts.get(status, 0) + 1

    evidence_complete = (
        len(ordered) == expected_trials
        and len(complete_pairs) == expected_trials // 2
    )
    all_scores_finite = all(
        math.isfinite(float(run["score"]))
        and math.isfinite(float(run["max_score"]))
        and float(run["max_score"]) > 0
        for run in all_runs
    )
    all_runs_ok = all(str(run["status"]) == "ok" for run in all_runs)
    direction_disagreements = sum(
        not bool(pair["direction_agrees"]) for pair in complete_pairs
    )
    required_wins = math.ceil(
        len(complete_pairs) * MINIMUM_PROMOTION_WIN_RATE
    )
    candidate_wins = sum(delta > 0 for delta in deltas)
    seed_deltas: dict[int, list[float]] = {}
    for pair in complete_pairs:
        seed_deltas.setdefault(int(pair["seed"]), []).append(float(pair["mean_delta"]))
    by_seed = [
        {
            "seed": seed,
            "pairs": len(seed_values),
            "mean_delta": statistics.fmean(seed_values),
            "wins": sum(delta > 0 for delta in seed_values),
            "ties": sum(math.isclose(delta, 0.0, abs_tol=1e-12) for delta in seed_values),
            "losses": sum(delta < 0 for delta in seed_values),
        }
        for seed, seed_values in sorted(seed_deltas.items())
    ]
    evidence_reasons: list[str] = []
    if not evidence_complete:
        evidence_reasons.append("comparison_incomplete")
    if len(complete_pairs) < MINIMUM_PROMOTION_PAIRS:
        evidence_reasons.append(
            f"fewer_than_{MINIMUM_PROMOTION_PAIRS}_complete_pairs"
        )
    if direction_disagreements:
        evidence_reasons.append("ab_ba_direction_disagreement")
    if not all_runs_ok:
        evidence_reasons.append("non_ok_agent_run")
    if not all_scores_finite:
        evidence_reasons.append("non_finite_score")
    sufficient_repeated_evidence = not evidence_reasons

    decision_reasons: list[str] = []
    mean_delta = statistics.fmean(deltas) if deltas else None
    if mean_delta is None or mean_delta <= 0:
        decision_reasons.append("candidate_mean_not_positive")
    if candidate_wins < required_wins:
        decision_reasons.append(
            f"candidate_wins_below_{required_wins}_of_{len(complete_pairs)}"
        )
    non_positive_seeds = [
        str(row["seed"]) for row in by_seed if float(row["mean_delta"]) <= 0
    ]
    if non_positive_seeds:
        decision_reasons.append(
            "non_positive_seed_delta:" + ",".join(non_positive_seeds)
        )

    return {
        "expected_trials": expected_trials,
        "completed_trials": len(ordered),
        "expected_pairs": expected_trials // 2,
        "completed_pairs": len(complete_pairs),
        "evidence_complete": evidence_complete,
        "pair_results": complete_pairs,
        "candidate_minus_baseline": {
            "mean": mean_delta,
            "median": statistics.median(deltas) if deltas else None,
            "wins": sum(delta > 0 for delta in deltas),
            "ties": sum(math.isclose(delta, 0.0, abs_tol=1e-12) for delta in deltas),
            "losses": sum(delta < 0 for delta in deltas),
        },
        "trial_directions": {
            "candidate_wins": sum(delta > 0 for delta in trial_deltas),
            "ties": sum(math.isclose(delta, 0.0, abs_tol=1e-12) for delta in trial_deltas),
            "candidate_losses": sum(delta < 0 for delta in trial_deltas),
        },
        "order_sensitivity": {
            "mean_absolute_delta_gap": statistics.fmean(order_gaps) if order_gaps else None,
            "max_absolute_delta_gap": max(order_gaps) if order_gaps else None,
            "direction_disagreements": direction_disagreements,
        },
        "status_counts": status_counts,
        "all_scores_finite": all_scores_finite,
        "promotion_gate": {
            "minimum_complete_pairs": MINIMUM_PROMOTION_PAIRS,
            "minimum_win_rate": MINIMUM_PROMOTION_WIN_RATE,
            "required_wins": required_wins,
            "sufficient_repeated_evidence": sufficient_repeated_evidence,
            "supports_candidate": (
                sufficient_repeated_evidence and not decision_reasons
            ),
            "evidence_reasons": evidence_reasons,
            "decision_reasons": decision_reasons,
            "by_seed": by_seed,
        },
    }


def _direction(value: float) -> int:
    if math.isclose(value, 0.0, abs_tol=1e-12):
        return 0
    return 1 if value > 0 else -1


def _schedule(
    seeds: tuple[int, ...],
    pairs_per_seed: int,
    baseline_name: str,
    candidate_name: str,
) -> list[dict[str, Any]]:
    schedule: list[dict[str, Any]] = []
    for seed in seeds:
        for pair in range(1, pairs_per_seed + 1):
            for label, order in (
                ("ab", [baseline_name, candidate_name]),
                ("ba", [candidate_name, baseline_name]),
            ):
                schedule.append(
                    {
                        "trial_id": f"seed-{seed}-pair-{pair:02d}-{label}",
                        "seed": seed,
                        "pair": pair,
                        "order_label": label,
                        "order": order,
                    }
                )
    return schedule


def _trial_from_batch(
    scheduled: dict[str, Any],
    batch: AgentBatchReport,
    *,
    baseline_name: str,
    candidate_name: str,
    expected_mission_id: str,
) -> dict[str, Any]:
    if not batch.evidence_valid:
        raise RuntimeError(
            "batch evidence is invalid: "
            + ", ".join(batch.evidence_reasons or ("unspecified reason",))
        )
    if batch.mission_id != expected_mission_id:
        raise RuntimeError(
            f"batch report mission mismatch: {batch.mission_id} != {expected_mission_id}"
        )
    if batch.seed != int(scheduled["seed"]):
        raise RuntimeError(f"batch report seed mismatch: {batch.seed} != {scheduled['seed']}")
    missing = {baseline_name, candidate_name} - set(batch.agents)
    if missing:
        raise RuntimeError(f"batch report is missing agents: {', '.join(sorted(missing))}")
    _assert_identical_starting_spawn(
        batch,
        baseline_name=baseline_name,
        candidate_name=candidate_name,
    )
    agents: dict[str, dict[str, Any]] = {}
    for name in (baseline_name, candidate_name):
        report = batch.agents[name]
        if report.mission_id != batch.mission_id or report.task_id != batch.task_id:
            raise RuntimeError(f"mixed mission/task evidence for {name}")
        if report.seed != batch.seed or report.minecraft_seed != batch.minecraft_seed:
            raise RuntimeError(f"mixed seed evidence for {name}")
        if not math.isfinite(float(report.score)) or not math.isfinite(float(report.max_score)):
            raise RuntimeError(f"non-finite score for {name}")
        if report.max_score <= 0:
            raise RuntimeError(f"non-positive max score for {name}")
        agents[name] = {
            "score": report.score,
            "max_score": report.max_score,
            "normalized_score": report.score / report.max_score,
            "status": report.status,
            "spawn": report.raw.get("spawn"),
            "output_dir": str(report.output_dir),
            "trace_path": str(report.trace_path),
            "recording_path": str(report.recording_path)
            if report.recording_path is not None
            else None,
        }
    return {
        **scheduled,
        "task_id": batch.task_id,
        "minecraft_seed": batch.minecraft_seed,
        "output_dir": str(batch.output_dir),
        "agents": agents,
        "normalized_delta": (
            agents[candidate_name]["normalized_score"]
            - agents[baseline_name]["normalized_score"]
        ),
    }


def _assert_identical_starting_spawn(
    batch: AgentBatchReport,
    *,
    baseline_name: str,
    candidate_name: str,
) -> None:
    """Reject terrain-confounded evidence before it reaches an aggregate.

    A shared Minecraft seed is not enough: vanilla may choose a different
    first-login point inside the world's spawn radius for each copied lane.
    Reports produced by the official missions include the exact pinned spawn,
    so compare it whenever that evidence is present.  Synthetic/custom
    evaluators that omit spawn evidence remain supported.
    """

    spawns: dict[str, Any] = {}
    for name in (baseline_name, candidate_name):
        raw_spawn = batch.agents[name].raw.get("spawn")
        if isinstance(raw_spawn, dict) and raw_spawn.get("position") is not None:
            spawns[name] = raw_spawn["position"]
    if len(spawns) != 2:
        return
    if spawns[baseline_name] != spawns[candidate_name]:
        raise RuntimeError(
            "non-identical starting spawn invalidates paired evidence: "
            f"{baseline_name}={spawns[baseline_name]!r}, "
            f"{candidate_name}={spawns[candidate_name]!r}"
        )


def _identity(
    *,
    baseline: AgentSpec,
    candidate: AgentSpec,
    mission_id: str,
    seeds: tuple[int, ...],
    pairs_per_seed: int,
    config_path: Path | None,
    record: bool,
    agent_mode: AgentMode,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mission_id": mission_id,
        "seeds": list(seeds),
        "pairs_per_seed": pairs_per_seed,
        "config_path": str(config_path.resolve()) if config_path else None,
        "config_sha256": _file_sha256(config_path) if config_path else None,
        "record": record,
        "agent_mode": agent_mode.value,
        "baseline": _agent_identity(baseline),
        "candidate": _agent_identity(candidate),
    }


def _agent_identity(agent: AgentSpec) -> dict[str, Any]:
    return {
        "name": agent.name,
        "path": str(agent.path.resolve()),
        "tree_sha256": _tree_sha256(agent.path),
    }


def _tree_sha256(root: Path) -> str:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"agent path must be a directory: {root}")
    digest = hashlib.sha256()
    digest.update(b".\0")
    digest.update(str(root.stat().st_mode & 0o777).encode())
    digest.update(b"\0")
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if _IGNORED_PARTS & set(path.relative_to(root).parts):
            continue
        if path.is_symlink():
            raise ValueError(f"agent tree contains a symlink: {relative}")
        if path.is_dir():
            digest.update(f"{relative}/".encode())
            digest.update(b"\0")
            digest.update(str(path.stat().st_mode & 0o777).encode())
            digest.update(b"\0")
            continue
        if not path.is_file():
            raise ValueError(f"agent tree contains a non-regular file: {relative}")
        files.append(path)
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(str(path.stat().st_mode & 0o777).encode())
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_or_initialize(
    state_path: Path,
    identity: dict[str, Any],
    schedule: list[dict[str, Any]],
) -> dict[str, Any]:
    if not state_path.exists():
        state = {
            "schema_version": SCHEMA_VERSION,
            "state": "running",
            "identity": identity,
            "schedule": schedule,
            "trials": [],
            "summary": {},
        }
        _write_json_atomic(state_path, state)
        return state
    state = json.loads(state_path.read_text())
    if state.get("identity") != identity or state.get("schedule") != schedule:
        raise ValueError("existing comparison.json does not match this comparison identity")
    if not isinstance(state.get("trials"), list):
        raise ValueError("existing comparison.json has invalid trials")
    scheduled_ids = {str(trial["trial_id"]) for trial in schedule}
    completed_ids = [str(trial.get("trial_id")) for trial in state["trials"]]
    if len(completed_ids) != len(set(completed_ids)):
        raise ValueError("existing comparison.json has duplicate completed trials")
    if not set(completed_ids).issubset(scheduled_ids):
        raise ValueError("existing comparison.json contains unscheduled trials")
    return state


def _next_attempt_dir(base: Path) -> Path:
    if not base.exists():
        return base
    for attempt in range(2, 10_000):
        candidate = base.with_name(f"{base.name}-attempt-{attempt:02d}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"no free attempt directory for {base}")


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(f"{json.dumps(value, indent=2, sort_keys=True)}\n")
    temporary.replace(path)
