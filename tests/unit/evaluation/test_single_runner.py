from __future__ import annotations

import json

from npabench.agents.base import AgentSpec
from npabench.evaluation.evaluate import AgentMode
from npabench.evaluation.run_slot import AgentRunSlot, ServerEndpoint
from npabench.evaluation.single_runner import run_single_evaluation
from npabench.missions.base import MissionConfig


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
    assert report.task_prompt == "fake mission prompt"
    assert fake_agent.stop_called is True
    assert fake_mission.calls == ["configure_world", "setup_agent", "collect_final_state"]
    assert (tmp_path / "run" / "trace.json").exists()
    assert (tmp_path / "run" / "report.json").exists()
    assert (tmp_path / "run" / "raw_report.json").exists()
    written = json.loads((tmp_path / "run" / "report.json").read_text())
    assert written["task_prompt"] == "fake mission prompt"


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
