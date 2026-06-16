"""Low-level RCON output parsers and read primitives."""

from __future__ import annotations

import re

from mcrcon import MCRcon

def _block_matches(mcr: MCRcon, x: int, y: int, z: int, block: str) -> bool:
    raw = mcr.command(f"execute if block {x} {y} {z} {block} run time query gametime")
    return _rcon_test_passed(raw)


def _rcon_test_passed(raw: str) -> bool:
    text = raw.strip().lower()
    return "time is" in text


def _count_item(mcr: MCRcon, username: str, item: str) -> int:
    raw = mcr.command(f"clear {username} minecraft:{item} 0")
    m = re.search(r"\b(\d+)\b", raw)
    return int(m.group(1)) if (m and "found" in raw.lower()) else 0


def _read_score(mcr: MCRcon, username: str, objective: str) -> int:
    raw = mcr.command(f"scoreboard players get {username} {objective}")
    m = re.search(r"has (-?\d+)", raw)
    return int(m.group(1)) if m else 0


_NUM = r"-?\d+(?:\.\d+)?"


def _parse_pos(raw: str) -> tuple[float, float, float] | None:
    m = re.search(rf"\[({_NUM})d?, ({_NUM})d?, ({_NUM})d?\]", raw)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


def _parse_scalar(raw: str) -> float | None:
    m = re.search(rf"({_NUM})[a-zA-Z]?\s*$", raw.strip())
    return float(m.group(1)) if m else None


