"""Compose rule-based + LLM grading into a single report."""

from __future__ import annotations

from typing import Any

from ..config import TaskConfig
from ..trace import Trace
from .llm import llm_grade
from .rules import evaluate_rule


def grade(task: TaskConfig, trace: Trace) -> dict[str, Any]:
    rule_results = []
    passed = 0
    for rule in task.success.rules:
        ok, detail = evaluate_rule(rule, trace)
        rule_results.append({"kind": rule.kind, "passed": ok, "detail": detail})
        passed += int(ok)
    n_rules = len(task.success.rules)
    rule_score = (passed / n_rules) if n_rules else None

    llm_result = llm_grade(task, trace)
    llm_score: float | None = None
    if isinstance(llm_result, dict) and "scores" in llm_result:
        vals = list(llm_result["scores"].values())
        if vals:
            llm_score = sum(vals) / (len(vals) * 10.0)  # normalize to 0..1

    # Combined score: rule-based dominates if present; LLM fills the gap.
    if rule_score is not None and llm_score is not None:
        score = 0.7 * rule_score + 0.3 * llm_score
    elif rule_score is not None:
        score = rule_score
    elif llm_score is not None:
        score = llm_score
    else:
        score = 0.0

    outcome = "pass" if score >= task.success.threshold else "fail"

    return {
        "task_id": task.id,
        "agent": trace.agent_name,
        "outcome": outcome,
        "score": score,
        "rule_score": rule_score,
        "llm_score": llm_score,
        "rules": rule_results,
        "llm": llm_result,
        "timed_out": trace.timed_out,
    }
