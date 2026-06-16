from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from mcrcon import MCRcon

from mcbench.infra.agents.base import Agent, AgentRunContext, AgentSpec
from mcbench.engine.runner import run_task
from mcbench.core.slot import Slot
from mcbench.core.task import RunConfig, Task
from mcbench.core.trace import FinalState, Trace, TraceEvent


class FakeAgent(Agent):
    def __init__(self) -> None:
        super().__init__(AgentSpec(name="fake_agent", path="/tmp/fake-agent"))
        self.ctx: AgentRunContext | None = None
        self.stop_called = False

    def run(self, ctx: AgentRunContext):
        self.ctx = ctx
        yield TraceEvent(kind="ready", data={})
        yield TraceEvent(kind="done", data={})

    def stop(self) -> None:
        self.stop_called = True


class FakeTask(Task):
    id = "fake_task"

    def __init__(self, commands: list[str]) -> None:
        self.commands = commands

    def default_config_path(self) -> Path:
        return Path("default.yaml")

    def load_config(self, path: str | Path) -> RunConfig:
        return RunConfig()

    def generate_instance(
        self,
        base_cfg: RunConfig,
        seed: int,
        instance_id: str | None = None,
    ):
        raise NotImplementedError

    def configure_world(self, mcr: MCRcon, cfg: RunConfig) -> None:
        self.commands.append("configure_world")

    def setup_agent(self, mcr: MCRcon, cfg: RunConfig):
        self.commands.append("setup_agent")
        return {"started": True}

    def goal_text(self, cfg: RunConfig) -> str:
        return "collect one log"

    def capture(self, mcr: MCRcon, cfg: RunConfig, setup_state):
        self.commands.append("capture")
        return {
            "final_state": FinalState(inventory={"oak_log": 1}),
            "setup_state": setup_state,
        }

    def score(self, cfg: RunConfig, trace, snapshot):
        return {
            "score": 1.0,
            "max_score": 1.0,
            "alive": True,
        }


class RunTaskTest(unittest.TestCase):
    def test_run_task_orchestrates_slot_agent_capture_and_artifacts(self) -> None:
        commands: list[str] = []

        @contextmanager
        def fake_rcon_session(*args, **kwargs):
            yield object()

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "run"
            slot = Slot(slot_id=2, rcon_password="secret")
            cfg = RunConfig(id="fake_instance", seed=42, duration_seconds=30)
            agent = FakeAgent()

            with (
                patch("mcbench.engine.runner._start_slot") as start_slot,
                patch("mcbench.engine.runner._stop_slot") as stop_slot,
                patch("mcbench.engine.runner.wait_for_ready") as wait_for_ready,
                patch("mcbench.engine.runner.rcon_session", side_effect=fake_rcon_session),
            ):
                report = run_task(
                    FakeTask(commands),
                    cfg,
                    agent,
                    slot=slot,
                    out_dir=out_dir,
                    record=None,
                )

            self.assertEqual(report["score"], 1.0)
            self.assertEqual(commands, ["configure_world", "setup_agent", "capture"])
            self.assertTrue(agent.stop_called)
            ctx = agent.ctx
            self.assertIsNotNone(ctx)
            self.assertEqual(ctx.goal, "collect one log")
            self.assertEqual(ctx.port, slot.game_port)

            start_slot.assert_called_once()
            wait_for_ready.assert_called_once()
            stop_slot.assert_called_once_with(slot, quiet=True)

            self.assertTrue((out_dir / "score.json").exists())
            self.assertTrue((out_dir / "config.json").exists())
            trace = Trace.load(out_dir / "trace.json")
            self.assertEqual(trace.instance_id, "fake_instance")
            self.assertEqual(trace.agent_name, "fake_agent")
            self.assertIsNotNone(trace.agent_ready_at)
            self.assertFalse(trace.timed_out)
            self.assertEqual(trace.final_state.inventory, {"oak_log": 1})


if __name__ == "__main__":
    unittest.main()
