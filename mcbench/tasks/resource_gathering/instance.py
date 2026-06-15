"""The generated instance and deterministic instance generation."""

from __future__ import annotations

import random

from pydantic import BaseModel

from mcbench.tasks.resource_gathering.config_schema import (
    ResourceGatheringTaskConfig,
    ResourceTarget,
)


class TaskInstance(BaseModel):
    """The frozen task all agents in one batch receive."""

    instance_id: str
    seed: int
    world_seed: int
    resource: str
    items: list[str]
    target_count: int
    points: float
    goal: str
    duration_seconds: int
    minecraft_version: str
    world_type: str
    generate_structures: bool
    difficulty: str
    spawn_time: int
    biome: str | None = None

    def to_run_config(self, base_cfg: ResourceGatheringTaskConfig) -> ResourceGatheringTaskConfig:
        # Drop the catalog (the menu) from the per-run config: the instance is
        # already chosen, so the run only needs the single selected target.
        data = base_cfg.model_dump(exclude={"catalog"})
        data.update(
            {
                "id": self.instance_id,
                "seed": self.world_seed,
                "minecraft_version": self.minecraft_version,
                "world_type": self.world_type,
                "generate_structures": self.generate_structures,
                "difficulty": self.difficulty,
                "duration_seconds": self.duration_seconds,
                "spawn_time": self.spawn_time,
                "biome": self.biome,
                "goal": self.goal,
                "resources": [
                    ResourceTarget(
                        item=self.resource,
                        items=self.items,
                        target_count=self.target_count,
                        points=self.points,
                    ).model_dump()
                ],
            }
        )
        return ResourceGatheringTaskConfig.model_validate(data)


def generate_instance(
    base_cfg: ResourceGatheringTaskConfig,
    seed: int,
    instance_id: str | None = None,
) -> TaskInstance:
    catalog = base_cfg.catalog
    if catalog is None:
        raise ValueError("resource-gathering config has no `catalog` section")
    rng = random.Random(seed)
    resource = rng.choice(sorted(catalog.resources))
    entry = catalog.resources[resource]
    target_count = rng.randint(*entry.target_range)
    world_seed = rng.randint(-(2**31), 2**31 - 1)
    display_name = entry.display_name or resource.replace("_", " ")
    instance_id = instance_id or f"resource_{seed}_{resource}_{target_count}"
    goal = (
        f"Before sunset, gather {target_count} {display_name}. "
        "Keep the items in your inventory and finish within 20 blocks of spawn."
    )
    return TaskInstance(
        instance_id=instance_id,
        seed=seed,
        world_seed=world_seed,
        resource=resource,
        items=entry.items,
        target_count=target_count,
        points=entry.points,
        goal=goal,
        duration_seconds=base_cfg.duration_seconds,
        minecraft_version=base_cfg.minecraft_version,
        world_type=base_cfg.world_type,
        generate_structures=base_cfg.generate_structures,
        difficulty=base_cfg.difficulty,
        spawn_time=base_cfg.spawn_time,
        biome=entry.biome,
    )
