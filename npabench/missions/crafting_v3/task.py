from __future__ import annotations

import random

from pydantic import Field

from npabench.missions.base import Task, TaskTarget
from npabench.missions.crafting_v3.config_schema import (
    Band,
    CraftingV3MissionConfig,
    MenuEntry,
    RecipeMenu,
    SamplingRules,
)

# Highest tier last, so build_task_id can name the iron targets (the most
# characteristic part of a round) rather than the basic ones.
_BAND_ORDER = ["A", "B", "C"]


class CraftingV3TaskTarget(TaskTarget):
    band: Band = "A"


class CraftingV3Task(Task):
    targets: list[CraftingV3TaskTarget] = Field(default_factory=list)
    # Rolled per seed and applied via MissionConfig.biome (single-biome preset).
    biome: str | None = None


def generate_task(
    base_config: CraftingV3MissionConfig,
    seed: int,
    task_id: str | None = None,
) -> CraftingV3Task:
    rng = random.Random(seed)
    targets = resolve_task_targets(base_config, rng)
    biome = rng.choice(base_config.biomes) if base_config.biomes else None
    minecraft_seed = rng.getrandbits(64)
    selected_task_id = task_id or build_task_id(base_config.id, seed, targets)
    return CraftingV3Task(
        task_id=selected_task_id,
        seed=seed,
        minecraft_seed=minecraft_seed,
        targets=targets,
        biome=biome,
    )


def resolve_task_targets(
    base_config: CraftingV3MissionConfig,
    rng: random.Random,
) -> list[CraftingV3TaskTarget]:
    menu = base_config.menu
    if menu is None:
        raise ValueError("crafting_v3 config has no `menu` section")
    sampling = base_config.sampling
    _assert_menu_is_samplable(menu, sampling)

    last_targets: list[CraftingV3TaskTarget] = []
    for _ in range(sampling.max_sample_attempts):
        last_targets = _sample_targets(menu, sampling, rng)
        if _bulk_target_count(last_targets, sampling.bulk_threshold) >= sampling.min_bulk_targets:
            return last_targets
    # Catalog cannot meet the granularity rule; a coarser task still scores
    # correctly, so degrade rather than fail the whole evaluation.
    return last_targets


def build_task_id(id_prefix: str, seed: int, targets: list[CraftingV3TaskTarget]) -> str:
    present = {target.band for target in targets}
    top_band = next((band for band in reversed(_BAND_ORDER) if band in present), None)
    top_keys = [target.key for target in targets if target.band == top_band] if top_band else []
    suffix = "_".join(top_keys) if top_keys else "basic"
    return f"{id_prefix}_{seed}_{suffix}"


def _assert_menu_is_samplable(menu: RecipeMenu, sampling: SamplingRules) -> None:
    for band, rule in sampling.bands.items():
        available = len(menu.keys_in_band(band))
        if available < rule.slots:
            raise ValueError(
                f"crafting_v3 config needs at least {rule.slots} band {band} recipes, "
                f"found {available}"
            )


def _sample_targets(
    menu: RecipeMenu,
    sampling: SamplingRules,
    rng: random.Random,
) -> list[CraftingV3TaskTarget]:
    targets: list[CraftingV3TaskTarget] = []
    for band in sorted(sampling.bands):
        rule = sampling.bands[band]
        keys = menu.keys_in_band(band)
        for key in rng.sample(keys, rule.slots):
            targets.append(
                _build_task_target(key, menu.recipes[key], rng, band=band, points=rule.points)
            )
    return targets


def _bulk_target_count(targets: list[CraftingV3TaskTarget], threshold: int) -> int:
    return sum(1 for target in targets if target.target_count >= threshold)


def _build_task_target(
    key: str,
    menu_entry: MenuEntry,
    rng: random.Random,
    *,
    band: str,
    points: float,
) -> CraftingV3TaskTarget:
    target_count = rng.randint(*menu_entry.target_range)
    return CraftingV3TaskTarget(
        key=key,
        display_name=menu_entry.display_name or key.replace("_", " "),
        items=list(menu_entry.items),
        target_count=target_count,
        band=band,
        points=points,
    )
