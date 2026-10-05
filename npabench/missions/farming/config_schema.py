from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from npabench.missions.base import MissionConfig

Difficulty = Literal["easy", "medium"]
PlotTemplate = Literal[
    "farmland",
    "water_edge",
    "cactus",
    "bamboo",
    "sweet_berries",
    "stem",
    "cocoa",
    "kelp",
    "mushroom",
    "glow_berries",
    "tree",
    "nether_wart",
]
HarvestMode = Literal["mature_state", "naturally_generated"]
MINECRAFT_ID_RE = re.compile(r"^[a-z0-9_.-]+$")
BLOCK_STATE_RE = re.compile(r"^[a-z0-9_.-]+(?:\[[a-z0-9_=,.-]+\])?$")


def _safe_identifier(value: str, label: str) -> str:
    if not MINECRAFT_ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be an unnamespaced Minecraft identifier")
    return value


def _valid_range(value: tuple[int, int], label: str) -> tuple[int, int]:
    low, high = value
    if low <= 0 or high < low:
        raise ValueError(f"{label} must be positive and increasing")
    return value


class CropMenuEntry(BaseModel):
    item: str
    starter_item: str
    starter_count: int = Field(gt=0, le=64)
    display_name: str
    difficulty: Difficulty
    template: PlotTemplate
    target_range: tuple[int, int]
    points: float = Field(gt=0)
    planted_block: str
    mature_block: str
    harvest_mode: HarvestMode
    max_age: int | None = Field(default=None, gt=0)
    generated_block: str | None = None

    @field_validator("item", "starter_item", "planted_block", "generated_block")
    @classmethod
    def identifiers_must_be_safe(cls, value: str | None) -> str | None:
        return _safe_identifier(value, "crop identifier") if value else None

    @field_validator("target_range")
    @classmethod
    def range_must_be_valid(cls, value: tuple[int, int]) -> tuple[int, int]:
        return _valid_range(value, "crop target range")

    @field_validator("mature_block")
    @classmethod
    def mature_block_must_be_safe(cls, value: str) -> str:
        if not BLOCK_STATE_RE.fullmatch(value):
            raise ValueError("mature_block must be a safe Minecraft block state")
        return value

    @model_validator(mode="after")
    def validate_growth_fields(self) -> CropMenuEntry:
        if (
            self.template
            in {
                "farmland",
                "sweet_berries",
                "cocoa",
                "nether_wart",
                "stem",
            }
            and self.max_age is None
        ):
            raise ValueError(f"{self.template} crops require max_age")
        if self.harvest_mode == "naturally_generated" and not self.generated_block:
            raise ValueError("naturally generated crops require generated_block")
        expected_points = 20.0 if self.difficulty == "easy" else 30.0
        if self.points != expected_points:
            raise ValueError(
                f"{self.difficulty} crops must be worth exactly {expected_points:g} points"
            )
        return self


class FarmingMenu(BaseModel):
    crops: dict[str, CropMenuEntry]

    @model_validator(mode="after")
    def validate_catalog(self) -> FarmingMenu:
        if not self.crops:
            raise ValueError("farming crop catalog cannot be empty")
        for key in self.crops:
            _safe_identifier(key, "farming crop key")
        return self


class FarmingSamplingRules(BaseModel):
    easy_targets: Literal[2] = 2
    medium_targets: Literal[2] = 2


class FarmingEnvironmentRules(BaseModel):
    random_tick_speed: int = Field(default=3, ge=1, le=100)
    runtime_poll_seconds: float = Field(default=1.0, gt=0)
    utility_log_count: int = Field(default=8, gt=0, le=64)


class FarmingTargetSpec(BaseModel):
    key: str
    display_name: str
    difficulty: Difficulty
    template: PlotTemplate
    target_count: int = Field(gt=0)
    points: float = Field(gt=0)
    item: str
    starter_item: str
    starter_count: int = Field(gt=0, le=64)
    planted_block: str
    mature_block: str
    harvest_mode: HarvestMode
    max_age: int | None = Field(default=None, gt=0)
    generated_block: str | None = None
    harvest_holder: str
    tracking_tag: str

    @field_validator("key", "item", "starter_item", "planted_block", "generated_block")
    @classmethod
    def identifiers_must_be_safe(cls, value: str | None) -> str | None:
        return _safe_identifier(value, "farming target identifier") if value else None

    @field_validator("harvest_holder")
    @classmethod
    def holder_must_be_safe(cls, value: str) -> str:
        if not re.fullmatch(r"#[a-z0-9_.-]{1,39}", value):
            raise ValueError("farming harvest holders must be safe fake-player names")
        return value

    @field_validator("tracking_tag")
    @classmethod
    def tracking_tag_must_be_safe(cls, value: str) -> str:
        return _safe_identifier(value, "farming tracking tag")

    @field_validator("mature_block")
    @classmethod
    def mature_block_must_be_safe(cls, value: str) -> str:
        if not BLOCK_STATE_RE.fullmatch(value):
            raise ValueError("mature_block must be a safe Minecraft block state")
        return value

    @model_validator(mode="after")
    def validate_target(self) -> FarmingTargetSpec:
        if self.harvest_mode == "naturally_generated" and not self.generated_block:
            raise ValueError("naturally generated targets require generated_block")
        return self


class FarmingMissionConfig(MissionConfig):
    id: str = "farming"
    duration_seconds: int = 1200
    difficulty: Literal["peaceful"] = "peaceful"
    keep_inventory: bool = True
    sampling: FarmingSamplingRules = Field(default_factory=FarmingSamplingRules)
    environment: FarmingEnvironmentRules = Field(default_factory=FarmingEnvironmentRules)
    menu: FarmingMenu | None = None
    targets: list[FarmingTargetSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_mission(self) -> FarmingMissionConfig:
        expected = {
            "easy": self.sampling.easy_targets,
            "medium": self.sampling.medium_targets,
        }
        if self.menu is not None:
            available = {
                difficulty: sum(crop.difficulty == difficulty for crop in self.menu.crops.values())
                for difficulty in expected
            }
            for difficulty, count in expected.items():
                if available[difficulty] < count:
                    raise ValueError(
                        f"farming catalog has too few {difficulty} crops: "
                        f"need {count}, got {available[difficulty]}"
                    )
        if self.targets:
            if len({target.key for target in self.targets}) != len(self.targets):
                raise ValueError("farming targets must have unique keys")
            if len({target.harvest_holder for target in self.targets}) != len(self.targets):
                raise ValueError("farming targets must have unique harvest holders")
            if len({target.tracking_tag for target in self.targets}) != len(self.targets):
                raise ValueError("farming targets must have unique tracking tags")
            actual = {
                difficulty: sum(target.difficulty == difficulty for target in self.targets)
                for difficulty in expected
            }
            if actual != expected:
                raise ValueError(f"farming target shape must be {expected}, got {actual}")
            if abs(sum(target.points for target in self.targets) - 100.0) > 1e-9:
                raise ValueError("farming task points must total exactly 100")
        return self
