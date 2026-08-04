from __future__ import annotations

import json
from pathlib import Path

import pytest

from npabench.agents.base import AgentSpec
from npabench.evaluation.comparison import compare_agents
from npabench.evaluation.evaluate import AgentBatchReport, AgentMode, AgentRunReport


def _report(
    name: str,
    score: float,
    root: Path,
    *,
    seed: int = 7,
    minecraft_seed: int = 77,
    status: str = "ok",
) -> AgentRunReport:
    out = root / name
    return AgentRunReport(
        agent_name=name,
        agent_kind=None,
        mission_id="resource_gathering",
        task_id="task",
        task_prompt="gather",
        seed=seed,
        minecraft_seed=minecraft_seed,
        score=score,
        max_score=100.0,
        status=status,
        output_dir=out,
        trace_path=out / "trace.json",
        recording_path=None,
        raw={"spawn": {"position": [0, 64, 0]}},
    )


def test_compare_counterbalances_and_aggregates_pairs(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    baseline_dir.mkdir()
    candidate_dir.mkdir()
    (baseline_dir / "index.js").write_text("baseline")
    (candidate_dir / "index.js").write_text("candidate")
    baseline = AgentSpec("baseline", baseline_dir)
    candidate = AgentSpec("candidate", candidate_dir)
    orders: list[list[str]] = []

    def evaluator(agents, **kwargs):
        order = [agent.name for agent in agents]
        orders.append(order)
        # Add a large slot-0 advantage. The AB/BA pair mean must cancel it.
        scores = {
            name: (60.0 if name == "candidate" else 50.0) + (20.0 if slot == 0 else 0.0)
            for slot, name in enumerate(order)
        }
        root = kwargs["output_dir"]
        return AgentBatchReport(
            mission_id="resource_gathering",
            task_id="task",
            seed=kwargs["seed"],
            minecraft_seed=77,
            agents={
                name: _report(name, scores[name], root, seed=kwargs["seed"])
                for name in order
            },
            output_dir=root,
        )

    report = compare_agents(
        baseline,
        candidate,
        seeds=(7,),
        pairs_per_seed=2,
        output_dir=tmp_path / "comparison",
        record=False,
        agent_mode=AgentMode.HOST,
        evaluator=evaluator,
    )

    assert orders == [
        ["baseline", "candidate"],
        ["candidate", "baseline"],
        ["baseline", "candidate"],
        ["candidate", "baseline"],
    ]
    assert report.state == "complete"
    assert report.summary["evidence_complete"] is True
    assert report.summary["completed_pairs"] == 2
    assert report.summary["candidate_minus_baseline"] == {
        "mean": pytest.approx(0.1),
        "median": pytest.approx(0.1),
        "wins": 2,
        "ties": 0,
        "losses": 0,
    }
    assert report.summary["trial_directions"] == {
        "candidate_wins": 2,
        "ties": 0,
        "candidate_losses": 2,
    }
    assert report.summary["order_sensitivity"] == {
        "mean_absolute_delta_gap": pytest.approx(0.4),
        "max_absolute_delta_gap": pytest.approx(0.4),
        "direction_disagreements": 2,
    }
    assert report.summary["promotion_gate"]["sufficient_repeated_evidence"] is False
    assert report.summary["promotion_gate"]["supports_candidate"] is False
    assert "fewer_than_3_complete_pairs" in report.summary["promotion_gate"]["evidence_reasons"]
    assert "ab_ba_direction_disagreement" in report.summary["promotion_gate"]["evidence_reasons"]
    persisted = json.loads((report.output_dir / "comparison.json").read_text())
    assert persisted["state"] == "complete"
    assert persisted["identity"]["baseline"]["tree_sha256"]


def test_compare_requires_three_stable_pairs_before_supporting_promotion(
    tmp_path: Path,
) -> None:
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    baseline_dir.mkdir()
    candidate_dir.mkdir()
    (baseline_dir / "index.js").write_text("baseline")
    (candidate_dir / "index.js").write_text("candidate")

    def evaluator(agents, **kwargs):
        root = kwargs["output_dir"]
        return AgentBatchReport(
            mission_id="resource_gathering",
            task_id="task",
            seed=kwargs["seed"],
            minecraft_seed=77,
            agents={
                agent.name: _report(
                    agent.name,
                    60.0 if agent.name == "candidate" else 50.0,
                    root,
                    seed=kwargs["seed"],
                )
                for agent in agents
            },
            output_dir=root,
        )

    report = compare_agents(
        AgentSpec("baseline", baseline_dir),
        AgentSpec("candidate", candidate_dir),
        seeds=(7,),
        pairs_per_seed=3,
        output_dir=tmp_path / "comparison",
        evaluator=evaluator,
    )

    gate = report.summary["promotion_gate"]
    assert gate["sufficient_repeated_evidence"] is True
    assert gate["supports_candidate"] is True
    assert gate["required_wins"] == 3
    assert gate["evidence_reasons"] == []
    assert gate["decision_reasons"] == []
    assert gate["by_seed"] == [{
        "seed": 7,
        "pairs": 3,
        "mean_delta": pytest.approx(0.1),
        "wins": 3,
        "ties": 0,
        "losses": 0,
    }]


def test_compare_resumes_completed_trials_and_uses_new_attempt(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    baseline_dir.mkdir()
    candidate_dir.mkdir()
    (baseline_dir / "index.js").write_text("baseline")
    (candidate_dir / "index.js").write_text("candidate")
    baseline = AgentSpec("baseline", baseline_dir)
    candidate = AgentSpec("candidate", candidate_dir)
    output = tmp_path / "comparison"
    calls = 0

    def interrupted(agents, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            kwargs["output_dir"].mkdir(parents=True)
            raise RuntimeError("simulated interruption")
        root = kwargs["output_dir"]
        return AgentBatchReport(
            mission_id="resource_gathering",
            task_id="task",
            seed=kwargs["seed"],
            minecraft_seed=77,
            agents={
                agent.name: _report(agent.name, 50.0, root, seed=kwargs["seed"])
                for agent in agents
            },
            output_dir=root,
        )

    with pytest.raises(RuntimeError, match="simulated interruption"):
        compare_agents(
            baseline,
            candidate,
            seeds=(7,),
            output_dir=output,
            evaluator=interrupted,
        )

    state = json.loads((output / "comparison.json").read_text())
    assert len(state["trials"]) == 1
    assert state["state"] == "running"

    resumed_calls: list[Path] = []

    def resumed(agents, **kwargs):
        root = kwargs["output_dir"]
        resumed_calls.append(root)
        return AgentBatchReport(
            mission_id="resource_gathering",
            task_id="task",
            seed=kwargs["seed"],
            minecraft_seed=77,
            agents={
                agent.name: _report(agent.name, 50.0, root, seed=kwargs["seed"])
                for agent in agents
            },
            output_dir=root,
        )

    report = compare_agents(
        baseline,
        candidate,
        seeds=(7,),
        output_dir=output,
        evaluator=resumed,
    )

    assert len(resumed_calls) == 1
    assert resumed_calls[0].name.endswith("-attempt-02")
    assert report.summary["completed_trials"] == 2
    assert report.summary["completed_pairs"] == 1


def test_compare_rejects_changed_candidate_on_resume(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    baseline_dir.mkdir()
    candidate_dir.mkdir()
    (baseline_dir / "index.js").write_text("baseline")
    candidate_file = candidate_dir / "index.js"
    candidate_file.write_text("candidate-v1")
    baseline = AgentSpec("baseline", baseline_dir)
    candidate = AgentSpec("candidate", candidate_dir)

    def evaluator(agents, **kwargs):
        root = kwargs["output_dir"]
        return AgentBatchReport(
            mission_id="resource_gathering",
            task_id="task",
            seed=kwargs["seed"],
            minecraft_seed=77,
            agents={
                agent.name: _report(agent.name, 50.0, root, seed=kwargs["seed"])
                for agent in agents
            },
            output_dir=root,
        )

    output = tmp_path / "comparison"
    compare_agents(baseline, candidate, output_dir=output, evaluator=evaluator)
    candidate_file.write_text("candidate-v2")

    with pytest.raises(ValueError, match="does not match"):
        compare_agents(baseline, candidate, output_dir=output, evaluator=evaluator)


def test_compare_rejects_duplicate_seeds_before_running(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    baseline_dir.mkdir()
    candidate_dir.mkdir()
    (baseline_dir / "index.js").write_text("baseline")
    (candidate_dir / "index.js").write_text("candidate")

    with pytest.raises(ValueError, match="seeds must be unique"):
        compare_agents(
            AgentSpec("baseline", baseline_dir),
            AgentSpec("candidate", candidate_dir),
            seeds=(7, 7),
            output_dir=tmp_path / "comparison",
        )


def test_compare_rejects_symlinked_agent_tree(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    baseline_dir.mkdir()
    candidate_dir.mkdir()
    (baseline_dir / "index.js").write_text("baseline")
    (candidate_dir / "index.js").symlink_to(baseline_dir / "index.js")

    with pytest.raises(ValueError, match="contains a symlink"):
        compare_agents(
            AgentSpec("baseline", baseline_dir),
            AgentSpec("candidate", candidate_dir),
            output_dir=tmp_path / "comparison",
        )


def test_compare_rejects_mixed_batch_identity(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    baseline_dir.mkdir()
    candidate_dir.mkdir()
    (baseline_dir / "index.js").write_text("baseline")
    (candidate_dir / "index.js").write_text("candidate")

    def evaluator(agents, **kwargs):
        root = kwargs["output_dir"]
        return AgentBatchReport(
            mission_id="wrong_mission",
            task_id="task",
            seed=kwargs["seed"],
            minecraft_seed=77,
            agents={
                agent.name: _report(agent.name, 50.0, root, seed=kwargs["seed"])
                for agent in agents
            },
            output_dir=root,
        )

    with pytest.raises(RuntimeError, match="mission mismatch"):
        compare_agents(
            AgentSpec("baseline", baseline_dir),
            AgentSpec("candidate", candidate_dir),
            output_dir=tmp_path / "comparison",
            evaluator=evaluator,
        )


def test_compare_rejects_non_identical_starting_spawns(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    baseline_dir.mkdir()
    candidate_dir.mkdir()
    (baseline_dir / "index.js").write_text("baseline")
    (candidate_dir / "index.js").write_text("candidate")

    def evaluator(agents, **kwargs):
        root = kwargs["output_dir"]
        reports = {
            agent.name: _report(agent.name, 50.0, root, seed=kwargs["seed"])
            for agent in agents
        }
        reports["candidate"].raw["spawn"]["position"] = [8, 70, -3]
        return AgentBatchReport(
            mission_id="resource_gathering",
            task_id="task",
            seed=kwargs["seed"],
            minecraft_seed=77,
            agents=reports,
            output_dir=root,
        )

    with pytest.raises(RuntimeError, match="non-identical starting spawn"):
        compare_agents(
            AgentSpec("baseline", baseline_dir),
            AgentSpec("candidate", candidate_dir),
            output_dir=tmp_path / "comparison",
            evaluator=evaluator,
        )
