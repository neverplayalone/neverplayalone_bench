from __future__ import annotations

import random

from pydantic import Field

from npabench.missions.base import Task, TaskTarget
from npabench.missions.farming.config_schema import (
    CropMenuEntry,
    Difficulty,
    FarmingMissionConfig,
    FarmingTargetSpec,
    HarvestMode,
    PLOT_COLORS,
    PlotTemplate,
)


class FarmingTaskTarget(TaskTarget):
    difficulty: Difficulty
    template: PlotTemplate
    item: str
    starter_item: str
    starter_count: int
    growth_step_seconds: int
    planted_block: str
    mature_block: str
    harvest_mode: HarvestMode
    max_age: int | None = None
    generated_block: str | None = None
    harvest_holder: str
    plot_tag: str


class FarmingTask(Task):
    targets: list[FarmingTaskTarget] = Field(default_factory=list)
    duration_seconds: int
    keep_inventory: bool
    layout_seed: int


def generate_task(
    base_config: FarmingMissionConfig,
    seed: int,
    task_id: str | None = None,
) -> FarmingTask:
    if base_config.menu is None:
        raise ValueError("farming config has no `menu` section")
    rng = random.Random(seed)
    selected: list[tuple[str, CropMenuEntry]] = []
    for difficulty, count in (
        ("easy", base_config.sampling.easy_targets),
        ("medium", base_config.sampling.medium_targets),
    ):
        candidates = [
            (key, entry)
            for key, entry in sorted(base_config.menu.crops.items())
            if entry.difficulty == difficulty
        ]
        selected.extend(rng.sample(candidates, count))

    targets = [
        _target_from_menu(key, entry, rng, index) for index, (key, entry) in enumerate(selected)
    ]
    selected_id = task_id or build_task_id(seed, targets)
    minecraft_seed = random.Random(f"farming-world:{seed}").getrandbits(64)
    layout_seed = random.Random(f"farming-layout:{seed}").getrandbits(64)
    task = FarmingTask(
        task_id=selected_id,
        seed=seed,
        minecraft_seed=minecraft_seed,
        layout_seed=layout_seed,
        targets=targets,
        duration_seconds=base_config.duration_seconds,
        keep_inventory=base_config.keep_inventory,
    )
    return task.model_copy(update={"prompt": build_prompt(task)})


def _target_from_menu(
    key: str,
    entry: CropMenuEntry,
    rng: random.Random,
    index: int,
) -> FarmingTaskTarget:
    return FarmingTaskTarget(
        key=key,
        display_name=entry.display_name,
        items=[entry.item],
        target_count=rng.randint(*entry.target_range),
        role="essential",
        points=entry.points,
        difficulty=entry.difficulty,
        template=entry.template,
        item=entry.item,
        starter_item=entry.starter_item,
        starter_count=entry.starter_count,
        growth_step_seconds=entry.growth_step_seconds,
        planted_block=entry.planted_block,
        mature_block=entry.mature_block,
        harvest_mode=entry.harvest_mode,
        max_age=entry.max_age,
        generated_block=entry.generated_block,
        harvest_holder=f"#farm{index:02d}",
        plot_tag=f"nff_plot_{index:02d}",
    )


def build_task_id(seed: int, targets: list[FarmingTaskTarget]) -> str:
    keys = "_".join(target.key for target in targets)
    return f"farming_{seed}_{keys}"


def build_prompt(task: FarmingTask) -> str:
    target_lines = [
        f"- Harvest {target.target_count} valid {target.display_name} units from the "
        f"{PLOT_COLORS[index]}-bordered plot "
        f"({target.points:g} points, {target.difficulty})."
        for index, target in enumerate(task.targets)
    ]
    minutes = task.duration_seconds // 60
    return "\n".join(
        [
            "You start with an empty inventory in a random world.",
            "A controlled farming hub at spawn contains four crop-specific plots and a "
            "barrel with guaranteed starter materials and tools.",
            f"You have {minutes} minutes. Grow, harvest, and replant the target crops.",
            "Only mature benchmark-grown harvest units removed from the assigned plot score.",
            "Starter items, immature crops, crops outside the plots, and re-placed harvests "
            "do not score.",
            "Targets:",
            *target_lines,
            "Partial progress scores linearly. Emit ready to begin and done when finished.",
        ]
    )


def target_specs(targets: list[FarmingTaskTarget]) -> list[FarmingTargetSpec]:
    return [FarmingTargetSpec.model_validate(target.model_dump()) for target in targets]
