"""Pydantic models for catalog entries and the generated challenge."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from .competition import ResourceCompetitionConfig, ResourceTarget

class ResourceCatalogEntry(BaseModel):
    """One logical resource that can be selected for a challenge."""

    items: list[str]
    target_range: tuple[int, int]
    points: float = 100.0
    display_name: str | None = None
    # Optional biome to pin the whole world to (e.g. "minecraft:forest"), so the
    # resource is guaranteed present near spawn instead of depending on the seed.
    # None => normal multi-biome generation.
    biome: str | None = None

    @field_validator("items")
    @classmethod
    def items_must_not_be_empty(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("catalog resource must count at least one item")
        return value

    @field_validator("target_range")
    @classmethod
    def target_range_must_be_valid(cls, value: tuple[int, int]) -> tuple[int, int]:
        lo, hi = value
        if lo <= 0 or hi <= 0 or lo > hi:
            raise ValueError("target_range must be positive and increasing")
        return value


class ResourceCatalog(BaseModel):
    resources: dict[str, ResourceCatalogEntry]

    @field_validator("resources")
    @classmethod
    def resources_must_not_be_empty(
        cls, value: dict[str, ResourceCatalogEntry]
    ) -> dict[str, ResourceCatalogEntry]:
        if not value:
            raise ValueError("resource catalog cannot be empty")
        return value


class GeneratedChallenge(BaseModel):
    """The frozen task all miners in one batch receive."""

    challenge_id: str
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

    def to_competition_config(
        self, base_cfg: ResourceCompetitionConfig
    ) -> ResourceCompetitionConfig:
        data = base_cfg.model_dump()
        data.update(
            {
                "id": self.challenge_id,
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
        return ResourceCompetitionConfig.model_validate(data)


