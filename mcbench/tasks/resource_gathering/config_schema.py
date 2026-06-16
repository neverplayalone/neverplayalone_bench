"""Configuration schema for the resource-gathering task.

The default values are loaded from the bundled ``configs/default.yaml``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from mcbench.core.task import KitItem, RunConfig


class ResourceTarget(BaseModel):
    item: str
    items: list[str] = Field(default_factory=list)
    target_count: int
    points: float = 100.0

    @field_validator("target_count")
    @classmethod
    def target_count_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("resource target_count must be positive")
        return value


class TaskScoringConfig(BaseModel):
    # Final score = resource_score * distance_multiplier. distance_bands maps an
    # upper distance bound (blocks from spawn) to the multiplier applied at or
    # below it, evaluated low-to-high; beyond the last band the multiplier is
    # distance_floor_mult. Time-to-finish is not scored; it only breaks ties
    # between equal scores (see `time_efficiency` in the report).
    distance_bands: list[tuple[float, float]] = Field(
        default_factory=lambda: [
            (10.0, 1.00),
            (30.0, 0.90),
            (100.0, 0.75),
            (250.0, 0.60),
            (500.0, 0.50),
            (1000.0, 0.40),
            (2000.0, 0.30),
        ]
    )
    distance_floor_mult: float = 0.20

    @field_validator("distance_bands")
    @classmethod
    def bands_sorted_ascending(
        cls, value: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        return sorted(value, key=lambda band: band[0])


class ResourceCatalogEntry(BaseModel):
    """One logical resource that can be selected for a instance."""

    items: list[str]
    target_range: tuple[int, int]
    points: float = 100.0
    display_name: str | None = None
    # Optional biome to pin the whole world to (e.g. "minecraft:forest"), so the
    # resource is guaranteed present near spawn instead of depending on the seed.
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
    """The menu of resource targets; the seed picks one entry to form the instance."""

    resources: dict[str, ResourceCatalogEntry]

    @field_validator("resources")
    @classmethod
    def resources_must_not_be_empty(
        cls, value: dict[str, ResourceCatalogEntry]
    ) -> dict[str, ResourceCatalogEntry]:
        if not value:
            raise ValueError("resource catalog cannot be empty")
        return value


class ResourceGatheringTaskConfig(RunConfig):
    """Shared RunConfig + the resource-gathering kit, scoring, catalog, and target."""

    id: str = "resource_gathering_v1"
    goal: str = "Gather the requested resource before sunset."
    kit: list[KitItem] = Field(default_factory=list)
    scoring: TaskScoringConfig = Field(default_factory=TaskScoringConfig)
    # The task menu (present in the bundled config; dropped from the per-run config
    # once a single instance has been selected).
    catalog: ResourceCatalog | None = None
    # The selected target for one instance (empty in the base config; filled in by
    # TaskInstance.to_run_config).
    resources: list[ResourceTarget] = Field(default_factory=list)
