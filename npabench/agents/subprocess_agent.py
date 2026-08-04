from __future__ import annotations

import os
import signal
import subprocess
import threading
from typing import Iterator, TextIO

from npabench.agents.base import Agent, AgentRunContext
from npabench.agents.event_stream import pump_trace_events
from npabench.agents.launcher import detect_launch
from npabench.evaluation.run_trace import TraceEvent


class SubprocessAgent(Agent):
    def __init__(self, spec):
        super().__init__(spec)
        self.child_process: subprocess.Popen[str] | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lines: list[str] = []

    def run(self, context: AgentRunContext) -> Iterator[TraceEvent]:
        path = self.spec.path.resolve()
        command = detect_launch(path) + (self.spec.extra_args or [])
        env = {
            **os.environ,
            "NPABENCH_HOST": context.host,
            "NPABENCH_PORT": str(context.port),
            "NPABENCH_AGENT_USERNAME": context.username,
            "NPABENCH_AGENT_PROMPT": context.prompt,
            "NPABENCH_TIMEOUT_SECONDS": str(context.timeout_seconds),
        }
        cwd = path if path.is_dir() else path.parent
        self.child_process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(self.child_process.stderr,),
            daemon=True,
        )
        self._stderr_thread.start()

        yield from pump_trace_events(
            self.child_process,
            context.timeout_seconds,
            lambda: self.stderr_log,
        )

    def stop(self) -> None:
        process = self.child_process
        if not process:
            return
        if process.poll() is None:
            try:
                process.send_signal(signal.SIGTERM)
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=2)
            self._stderr_thread = None
        self.child_process = None

    @property
    def stderr_log(self) -> list[str]:
        return list(self._stderr_lines)

    def _drain_stderr(self, stream: TextIO) -> None:
        # Capture the stream before the thread starts. `stop()` is allowed to
        # clear `child_process` as soon as the process exits, and looking it up
        # again here races with fast agents and test doubles.
        for line in stream:
            self._stderr_lines.append(line.rstrip("\n"))
