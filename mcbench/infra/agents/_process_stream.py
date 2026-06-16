"""Shared stdout/stderr streaming for process-backed agents.

Both :class:`SubprocessAgent` and :class:`DockerAgent` launch a child process
that emits one JSON event per line on stdout; the only real difference is the
launch command (``node index.js`` vs ``docker run ...``). This module owns the
read loop and the timeout policy so both behave identically.
"""

from __future__ import annotations

import subprocess
import threading
import time
from queue import Empty, Queue
from typing import Callable, Iterator

from mcbench.core.trace import TraceEvent, parse_event_line


def drain_into(stream, sink: Callable[[str], None]) -> None:
    """Forward every line of ``stream`` to ``sink`` until the stream closes."""
    for line in stream:
        sink(line)


def pump_events(
    proc: subprocess.Popen,
    timeout_seconds: float,
    stderr_tail: Callable[[], list[str]],
) -> Iterator[TraceEvent]:
    """Yield TraceEvents from a child's stdout until it exits or times out.

    On timeout the process is left running on purpose: the caller captures the
    final world state over RCON while the bot is still connected, then calls
    ``stop()``. Killing here would disconnect the player before the snapshot.
    """
    queue: Queue[str] = Queue()
    threading.Thread(
        target=drain_into, args=(proc.stdout, queue.put), daemon=True
    ).start()

    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            yield TraceEvent(kind="info", data={"msg": "timeout"})
            return
        if proc.poll() is not None and queue.empty():
            if proc.returncode:
                yield TraceEvent(
                    kind="error",
                    data={
                        "msg": f"agent exited with code {proc.returncode}",
                        "stderr": stderr_tail()[-20:],
                    },
                )
            return
        try:
            line = queue.get(timeout=min(0.5, remaining))
        except Empty:
            continue
        event = parse_event_line(line)
        if event is not None:
            yield event
        else:
            yield TraceEvent(kind="info", data={"msg": "stdout", "line": line.strip()})
