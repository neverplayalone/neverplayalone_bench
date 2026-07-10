from __future__ import annotations

from npabench.agents.base import AgentSpec
from npabench.evaluation.batch_runner import run_batch_evaluation
from npabench.evaluation.evaluate import AgentMode, AgentRunReport
from npabench.evaluation.run_slot import AgentRunSlot
from npabench.missions.base import MissionConfig


def test_run_batch_evaluation_uses_safe_output_dir_names(
    monkeypatch,
    tmp_path,
    fake_mission,
) -> None:
    output_dirs = []

    def fake_run_single_evaluation(*args, **kwargs):
        output_dir = kwargs["output_dir"]
        output_dirs.append(output_dir)
        return AgentRunReport(
            agent_name="agent/a",
            agent_kind=None,
            mission_id="fake",
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
            raw={},
        )

    monkeypatch.setattr(
        "npabench.evaluation.batch_runner.run_single_evaluation",
        fake_run_single_evaluation,
    )

    run_batch_evaluation(
        fake_mission,
        MissionConfig(id="fake-task"),
        [AgentRunSlot.allocate(data_root=tmp_path / "slot")],
        [AgentSpec(name="agent/a", path=tmp_path)],
        reference_world_dir=tmp_path / "world",
        recording=False,
        agent_mode=AgentMode.HOST,
        output_dir=tmp_path / "agents",
    )

    assert output_dirs == [tmp_path / "agents" / "agent_a"]
