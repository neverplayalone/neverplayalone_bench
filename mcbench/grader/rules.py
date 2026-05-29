"""Rule-based checks. Each rule is a pure function of (Rule, Trace) → (passed, detail)."""

from __future__ import annotations

from typing import Callable

from ..config import Rule
from ..trace import Trace

CheckFn = Callable[[Rule, Trace], tuple[bool, str]]


def inventory_contains(rule: Rule, trace: Trace) -> tuple[bool, str]:
    if not rule.item:
        return False, "rule missing 'item'"
    have = trace.final_state.inventory.get(rule.item, 0)
    ok = have >= rule.min_count
    return ok, f"inventory[{rule.item}] = {have} (need ≥ {rule.min_count})"


def blocks_broken(rule: Rule, trace: Trace) -> tuple[bool, str]:
    """Server-authoritative count of blocks mined this episode (minecraft.mined stat)."""
    if not rule.block:
        return False, "rule missing 'block'"
    n = trace.final_state.blocks_broken.get(rule.block, 0)
    return n >= rule.min_count, f"broken[{rule.block}] = {n} (need ≥ {rule.min_count})"


def blocks_placed(rule: Rule, trace: Trace) -> tuple[bool, str]:
    """Server-authoritative count of blocks placed this episode (minecraft.used stat)."""
    if not rule.block:
        return False, "rule missing 'block'"
    n = trace.final_state.blocks_placed.get(rule.block, 0)
    return n >= rule.min_count, f"placed[{rule.block}] = {n} (need ≥ {rule.min_count})"


def entities_killed(rule: Rule, trace: Trace) -> tuple[bool, str]:
    """Server-authoritative count of mobs killed this episode (minecraft.killed stat)."""
    if not rule.entity:
        return False, "rule missing 'entity'"
    n = trace.final_state.entities_killed.get(rule.entity, 0)
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
