from typing import Annotated, Literal

from pydantic import Field, model_validator

from npabench.missions.base import MissionConfig, TaskTarget

SPECIES = ("cow", "sheep", "pig", "chicken", "rabbit")
Species = Literal["cow", "sheep", "pig", "chicken", "rabbit"]
GoalCount = Annotated[int, Field(strict=True, ge=2, le=4)]

CROP_ITEMS = ("wheat_seeds", "carrot", "potato", "beetroot_seeds")
CropItem = Literal["wheat_seeds", "carrot", "potato", "beetroot_seeds"]
CropCount = Annotated[int, Field(strict=True, ge=1, le=4)]
FENCE_SUPPLIES = {"oak_fence": 128, "oak_fence_gate": 8}


def supply_kit(config):
    if set(config.starter_crops) != set(CROP_ITEMS):
        raise ValueError("generate and resolve task starter_crops before setting up supplies")
    return {**FENCE_SUPPLIES, **config.starter_crops}


class AnimalTarget(TaskTarget):
    key: Species
    points: Literal[50.0] = 50.0
    target_count: int = Field(default=2, ge=2, le=4)
    role: Literal["essential"] = "essential"


class AnimalHusbandryConfig(MissionConfig):
    id: str = "animal_husbandry"
    duration_seconds: Literal[1200] = 1200
    difficulty: Literal["peaceful"] = "peaceful"
    biome: None = None
    goal_count_ranges: dict[Species, tuple[GoalCount, GoalCount]] = Field(
        default_factory=lambda: {species: (2, 4) for species in SPECIES}
    )
    starter_crops: dict[CropItem, CropCount] = Field(default_factory=dict)
    starter_babies_per_species: Literal[2] = 2
    min_farm_size: int = Field(default=20, ge=20, le=64)
    max_farm_size: int = Field(default=64, ge=20, le=64)
    targets: list[AnimalTarget] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def reject_fixed_count(cls, value):
        if isinstance(value, dict) and "target_count" in value:
            raise ValueError("replace fixed target_count with per-species goal_count_ranges")
        return value

    @model_validator(mode="after")
    def validate_targets(self):
        if self.starter_crops and set(self.starter_crops) != set(CROP_ITEMS):
            raise ValueError("starter_crops must include all four crop items")
        if self.max_farm_size < self.min_farm_size:
            raise ValueError("max_farm_size must be at least min_farm_size")
        if set(self.goal_count_ranges) != set(SPECIES):
            raise ValueError("goal_count_ranges must cover all five crop-fed species")
        if any(low > high for low, high in self.goal_count_ranges.values()):
            raise ValueError("goal count ranges must be increasing (min <= max)")
        if self.targets and (len(self.targets) != 2 or len({t.key for t in self.targets}) != 2):
            raise ValueError("animal husbandry requires exactly two distinct species")
        return self
