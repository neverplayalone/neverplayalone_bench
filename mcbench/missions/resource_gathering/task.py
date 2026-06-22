from __future__ import annotations

import random

from mcbench.missions.base import Task
from mcbench.missions.resource_gathering.config_schema import (
    MenuEntry,
    ResourceGatheringMissionConfig,
)


def generate_task(
    base_config: ResourceGatheringMissionConfig,
    seed: int,
    task_id: str | None = None,
) -> Task:
    rng = random.Random(seed)
    resource_name, menu_entry, target_count = select_resource_assignment(base_config, rng)
    minecraft_seed = rng.getrandbits(64)
    selected_task_id = task_id or f"resource_{seed}_{resource_name}_{target_count}"
    return Task(
        task_id=selected_task_id,
        seed=seed,
        minecraft_seed=minecraft_seed,
        prompt=build_prompt(resource_name, menu_entry, target_count),
    )


def resolve_resource_assignment(
    base_config: ResourceGatheringMissionConfig,
    seed: int,
) -> tuple[str, MenuEntry, int]:
    return select_resource_assignment(base_config, random.Random(seed))


def select_resource_assignment(
    base_config: ResourceGatheringMissionConfig,
    rng: random.Random,
) -> tuple[str, MenuEntry, int]:
    menu = base_config.menu
    if menu is None:
        raise ValueError("resource-gathering config has no `menu` section")
    resource_name = rng.choice(sorted(menu.resources))
    menu_entry = menu.resources[resource_name]
    target_count = rng.randint(*menu_entry.target_range)
    return resource_name, menu_entry, target_count


def build_prompt(
    resource_name: str,
    menu_entry: MenuEntry,
    target_count: int,
) -> str:
    display_name = menu_entry.display_name or resource_name.replace("_", " ")
    return (
        f"Before sunset, gather {target_count} {display_name}. "
        "Keep the items in your inventory and finish within 20 blocks of spawn."
    )
