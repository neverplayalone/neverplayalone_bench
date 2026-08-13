from __future__ import annotations

import json
import re

from npabench.evaluation.craft_log import CraftAnnouncer, _targets


class FakeRcon:
    """Answers count_item's `clear <player> minecraft:<item> 0` dry run from a
    dict and records commands."""

    def __init__(self, inventory: dict[str, int]) -> None:
        self.inventory = inventory
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        match = re.match(r"clear \S+ minecraft:(\S+) 0", command)
        if match:
            count = self.inventory.get(match.group(1), 0)
            return f"Found {count} matching items" if count else "No items were found"
        return ""


class _Cfg:
    def __init__(self, recipes=None, resources=None):
        self.recipes = recipes or []
        self.resources = resources or []


class _Spec:
    def __init__(self, item, items, display_name, target_count):
        self.item = item
        self.items = items
        self.display_name = display_name
        self.target_count = target_count


def _announcer(cfg):
    return CraftAnnouncer(
        host="h", rcon_port=1, rcon_password="p", username="agent", mission_config=cfg
    )


def test_targets_reads_recipes_then_resources() -> None:
    crafting = _Cfg(recipes=[_Spec("iron_pickaxe", ["iron_pickaxe"], "Iron Pickaxe", 2)])
    assert _targets(crafting) == [("Iron Pickaxe", ["iron_pickaxe"], 2)]
    mining = _Cfg(resources=[_Spec("iron", ["raw_iron", "iron_ingot"], "iron", 10)])
    assert _targets(mining) == [("iron", ["raw_iron", "iron_ingot"], 10)]


def test_announces_only_when_a_target_count_goes_up() -> None:
    ann = _announcer(_Cfg(recipes=[_Spec("iron_pickaxe", ["iron_pickaxe"], "Iron Pickaxe", 2)]))
    last: dict[str, int] = {}

    def tick(rcon):
        # mirror one poll iteration of _run without the thread/session
        for label, items, target_count in ann.targets:
            have = sum(_count(rcon, i) for i in items)
            if have > last.get(label, 0):
                ann._announce(rcon, label, min(have, target_count), target_count)
            last[label] = have

    def _count(rcon, item):
        from npabench.minecraft.rcon_helpers import count_item

        return count_item(rcon, "agent", item)

    # baseline: nothing crafted -> no message
    r0 = FakeRcon({"iron_pickaxe": 0})
    tick(r0)
    assert not [c for c in r0.commands if c.startswith("tellraw")]
    # crafted one -> exactly one system message
    r1 = FakeRcon({"iron_pickaxe": 1})
    tick(r1)
    tells = [c for c in r1.commands if c.startswith("tellraw @a ")]
    assert len(tells) == 1
    msg = json.loads(tells[0][len("tellraw @a "):])
    assert msg["text"] == "crafted Iron Pickaxe (1/2)"
    # no further change -> silent
    r2 = FakeRcon({"iron_pickaxe": 1})
    tick(r2)
    assert not [c for c in r2.commands if c.startswith("tellraw")]


def test_grouped_items_are_summed_and_capped() -> None:
    ann = _announcer(_Cfg(recipes=[_Spec("any_slab", ["oak_slab", "spruce_slab"], "Wooden Slab", 20)]))
    rcon = FakeRcon({"oak_slab": 24, "spruce_slab": 4})
    ann._announce(rcon, "Wooden Slab", min(28, 20), 20)
    tell = next(c for c in rcon.commands if c.startswith("tellraw @a "))
    assert "crafted Wooden Slab (20/20)" in tell
