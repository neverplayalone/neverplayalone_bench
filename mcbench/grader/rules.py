"""Rule-based checks. Each rule is a pure function of (Rule, Trace) → (passed, detail)."""

from __future__ import annotations

from collections import Counter
from typing import Callable

from ..config import Rule
from ..trace import Trace

CheckFn = Callable[[Rule, Trace], tuple[bool, str]]


def _events_with(trace: Trace, kind: str) -> list[dict]:
    return [e.data for e in trace.events if e.kind == kind]


def inventory_contains(rule: Rule, trace: Trace) -> tuple[bool, str]:
    if not rule.item:
        return False, "rule missing 'item'"
    have = trace.final_state.inventory.get(rule.item, 0)
    ok = have >= rule.min_count
    return ok, f"inventory[{rule.item}] = {have} (need ≥ {rule.min_count})"


def blocks_broken(rule: Rule, trace: Trace) -> tuple[bool, str]:
    """Count `dig` action events where data.block matches rule.block."""
    if not rule.block:
        return False, "rule missing 'block'"
    broken = Counter()
    for d in _events_with(trace, "action"):
        if d.get("action") == "dig":
            broken[d.get("block", "")] += 1
    n = broken.get(rule.block, 0)
    return n >= rule.min_count, f"broken[{rule.block}] = {n} (need ≥ {rule.min_count})"


def blocks_placed(rule: Rule, trace: Trace) -> tuple[bool, str]:
    if not rule.block:
        return False, "rule missing 'block'"
    placed = Counter()
    for d in _events_with(trace, "action"):
        if d.get("action") == "place":
            placed[d.get("block", "")] += 1
    n = placed.get(rule.block, 0)
    return n >= rule.min_count, f"placed[{rule.block}] = {n} (need ≥ {rule.min_count})"


def entities_killed(rule: Rule, trace: Trace) -> tuple[bool, str]:
    if not rule.entity:
        return False, "rule missing 'entity'"
    killed = Counter()
    for d in _events_with(trace, "kill"):
        killed[d.get("entity", "")] += 1
    n = killed.get(rule.entity, 0)
    return n >= rule.min_count, f"killed[{rule.entity}] = {n} (need ≥ {rule.min_count})"


CHECKS: dict[str, CheckFn] = {
    "inventory_contains": inventory_contains,
    "blocks_broken": blocks_broken,
    "blocks_placed": blocks_placed,
    "entities_killed": entities_killed,
}


def evaluate_rule(rule: Rule, trace: Trace) -> tuple[bool, str]:
    fn = CHECKS.get(rule.kind)
    if fn is None:
        return False, f"unknown rule kind: {rule.kind}"
    return fn(rule, trace)
