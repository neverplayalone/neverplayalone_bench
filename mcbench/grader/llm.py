"""Optional LLM-based subjective grading via Claude.

The grader sends the task goal + a compact summary of the trace and asks
Claude to score against a rubric. Used for creative tasks where rule-based
checks aren't expressive enough (e.g., "build a nice house").
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..config import TaskConfig
from ..trace import Trace

MODEL = "claude-sonnet-4-6"


def _summarize_trace(trace: Trace, max_events: int = 200) -> dict[str, Any]:
    events = trace.events[-max_events:]
    return {
        "task_id": trace.task_id,
        "agent": trace.agent_name,
        "duration_s": (trace.ended_at or 0) - trace.started_at,
        "timed_out": trace.timed_out,
        "final_state": trace.final_state.model_dump(),
        "events": [{"kind": e.kind, "data": e.data} for e in events],
    }


def llm_grade(task: TaskConfig, trace: Trace) -> dict[str, Any]:
    """Return {scores: {criterion: 0..10}, reasoning: str}.

    Skips gracefully (returns empty) if no rubric or no API key.
    """
    if not task.success.llm_rubric:
        return {}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"skipped": "ANTHROPIC_API_KEY not set"}

    # Import lazily so the runner works without anthropic installed in dev
    from anthropic import Anthropic

    client = Anthropic()
    summary = _summarize_trace(trace)
    system = (
        "You are an evaluator for a Minecraft agent benchmark. "
        "Given the task goal, a rubric, and a JSON trace of what the agent did, "
        "score each rubric criterion 0–10. Return strict JSON: "
        '{"scores": {"<criterion>": <0-10>}, "reasoning": "<short>"}'
    )
    user = json.dumps(
        {
            "goal": task.goal,
            "rubric": task.success.llm_rubric,
            "trace_summary": summary,
        },
        default=str,
    )
    msg = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in msg.content if block.type == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text, "parse_error": True}
