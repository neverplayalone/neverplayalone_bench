import random

from pydantic import Field

from npabench.missions.base import Task

from .config_schema import (
    CROP_ITEMS,
    SPECIES,
    AnimalHusbandryConfig,
    AnimalTarget,
    CropCount,
    CropItem,
)


class AnimalTask(Task):
    generator_version: str = "4.1.0"
    schema_version: int = 5
    starter_crops: dict[CropItem, CropCount]
    targets: list[AnimalTarget] = Field(default_factory=list)


def generate_task(config: AnimalHusbandryConfig, seed: int, task_id: str | None = None):
    starter_crops = {
        item: random.Random(f"animal-crop-counts-v1:{seed}:{item}").randint(1, 4)
        for item in CROP_ITEMS
    }
    chosen = random.Random(f"animal-targets-v1:{seed}").sample(sorted(SPECIES), 2)
    targets = [
        AnimalTarget(
            key=s,
            display_name=s.replace("_", " ").title(),
            target_count=random.Random(f"animal-counts-v1:{seed}:{s}").randint(
                *config.goal_count_ranges[s]
            ),
        )
        for s in chosen
    ]
    goals = " and ".join(
        f"{t.target_count} adult {t.key} offspring" for t in targets
    )
    prompt = f"Raise {goals} within {config.duration_seconds // 60} minutes."
    target_suffix = "_".join(f"{t.key}{t.target_count}" for t in targets)
    return AnimalTask(
        task_id=task_id or f"animal_husbandry_v4_1_{seed}_{target_suffix}",
        seed=seed,
        minecraft_seed=random.Random(f"animal-world-v1:{seed}").getrandbits(64),
        targets=targets,
        starter_crops=starter_crops,
        prompt=prompt,
    )
