from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from mcbench.missions.base import MissionConfig, StartingItem


class ResourceSpec(BaseModel):
    item: str
    items: list[str] = Field(default_factory=list)
    target_count: int
    points: float = 100.0

    @field_validator("target_count")
    @classmethod
    def target_count_must_be_positive(cls, input_value: int) -> int:
        if input_value <= 0:
            raise ValueError("resource target_count must be positive")
        return input_value


class ScoringRules(BaseModel):
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
        cls, input_value: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        return sorted(input_value, key=lambda band: band[0])


class MenuEntry(BaseModel):
    items: list[str]
    target_range: tuple[int, int]
    points: float = 100.0
    display_name: str | None = None
    biome: str | None = None

    @field_validator("items")
    @classmethod
    def items_must_not_be_empty(cls, input_value: list[str]) -> list[str]:
        if not input_value:
            raise ValueError("catalog resource must count at least one item")
        return input_value

    @field_validator("target_range")
    @classmethod
    def target_range_must_be_valid(
        cls, input_value: tuple[int, int]
    ) -> tuple[int, int]:
        lo, hi = input_value
        if lo <= 0 or hi <= 0 or lo > hi:
            raise ValueError("target_range must be positive and increasing")
        return input_value


class ResourceMenu(BaseModel):
    resources: dict[str, MenuEntry]

    @field_validator("resources")
    @classmethod
    def resources_must_not_be_empty(
        cls, input_value: dict[str, MenuEntry]
    ) -> dict[str, MenuEntry]:
        if not input_value:
            raise ValueError("resource catalog cannot be empty")
        return input_value


class ResourceGatheringMissionConfig(MissionConfig):
    id: str = "resource_gathering"
    prompt: str = "Gather the requested resource before sunset."
    starting_items: list[StartingItem] = Field(default_factory=list)
    scoring: ScoringRules = Field(default_factory=ScoringRules)
    menu: ResourceMenu | None = None
    resources: list[ResourceSpec] = Field(default_factory=list)
