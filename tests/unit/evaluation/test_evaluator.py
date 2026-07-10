from __future__ import annotations

import json

import pytest

from npabench.agents.base import AgentSpec
from npabench.evaluation.evaluate import (
    AgentBatchReport,
    AgentMode,
    AgentRunReport,
    evaluate_multiple_agents,
    evaluate_single_agent,
)
from npabench.missions.base import MissionConfig


def test_evaluate_returns_typed_report(monkeypatch, tmp_path, fake_mission) -> None:
    agent_spec = AgentSpec(name="agent_a", path=tmp_path / "agent_a")
    agent_spec.path.mkdir()

    monkeypatch.setattr("npabench.evaluation.evaluate.get_mission", lambda mission_id: fake_mission)
    monkeypatch.setattr(
        "npabench.evaluation.evaluate._load_mission_config",
        lambda mission, config_path: MissionConfig(id="resource_gathering"),
    )

    class FakeBuilder:
        def build(self, mission, mission_config, output_dir, **kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            return output_dir

    monkeypatch.setattr("npabench.evaluation.evaluate.ReferenceWorldBuilder", lambda: FakeBuilder())
    monkeypatch.setattr(
        "npabench.evaluation.evaluate.run_single_evaluation",
        lambda *args, **kwargs: AgentRunReport(
            agent_name="agent_a",
            agent_kind=None,
            mission_id="resource_gathering",
            task_id="fake-task",
            task_prompt="collect one log",
            seed=0,
            minecraft_seed=0,
            score=1.0,
            max_score=1.0,
            status="ok",
            output_dir=tmp_path / "out",
            trace_path=tmp_path / "out" / "trace.json",
            recording_path=None,
            raw={"score": 1.0, "max_score": 1.0},
        ),
    )

    report = evaluate_single_agent(
        agent_spec,
        mission_id="resource_gathering",
        output_dir=tmp_path / "results",
        agent_mode=AgentMode.HOST,
    )

    assert isinstance(report, AgentRunReport)
    assert report.agent_name == "agent_a"
    assert report.status == "ok"


def test_evaluate_multiple_agents_returns_dict_keyed_by_agent(
    monkeypatch, tmp_path, fake_mission
) -> None:
    agent_a = AgentSpec(name="agent_a", path=tmp_path / "agent_a")
    agent_b = AgentSpec(name="agent_b", path=tmp_path / "agent_b")
    agent_a.path.mkdir()
    agent_b.path.mkdir()

    monkeypatch.setattr("npabench.evaluation.evaluate.get_mission", lambda mission_id: fake_mission)
    monkeypatch.setattr(
        "npabench.evaluation.evaluate._load_mission_config",
        lambda mission, config_path: MissionConfig(id="resource_gathering"),
    )

    class FakeBuilder:
        def build(self, mission, mission_config, output_dir, **kwargs):
            output_dir.mkdir(parents=True, exist_ok=True)
            return output_dir

    monkeypatch.setattr("npabench.evaluation.evaluate.ReferenceWorldBuilder", lambda: FakeBuilder())
    monkeypatch.setattr(
        "npabench.evaluation.evaluate.run_batch_evaluation",
        lambda *args, **kwargs: {
            "agent_a": AgentRunReport(
                agent_name="agent_a",
                agent_kind=None,
                mission_id="resource_gathering",
                task_id="fake-task",
                task_prompt="collect one log",
                seed=0,
                minecraft_seed=0,
                score=1.0,
                max_score=1.0,
                status="ok",
                output_dir=tmp_path / "results" / "agent_a",
                trace_path=tmp_path / "results" / "agent_a" / "trace.json",
                recording_path=None,
                raw={"score": 1.0, "max_score": 1.0},
            ),
            "agent_b": AgentRunReport(
                agent_name="agent_b",
                agent_kind=None,
                mission_id="resource_gathering",
                task_id="fake-task",
                task_prompt="collect one log",
                seed=0,
                minecraft_seed=0,
                score=2.0,
                max_score=2.0,
                status="ok",
                output_dir=tmp_path / "results" / "agent_b",
                trace_path=tmp_path / "results" / "agent_b" / "trace.json",
                recording_path=None,
                raw={"score": 2.0, "max_score": 2.0},
            ),
        },
    )

    batch_report = evaluate_multiple_agents(
        [agent_a, agent_b],
        mission_id="resource_gathering",
        output_dir=tmp_path / "results",
        agent_mode=AgentMode.HOST,
    )

    assert isinstance(batch_report, AgentBatchReport)
    assert set(batch_report.agents) == {"agent_a", "agent_b"}


def test_evaluate_multiple_agents_rejects_output_safe_name_collisions(
    monkeypatch,
    tmp_path,
    fake_mission,
) -> None:
    agent_a = AgentSpec(name="agent/a", path=tmp_path / "agent_a")
    agent_b = AgentSpec(name="agent a", path=tmp_path / "agent_b")
    agent_a.path.mkdir()
    agent_b.path.mkdir()

    monkeypatch.setattr("npabench.evaluation.evaluate.get_mission", lambda mission_id: fake_mission)
    monkeypatch.setattr(
        "npabench.evaluation.evaluate._load_mission_config",
        lambda mission, config_path: MissionConfig(id="resource_gathering"),
    )

    with pytest.raises(ValueError, match="unique output-safe names"):
        evaluate_multiple_agents(
            [agent_a, agent_b],
            mission_id="resource_gathering",
            output_dir=tmp_path / "results",
            agent_mode=AgentMode.HOST,
        )


def test_evaluate_single_agent_uses_timestamp_run_root_by_default(
    monkeypatch,
    tmp_path,
    fake_mission,
) -> None:
    agent_spec = AgentSpec(name="agent_a", path=tmp_path / "agent_a")
    agent_spec.path.mkdir()
    run_root = tmp_path / "results" / "20260630_120000"
    captured: dict[str, object] = {}

    monkeypatch.setattr("npabench.evaluation.evaluate.RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr("npabench.evaluation.evaluate._run_id_now", lambda: "20260630_120000")
    monkeypatch.setattr("npabench.evaluation.evaluate.get_mission", lambda mission_id: fake_mission)
    monkeypatch.setattr(
        "npabench.evaluation.evaluate._load_mission_config",
        lambda mission, config_path: MissionConfig(id="resource_gathering"),
    )

    class FakeBuilder:
        def build(self, mission, mission_config, output_dir, **kwargs):
            captured["reference_world_dir"] = output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            return output_dir

    monkeypatch.setattr("npabench.evaluation.evaluate.ReferenceWorldBuilder", lambda: FakeBuilder())

    def fake_run_single_evaluation(*args, **kwargs):
        output_dir = kwargs["output_dir"]
        captured["agent_output_dir"] = output_dir
        return AgentRunReport(
            agent_name="agent_a",
            agent_kind=None,
            mission_id="resource_gathering",
            task_id="fake-task",
            task_prompt="collect one log",
            seed=0,
            minecraft_seed=0,
            score=1.0,
            max_score=1.0,
            status="ok",
            output_dir=output_dir,
            trace_path=output_dir / "trace.json",
            recording_path=None,
            raw={"score": 1.0, "max_score": 1.0},
        )

    monkeypatch.setattr(
        "npabench.evaluation.evaluate.run_single_evaluation",
        fake_run_single_evaluation,
    )

    report = evaluate_single_agent(
        agent_spec,
        mission_id="resource_gathering",
        agent_mode=AgentMode.HOST,
    )

    assert report.output_dir == run_root / "agents" / "agent_a"
    assert captured["reference_world_dir"] == run_root / "reference_world"
    assert captured["agent_output_dir"] == run_root / "agents" / "agent_a"
    task = json.loads((run_root / "task.json").read_text())
    assert task["task_id"] == "fake-task"
    assert task["seed"] == 0


def test_evaluate_multiple_agents_uses_timestamp_run_root_by_default(
    monkeypatch,
    tmp_path,
    fake_mission,
) -> None:
    agent_a = AgentSpec(name="agent_a", path=tmp_path / "agent_a")
    agent_b = AgentSpec(name="agent_b", path=tmp_path / "agent_b")
    agent_a.path.mkdir()
    agent_b.path.mkdir()
    run_root = tmp_path / "results" / "20260630_120000"
    captured: dict[str, object] = {}

    monkeypatch.setattr("npabench.evaluation.evaluate.RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr("npabench.evaluation.evaluate._run_id_now", lambda: "20260630_120000")
    monkeypatch.setattr("npabench.evaluation.evaluate.get_mission", lambda mission_id: fake_mission)
    monkeypatch.setattr(
        "npabench.evaluation.evaluate._load_mission_config",
        lambda mission, config_path: MissionConfig(id="resource_gathering"),
    )

    class FakeBuilder:
        def build(self, mission, mission_config, output_dir, **kwargs):
            captured["reference_world_dir"] = output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            return output_dir

    monkeypatch.setattr("npabench.evaluation.evaluate.ReferenceWorldBuilder", lambda: FakeBuilder())

    def fake_run_batch_evaluation(*args, **kwargs):
        captured["agents_output_dir"] = kwargs["output_dir"]
        return {
            "agent_a": AgentRunReport(
                agent_name="agent_a",
                agent_kind=None,
                mission_id="resource_gathering",
                task_id="fake-task",
                task_prompt="collect one log",
                seed=0,
                minecraft_seed=0,
                score=1.0,
                max_score=1.0,
                status="ok",
                output_dir=run_root / "agents" / "agent_a",
                trace_path=run_root / "agents" / "agent_a" / "trace.json",
                recording_path=None,
                raw={"score": 1.0, "max_score": 1.0},
            ),
            "agent_b": AgentRunReport(
                agent_name="agent_b",
                agent_kind=None,
                mission_id="resource_gathering",
                task_id="fake-task",
                task_prompt="collect one log",
                seed=0,
                minecraft_seed=0,
                score=2.0,
                max_score=2.0,
                status="ok",
                output_dir=run_root / "agents" / "agent_b",
                trace_path=run_root / "agents" / "agent_b" / "trace.json",
                recording_path=None,
                raw={"score": 2.0, "max_score": 2.0},
            ),
        }

    monkeypatch.setattr(
        "npabench.evaluation.evaluate.run_batch_evaluation",
        fake_run_batch_evaluation,
    )

    batch_report = evaluate_multiple_agents(
        [agent_a, agent_b],
        mission_id="resource_gathering",
        agent_mode=AgentMode.HOST,
    )

    assert batch_report.output_dir == run_root
    assert captured["reference_world_dir"] == run_root / "reference_world"
    assert captured["agents_output_dir"] == run_root / "agents"
    task = json.loads((run_root / "task.json").read_text())
    assert task["task_id"] == "fake-task"
    assert task["seed"] == 0
