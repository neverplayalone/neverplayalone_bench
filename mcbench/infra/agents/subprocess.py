"""SubprocessAgent: launch a child process (Node.js, Python, anything) and stream JSONL events from its stdout.

The child receives connection info as env vars:
    MCBENCH_HOST, MCBENCH_PORT, MCBENCH_USERNAME, MCBENCH_GOAL, MCBENCH_TIMEOUT

The child must emit one JSON object per line on stdout, each shaped like:
    {"kind": "action", "data": {"action": "dig", "block": "oak_log"}}

If the child is a directory, it's executed with `node index.js` when a package.json is present,
otherwise with `python main.py`.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Iterator

from mcbench.core.trace import TraceEvent
from mcbench.infra.agents.base import Agent, AgentRunContext
from mcbench.infra.agents._process_stream import pump_events


def _detect_launch(path: Path) -> list[str]:
    if (path / "package.json").exists():
        return ["node", "index.js"]
    if (path / "main.py").exists():
        return ["python", "main.py"]
    if path.is_file() and os.access(path, os.X_OK):
        return [str(path)]
    raise FileNotFoundError(f"Don't know how to launch agent at {path}")


class SubprocessAgent(Agent):
    def __init__(self, spec):
        super().__init__(spec)
        self.proc: subprocess.Popen | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lines: list[str] = []

    def run(self, ctx: AgentRunContext) -> Iterator[TraceEvent]:
        path = Path(self.spec.path).resolve()
        cmd = _detect_launch(path) + (self.spec.extra_args or [])
        env = {
            **os.environ,
            "MCBENCH_HOST": ctx.host,
            "MCBENCH_PORT": str(ctx.port),
            "MCBENCH_USERNAME": ctx.username,
            "MCBENCH_GOAL": ctx.goal,
            "MCBENCH_TIMEOUT": str(ctx.timeout_seconds),
        }
        cwd = path if path.is_dir() else path.parent
        self.proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True
        )
        self._stderr_thread.start()

        yield from pump_events(self.proc, ctx.timeout_seconds, lambda: self.stderr_log)

    def stop(self) -> None:
        if not self.proc:
            return
        if self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.SIGTERM)
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    @property
    def stderr_log(self) -> list[str]:
        return list(self._stderr_lines)

    def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            self._stderr_lines.append(line.rstrip("\n"))
