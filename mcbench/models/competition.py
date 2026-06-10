"""Pydantic models for the resource-gathering run configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

USERNAME = "BenchmarkBot"
class KitItem(BaseModel):
    item: str
    count: int = 1
    enchantments: list[str] = Field(default_factory=list)
    slot: str | None = None

    @field_validator("count")
    @classmethod
    def count_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("kit item count must be positive")
        return value


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


class CompetitionScoringConfig(BaseModel):
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


class ResourceCompetitionConfig(BaseModel):
    id: str = "resource_gathering_v1"
    seed: int = 0
    minecraft_version: str = "1.21.11"
    world_type: str = "normal"
    # When set (e.g. "minecraft:forest"), the world is pinned to this single biome
    # via a generated world-preset datapack; None => normal multi-biome generation.
    biome: str | None = None
    # Side length (blocks) of the square worldborder centered on spawn. Bounds the
    # playable arena; the world is otherwise effectively infinite.
    world_size: int = 5000
    generate_structures: bool = True
    difficulty: Literal["peaceful", "easy", "normal", "hard"] = "peaceful"
    memory: str = "2G"
    duration_seconds: int = 1200
    spawn_time: int = 0
    username: str = USERNAME
    goal: str = "Gather the requested resource before sunset."
    kit: list[KitItem] = Field(default_factory=list)
    resources: list[ResourceTarget] = Field(default_factory=list)
    scoring: CompetitionScoringConfig = Field(default_factory=CompetitionScoringConfig)

    @field_validator("duration_seconds")
    @classmethod
    def duration_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("duration_seconds must be positive")
        return value


