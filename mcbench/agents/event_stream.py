from __future__ import annotations

import subprocess
import threading
import time
from queue import Empty, Queue
from typing import Callable, Iterator

from mcbench.evaluation.run_trace import TraceEvent, parse_trace_event_line


def drain_into(stream, sink: Callable[[str], None]) -> None:
    for line in stream:
        sink(line)


def pump_trace_events(
    child_process: subprocess.Popen[str],
    timeout_seconds: float,
    stderr_tail: Callable[[], list[str]],
) -> Iterator[TraceEvent]:
    queue: Queue[str] = Queue()
    threading.Thread(
        target=drain_into, args=(child_process.stdout, queue.put), daemon=True
    ).start()

    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            yield TraceEvent(kind="info", data={"msg": "timeout"})
            return
        if child_process.poll() is not None and queue.empty():
            if child_process.returncode:
                yield TraceEvent(
                    kind="error",
                    data={
                        "msg": f"agent exited with code {child_process.returncode}",
                        "stderr": stderr_tail()[-20:],
                    },
                )
            return
        try:
            line = queue.get(timeout=min(0.5, remaining))
        except Empty:
            continue
        event = parse_trace_event_line(line)
        if event is not None:
            yield event
        else:
            yield TraceEvent(kind="info", data={"msg": "stdout", "line": line.strip()})
