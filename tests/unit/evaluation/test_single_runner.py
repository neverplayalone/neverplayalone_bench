from __future__ import annotations

import json

import pytest

from npabench.agents.base import AgentSpec
from npabench.evaluation.evaluate import AgentMode
from npabench.evaluation.run_slot import AgentRunSlot, ServerEndpoint
from npabench.evaluation.single_runner import run_single_evaluation
from npabench.missions.base import MissionConfig


@pytest.fixture(autouse=True)
def fake_clean_movement_monitor(monkeypatch) -> None:
    class FakeMovementMonitor:
        def start(self) -> None:
            pass

        def stop(self) -> dict:
            return {"enabled": True, "violated": False}

    monkeypatch.setattr(
        "npabench.evaluation.single_runner._create_movement_monitor",
        lambda *args, **kwargs: FakeMovementMonitor(),
    )


def test_run_single_evaluation_writes_artifacts(
    monkeypatch,
    tmp_path,
    fake_agent,
    fake_mission,
    fake_rcon_session,
) -> None:
    agent_spec = AgentSpec(name="fake_agent", path=tmp_path / "agent")
    agent_spec.path.mkdir()
    mission_config = MissionConfig(id="fake-task", seed=42, duration_seconds=30)
    agent_run_slot = AgentRunSlot.allocate(slot_id=2, data_root=tmp_path / "slot")

    monkeypatch.setattr("npabench.evaluation.single_runner.create_agent", lambda *args, **kwargs: fake_agent)
    monkeypatch.setattr("npabench.evaluation.single_runner.ensure_agent_image", lambda: "image")
    monkeypatch.setattr("npabench.evaluation.single_runner.start_agent_run_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr("npabench.evaluation.single_runner.stop_agent_run_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr("npabench.evaluation.single_runner.cleanup_run_worlds", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "npabench.evaluation.single_runner._wait_for_slot_ready",
        lambda slot: ServerEndpoint(
            host=slot.host,
            game_port=slot.game_port,
            rcon_port=slot.rcon_port,
            rcon_password=slot.rcon_password,
        ),
    )
    monkeypatch.setattr("npabench.evaluation.single_runner.rcon_session", fake_rcon_session)

    report = run_single_evaluation(
        fake_mission,
        mission_config,
        agent_run_slot,
        agent_spec,
        reference_world_dir=tmp_path / "reference_world",
        recording=False,
        agent_mode=AgentMode.HOST,
        output_dir=tmp_path / "run",
        task_seed=7,
    )

    assert report.score == 1.0
    assert report.seed == 7
    assert report.minecraft_seed == 42
    assert report.task_prompt == "collect one log"
    assert fake_agent.stop_called is True
    assert fake_mission.calls == ["configure_world", "setup_agent", "collect_final_state"]
    assert (tmp_path / "run" / "trace.json").exists()
    assert (tmp_path / "run" / "report.json").exists()
    assert (tmp_path / "run" / "raw_report.json").exists()
    written = json.loads((tmp_path / "run" / "report.json").read_text())
    assert written["task_prompt"] == "collect one log"


def test_run_single_evaluation_prefers_ranking_score_when_present(
    monkeypatch,
    tmp_path,
    fake_agent,
    fake_mission,
    fake_rcon_session,
) -> None:
    agent_spec = AgentSpec(name="fake_agent", path=tmp_path / "agent")
    agent_spec.path.mkdir()
    mission_config = MissionConfig(id="fake-task", seed=42, duration_seconds=30)
    agent_run_slot = AgentRunSlot.allocate(slot_id=2, data_root=tmp_path / "slot")

    monkeypatch.setattr("npabench.evaluation.single_runner.create_agent", lambda *args, **kwargs: fake_agent)
    monkeypatch.setattr("npabench.evaluation.single_runner.ensure_agent_image", lambda: "image")
    monkeypatch.setattr("npabench.evaluation.single_runner.start_agent_run_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr("npabench.evaluation.single_runner.stop_agent_run_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr("npabench.evaluation.single_runner.cleanup_run_worlds", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "npabench.evaluation.single_runner._wait_for_slot_ready",
        lambda slot: ServerEndpoint(
            host=slot.host,
            game_port=slot.game_port,
            rcon_port=slot.rcon_port,
            rcon_password=slot.rcon_password,
        ),
    )
    monkeypatch.setattr("npabench.evaluation.single_runner.rcon_session", fake_rcon_session)
    monkeypatch.setattr(
        fake_mission,
        "score",
        lambda mission_config, agent_run_trace, final_snapshot: {
            "score": 1.0,
            "ranking_score": 1.000001,
            "max_score": 1.0,
            "status": "ok",
        },
    )

    report = run_single_evaluation(
        fake_mission,
        mission_config,
        agent_run_slot,
        agent_spec,
        reference_world_dir=tmp_path / "reference_world",
        recording=False,
        agent_mode=AgentMode.HOST,
        output_dir=tmp_path / "run",
        task_seed=7,
    )

    assert report.score == 1.000001


def test_run_single_evaluation_returns_error_report_when_setup_fails(
    monkeypatch,
    tmp_path,
    fake_agent,
    fake_mission,
    fake_rcon_session,
) -> None:
    agent_spec = AgentSpec(name="fake_agent", path=tmp_path / "agent")
    agent_spec.path.mkdir()
    mission_config = MissionConfig(id="fake-task", seed=42, duration_seconds=30)
    agent_run_slot = AgentRunSlot.allocate(slot_id=2, data_root=tmp_path / "slot")

    monkeypatch.setattr("npabench.evaluation.single_runner.create_agent", lambda *args, **kwargs: fake_agent)
    monkeypatch.setattr("npabench.evaluation.single_runner.start_agent_run_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr("npabench.evaluation.single_runner.stop_agent_run_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr("npabench.evaluation.single_runner.cleanup_run_worlds", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "npabench.evaluation.single_runner._wait_for_slot_ready",
        lambda slot: ServerEndpoint(
            host=slot.host,
            game_port=slot.game_port,
            rcon_port=slot.rcon_port,
            rcon_password=slot.rcon_password,
        ),
    )
    monkeypatch.setattr("npabench.evaluation.single_runner.rcon_session", fake_rcon_session)
    monkeypatch.setattr(
        fake_mission,
        "setup_agent",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("RCON timed out")),
    )

    report = run_single_evaluation(
        fake_mission,
        mission_config,
        agent_run_slot,
        agent_spec,
        reference_world_dir=tmp_path / "reference_world",
        recording=False,
        agent_mode=AgentMode.HOST,
        output_dir=tmp_path / "run",
    )

    assert report.status == "error"
    assert report.score == 0.0
    assert report.raw["error"] == "RCON timed out"
    assert (tmp_path / "run" / "report.json").exists()


def test_run_single_evaluation_zeroes_score_on_movement_violation(
    monkeypatch,
    tmp_path,
    fake_agent,
    fake_mission,
    fake_rcon_session,
) -> None:
    class FakeMovementMonitor:
        def start(self) -> None:
            pass

        def stop(self) -> dict:
            return {
                "enabled": True,
                "violated": True,
                "violation": {"reason": "impossible_horizontal_movement"},
            }

    agent_spec = AgentSpec(name="fake_agent", path=tmp_path / "agent")
    agent_spec.path.mkdir()
    mission_config = MissionConfig(id="fake-task", seed=42, duration_seconds=30)
    agent_run_slot = AgentRunSlot.allocate(slot_id=2, data_root=tmp_path / "slot")

    monkeypatch.setattr("npabench.evaluation.single_runner.create_agent", lambda *args, **kwargs: fake_agent)
    monkeypatch.setattr("npabench.evaluation.single_runner.ensure_agent_image", lambda: "image")
    monkeypatch.setattr("npabench.evaluation.single_runner.start_agent_run_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr("npabench.evaluation.single_runner.stop_agent_run_slot", lambda *args, **kwargs: None)
    monkeypatch.setattr("npabench.evaluation.single_runner.cleanup_run_worlds", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "npabench.evaluation.single_runner._wait_for_slot_ready",
        lambda slot: ServerEndpoint(
            host=slot.host,
            game_port=slot.game_port,
            rcon_port=slot.rcon_port,
            rcon_password=slot.rcon_password,
        ),
    )
    monkeypatch.setattr("npabench.evaluation.single_runner.rcon_session", fake_rcon_session)
    monkeypatch.setattr(
        "npabench.evaluation.single_runner._create_movement_monitor",
        lambda *args, **kwargs: FakeMovementMonitor(),
    )

    report = run_single_evaluation(
        fake_mission,
        mission_config,
        agent_run_slot,
        agent_spec,
        reference_world_dir=tmp_path / "reference_world",
        recording=False,
        agent_mode=AgentMode.HOST,
        output_dir=tmp_path / "run",
    )

    assert report.status == "movement_violation"
    assert report.score == 0.0
    assert report.raw["status"] == "movement_violation"
    assert (tmp_path / "run" / "movement_monitor.json").exists()
