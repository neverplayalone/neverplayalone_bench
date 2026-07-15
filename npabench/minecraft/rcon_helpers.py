from __future__ import annotations

import re

from mcrcon import MCRcon

from npabench.minecraft.rcon_client import command_with_retry

def block_matches(rcon: MCRcon, x: int, y: int, z: int, block: str) -> bool:
    rcon_response = command_with_retry(
        rcon, f"execute if block {x} {y} {z} {block} run time query gametime"
    )
    return rcon_test_passed(rcon_response)


def rcon_test_passed(rcon_response: str) -> bool:
    text = rcon_response.strip().lower()
    return "time is" in text


def count_item(rcon: MCRcon, username: str, item: str) -> int:
    rcon_response = command_with_retry(rcon, f"clear {username} minecraft:{item} 0")
    match = re.search(r"\b(\d+)\b", rcon_response)
    return int(match.group(1)) if (match and "found" in rcon_response.lower()) else 0


def read_score(rcon: MCRcon, username: str, objective: str) -> int:
    rcon_response = command_with_retry(rcon, f"scoreboard players get {username} {objective}")
    match = re.search(r"has (-?\d+)", rcon_response)
    return int(match.group(1)) if match else 0


_NUM = r"-?\d+(?:\.\d+)?"


def parse_pos(rcon_response: str) -> tuple[float, float, float] | None:
    match = re.search(rf"\[({_NUM})d?, ({_NUM})d?, ({_NUM})d?\]", rcon_response)
    if not match:
        return None
    return float(match.group(1)), float(match.group(2)), float(match.group(3))


def parse_scalar(rcon_response: str) -> float | None:
    match = re.search(rf"({_NUM})[a-zA-Z]?\s*$", rcon_response.strip())
    return float(match.group(1)) if match else None
