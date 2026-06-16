from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import mcbench.infra.agents.docker as docker_agent
from mcbench.infra.agents import DockerAgent, SubprocessAgent
from mcbench.infra.agents.base import AgentRunContext, AgentSpec
from mcbench.infra.agents.docker import agent_image_tag
from mcbench.engine.batch import make_agent
from mcbench.infra.minecraft.container import _ensure_slot_network, _stop_slot
from mcbench.core.slot import Slot


def _ctx() -> AgentRunContext:
    return AgentRunContext(
        host="127.0.0.1",
        port=25665,
        username="BenchmarkBot",
        goal="gather 64 logs",
        timeout_seconds=120,
    )


class AgentImageTagTest(unittest.TestCase):
    def test_tag_is_stable_and_namespaced(self) -> None:
        tag = agent_image_tag()
        self.assertTrue(tag.startswith("mcbench-agent-runtime:"))
        self.assertEqual(tag, agent_image_tag())  # deterministic

    def test_tag_changes_when_lockfile_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image_dir = Path(tmp)
            (image_dir / "package.json").write_text('{"dependencies":{}}\n')
            (image_dir / "package-lock.json").write_text('{"lockfileVersion":3}\n')
            (image_dir / "Dockerfile").write_text("FROM node:20-bookworm-slim\n")

            with patch.object(docker_agent, "AGENT_IMAGE_DIR", image_dir):
                first = docker_agent.agent_image_tag()
                (image_dir / "package-lock.json").write_text(
                    '{"lockfileVersion":3,"packages":{"node_modules/example":{}}}\n'
                )
                second = docker_agent.agent_image_tag()

        self.assertNotEqual(first, second)


class DockerRunCommandTest(unittest.TestCase):
    def test_command_has_sandbox_flags_and_mount(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent_dir = Path(tmp)
            agent = DockerAgent(
                AgentSpec(name="m", path=str(agent_dir)),
                container_name="mcbench-agent-0",
                network_name="mcbench-resource-0-net",
                server_host="mcbench-resource-0",
            )
            cmd = agent.docker_run_cmd(_ctx(), image="mcbench-agent-runtime:test")
            joined = " ".join(cmd)

            self.assertEqual(cmd[:3], ["docker", "run", "--rm"])
            self.assertIn("--name mcbench-agent-0", joined)
            # Isolation
            self.assertIn("--cap-drop ALL", joined)
            self.assertIn("no-new-privileges", joined)
            self.assertIn("--read-only", joined)
            self.assertIn("--pids-limit", joined)
            self.assertIn("--memory", joined)
            # Code mounted read-only
            self.assertIn(f"{agent_dir}:/agent:ro", joined)
            # Reaches the server over the dedicated per-slot Docker network.
            self.assertIn("--network mcbench-resource-0-net", joined)
            self.assertNotIn("host.docker.internal", joined)
            self.assertIn("MCBENCH_HOST=mcbench-resource-0", joined)
            self.assertIn("MCBENCH_PORT=25565", joined)
            # Image then entrypoint last
            self.assertEqual(cmd[-3:], ["mcbench-agent-runtime:test", "node", "index.js"])

    def test_requires_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "index.js"
            f.write_text("// not a dir")
            agent = DockerAgent(
                AgentSpec(name="m", path=str(f)),
                container_name="mcbench-agent-0",
                network_name="mcbench-resource-0-net",
                server_host="mcbench-resource-0",
            )
            with self.assertRaises(NotADirectoryError):
                agent.docker_run_cmd(_ctx(), image="x")


class MakeAgentTest(unittest.TestCase):
    def test_subprocess_mode(self) -> None:
        agent = make_agent(
            AgentSpec(name="m", path="/tmp"), mode="subprocess", slot=Slot(slot_id=2)
        )
        self.assertIsInstance(agent, SubprocessAgent)

    def test_docker_mode_names_container_per_slot(self) -> None:
        agent = make_agent(
            AgentSpec(name="m", path="/tmp"),
            mode="docker",
            slot=Slot(slot_id=3),
            image="mcbench-agent-runtime:test",
        )
        self.assertIsInstance(agent, DockerAgent)
        self.assertEqual(agent.container_name, "mcbench-agent-3")
        self.assertEqual(agent.network_name, "mcbench-resource-3-net")
        self.assertEqual(agent.server_host, "mcbench-resource-3")
        self.assertEqual(agent.image, "mcbench-agent-runtime:test")

    def test_slot_names_dedicated_network(self) -> None:
        slot = Slot(slot_id=4)
        self.assertEqual(slot.network_name, "mcbench-resource-4-net")


class SlotNetworkTest(unittest.TestCase):
    def test_ensure_slot_network_creates_internal_network_when_missing(self) -> None:
        calls = []

        def fake_run(cmd, check=False, capture_output=True, text=True):
            calls.append(cmd)

            class Result:
                returncode = 1 if cmd[:3] == ["docker", "network", "inspect"] else 0
                stderr = ""
                stdout = ""

            return Result()

        with patch("mcbench.infra.minecraft.container.subprocess.run", side_effect=fake_run):
            _ensure_slot_network(Slot(slot_id=5))

        self.assertEqual(calls[0], ["docker", "network", "inspect", "mcbench-resource-5-net"])
        self.assertEqual(
            calls[1],
            ["docker", "network", "create", "--internal", "mcbench-resource-5-net"],
        )

    def test_stop_slot_removes_dedicated_network(self) -> None:
        calls = []

        def fake_run(cmd, check=False, capture_output=True, text=True):
            calls.append(cmd)

            class Result:
                returncode = 0
                stderr = ""
                stdout = ""

            return Result()

        with patch("mcbench.infra.minecraft.container.subprocess.run", side_effect=fake_run):
            _stop_slot(Slot(slot_id=6))

        self.assertEqual(calls[0], ["docker", "rm", "-f", "mcbench-resource-6"])
        self.assertEqual(calls[1], ["docker", "network", "rm", "mcbench-resource-6-net"])

    def test_stop_slot_ignores_missing_dedicated_network(self) -> None:
        calls = []

        def fake_run(cmd, check=False, capture_output=True, text=True):
            calls.append(cmd)

            class Result:
                returncode = 1
                stderr = (
                    "No such container: mcbench-resource-7"
                    if cmd[:3] == ["docker", "rm", "-f"]
                    else "Error response from daemon: network mcbench-resource-7-net not found"
                )
                stdout = ""

            return Result()

        with patch("mcbench.infra.minecraft.container.subprocess.run", side_effect=fake_run):
            _stop_slot(Slot(slot_id=7))

        self.assertEqual(calls[0], ["docker", "rm", "-f", "mcbench-resource-7"])
        self.assertEqual(calls[1], ["docker", "network", "rm", "mcbench-resource-7-net"])

    def test_unknown_mode_raises(self) -> None:
        with self.assertRaises(ValueError):
            make_agent(AgentSpec(name="m", path="/tmp"), mode="vm", slot=Slot())


if __name__ == "__main__":
    unittest.main()
