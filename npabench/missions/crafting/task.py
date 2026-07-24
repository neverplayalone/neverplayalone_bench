from __future__ import annotations

import random

from pydantic import Field

from npabench.missions.base import Task, TaskTarget
from npabench.missions.crafting.config_schema import (
    Band,
    CraftingMissionConfig,
    MenuEntry,
    RecipeMenu,
)

# Slots per band. Deliberately not proportional to band size: band B holds 12
# items but collapses to about four distinct recipe skills (three cobblestone
# shape variants, six stone tools on one pattern, plus lever/furnace/smoker), so
# sampling it more would mostly re-test the same thing. Band C's 16 items are
# genuinely heterogeneous -- torch (charcoal or mined coal), stone (one smelt),
# smooth_stone (two), chiseled_stone_bricks (cobble -> smelt -> bricks -> slab ->
# chisel, the deepest chain in the catalog) -- so slots spent there buy the most
# coverage of the recipes that actually break a planner.
BAND_SLOTS: dict[Band, int] = {"A": 2, "B": 2, "C": 3}

# 2*7.5 + 2*12.5 + 3*20 = 100. Per-slot points scale ~x1.6 per band, tracking
# what an item of that band costs relative to the one below. Band C carries 60%:
# it is the only band needing a furnace, fuel, and in several cases a multi-stage
# smelt, so an agent that cannot smelt caps at exactly 40.
BAND_POINTS: dict[Band, float] = {"A": 7.5, "B": 12.5, "C": 20.0}

# Score granularity guard. Points/target_count is the step size, so a count-1
# target (furnace, stone tools) is binary -- there is no half furnace. Two agents
# that reach the same tier and miss the same targets would otherwise score
# identically to the decimal, and a tie hands the round to the incumbent
# champion. Requiring some bulk-countable targets keeps the scale sensitive to
# marginal improvement: one more ladder always moves the score.
BULK_TARGET_THRESHOLD = 8
MIN_BULK_TARGETS = 2
# Bulk-capable items live in bands A and B; once smelt time is counted nearly
# every band C item lands at count 1-3. If the catalog ever cannot satisfy the
# rule we take the last sample rather than spin forever.
MAX_SAMPLE_ATTEMPTS = 64


class CraftingTaskTarget(TaskTarget):
    band: Band = "A"


class CraftingTask(Task):
    targets: list[CraftingTaskTarget] = Field(default_factory=list)
    # Rolled per seed and applied via MissionConfig.biome, which switches the
    # server to the single-biome world preset.
    biome: str | None = None


def generate_task(
    base_config: CraftingMissionConfig,
    seed: int,
    task_id: str | None = None,
) -> CraftingTask:
    rng = random.Random(seed)
    targets = resolve_task_targets(base_config, rng)
    biome = rng.choice(base_config.biomes) if base_config.biomes else None
    minecraft_seed = rng.getrandbits(64)
    selected_task_id = task_id or build_task_id(seed, targets)
    return CraftingTask(
        task_id=selected_task_id,
        seed=seed,
        minecraft_seed=minecraft_seed,
        targets=targets,
        biome=biome,
    )


def resolve_task_targets(
    base_config: CraftingMissionConfig,
    rng: random.Random,
) -> list[CraftingTaskTarget]:
    menu = base_config.menu
    if menu is None:
        raise ValueError("crafting config has no `menu` section")
    _assert_menu_is_samplable(menu)

    last_targets: list[CraftingTaskTarget] = []
    for _ in range(MAX_SAMPLE_ATTEMPTS):
        last_targets = _sample_targets(menu, rng)
        if _bulk_target_count(last_targets) >= MIN_BULK_TARGETS:
            return last_targets
    # Catalog cannot meet the granularity rule; a coarser task still scores
    # correctly, so degrade rather than fail the whole evaluation.
    return last_targets


def build_task_id(seed: int, targets: list[CraftingTaskTarget]) -> str:
    # Every target is rolled, so unlike mining there is no fixed/optional split to
    # name the task by. Band C is the most characteristic part of a round.
    smelt_keys = [target.key for target in targets if target.band == "C"]
    suffix = "_".join(smelt_keys) if smelt_keys else "no_smelting"
    return f"crafting_{seed}_{suffix}"


def _assert_menu_is_samplable(menu: RecipeMenu) -> None:
    for band, slots in BAND_SLOTS.items():
        available = len(menu.keys_in_band(band))
        if available < slots:
            raise ValueError(
                f"crafting config needs at least {slots} band {band} recipes, found {available}"
            )


def _sample_targets(
    menu: RecipeMenu,
    rng: random.Random,
) -> list[CraftingTaskTarget]:
    targets: list[CraftingTaskTarget] = []
    for band in ("A", "B", "C"):
        keys = menu.keys_in_band(band)
        for key in rng.sample(keys, BAND_SLOTS[band]):
            targets.append(_build_task_target(key, menu.recipes[key], rng, band=band))
    return targets


def _bulk_target_count(targets: list[CraftingTaskTarget]) -> int:
    return sum(1 for target in targets if target.target_count >= BULK_TARGET_THRESHOLD)


def _build_task_target(
    key: str,
    menu_entry: MenuEntry,
    rng: random.Random,
    *,
    band: Band,
) -> CraftingTaskTarget:
    target_count = rng.randint(*menu_entry.target_range)
    return CraftingTaskTarget(
        key=key,
        display_name=menu_entry.display_name or key.replace("_", " "),
        items=list(menu_entry.items),
        target_count=target_count,
        band=band,
        points=BAND_POINTS[band],
    )
