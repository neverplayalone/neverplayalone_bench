from __future__ import annotations

import hashlib
import subprocess
import threading
from pathlib import Path
from typing import Iterator

from rich.console import Console

from mcbench.agents.base import Agent, AgentRunContext
from mcbench.agents.event_stream import pump_trace_events
from mcbench.config import (
    DEFAULT_SANDBOX_MEMORY,
    DEFAULT_SANDBOX_PIDS_LIMIT,
    DOCKER_DIR,
)
from mcbench.evaluation.run_trace import TraceEvent

console = Console()

AGENT_IMAGE_DIR = DOCKER_DIR / "agent"
AGENT_IMAGE_REPO = "mcbench-agent-runtime"
SERVER_CONTAINER_PORT = 25565


def agent_image_tag() -> str:
    recipe = b"".join(
        (AGENT_IMAGE_DIR / filename).read_bytes()
        for filename in ("package.json", "package-lock.json", "Dockerfile")
    )
    digest = hashlib.sha256(recipe).hexdigest()[:12]
    return f"{AGENT_IMAGE_REPO}:{digest}"


def ensure_agent_image(tag: str | None = None) -> str:
    tag = tag or agent_image_tag()
    present = (
        subprocess.run(
            ["docker", "image", "inspect", tag],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )
    if not present:
        console.log(f"Building agent runtime image [bold]{tag}[/] (one-time)...")
        result = subprocess.run(
            ["docker", "build", "-t", tag, str(AGENT_IMAGE_DIR)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"building agent image failed (exit {result.returncode})\n"
                f"--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
            )
    return tag


class SandboxedAgent(Agent):
    def __init__(
        self,
        spec,
        *,
        container_name: str,
        network_name: str,
        server_host: str,
        server_port: int = SERVER_CONTAINER_PORT,
        image: str | None = None,
        memory: str = DEFAULT_SANDBOX_MEMORY,
        pids_limit: int = DEFAULT_SANDBOX_PIDS_LIMIT,
    ):
        super().__init__(spec)
        self.container_name = container_name
        self.network_name = network_name
        self.server_host = server_host
        self.server_port = server_port
        self.image = image
        self.memory = memory
        self.pids_limit = pids_limit
        self.child_process: subprocess.Popen[str] | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lines: list[str] = []

    def docker_run_cmd(self, context: AgentRunContext, image: str) -> list[str]:
        agent_dir = Path(self.spec.path).resolve()
        if not agent_dir.is_dir():
            raise NotADirectoryError(
                f"docker mode needs an agent directory (with index.js), got {agent_dir}"
            )
        env = {
            "MCBENCH_HOST": self.server_host,
            "MCBENCH_PORT": str(self.server_port),
            "MCBENCH_AGENT_USERNAME": context.username,
            "MCBENCH_AGENT_PROMPT": context.prompt,
            "MCBENCH_TIMEOUT_SECONDS": str(context.timeout_seconds),
        }

        cmd = ["docker", "run", "--rm", "--name", self.container_name]
        cmd += ["--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
        cmd += ["--read-only", "--tmpfs", "/tmp:rw,size=64m"]
        cmd += ["--pids-limit", str(self.pids_limit), "--memory", self.memory]
        cmd += ["-v", f"{agent_dir}:/agent:ro", "-w", "/agent"]
        cmd += ["--network", self.network_name]
        for key, value in env.items():
            cmd += ["-e", f"{key}={value}"]
        cmd += [image, "node", "index.js"]
        return cmd

    def run(self, context: AgentRunContext) -> Iterator[TraceEvent]:
        image = self.image or ensure_agent_image()
        self.image = image
        subprocess.run(
            ["docker", "rm", "-f", self.container_name],
            capture_output=True,
            text=True,
        )
        command = self.docker_run_cmd(context, image)
        self.child_process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

        yield from pump_trace_events(
            self.child_process,
            context.timeout_seconds,
            lambda: self.stderr_log,
        )

    def stop(self) -> None:
        subprocess.run(
            ["docker", "rm", "-f", self.container_name],
            capture_output=True,
            text=True,
        )
        if self.child_process and self.child_process.poll() is None:
            try:
                self.child_process.terminate()
                self.child_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child_process.kill()
        self.child_process = None

    @property
    def stderr_log(self) -> list[str]:
        return list(self._stderr_lines)

    def _drain_stderr(self) -> None:
        assert self.child_process and self.child_process.stderr
        for line in self.child_process.stderr:
            self._stderr_lines.append(line.rstrip("\n"))
