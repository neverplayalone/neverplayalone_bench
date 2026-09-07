from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from npabench.missions.base import MissionConfig

Tier = Literal["easy", "medium", "hard"]
TIER_ORDER: tuple[Tier, ...] = ("easy", "medium", "hard")
MINECRAFT_ID_RE = re.compile(r"^[a-z0-9_.-]+$")


class MobMenuEntry(BaseModel):
    entity_type: str
    display_name: str
    target_range: tuple[int, int]
    difficulty_weight: float = 1.0
    drop_items: list[str] = Field(default_factory=list)
    spawn_reserve_multiplier: float = 1.5

    @field_validator("entity_type")
    @classmethod
    def entity_type_must_be_safe(cls, value: str) -> str:
        if not MINECRAFT_ID_RE.fullmatch(value):
            raise ValueError("entity_type must be an unnamespaced Minecraft identifier")
        return value

    @field_validator("drop_items")
    @classmethod
    def drop_items_must_be_safe(cls, values: list[str]) -> list[str]:
        if any(not MINECRAFT_ID_RE.fullmatch(value) for value in values):
            raise ValueError("drop_items must be unnamespaced Minecraft identifiers")
        return values

    @field_validator("target_range")
    @classmethod
    def target_range_must_be_valid(cls, value: tuple[int, int]) -> tuple[int, int]:
        low, high = value
        if low <= 0 or high < low:
            raise ValueError("target_range must be positive and increasing")
        return value

    @field_validator("difficulty_weight", "spawn_reserve_multiplier")
    @classmethod
    def positive_weights(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("combat weights must be positive")
        return value


class TierMenu(BaseModel):
    mobs: dict[str, MobMenuEntry]

    @field_validator("mobs")
    @classmethod
    def mobs_must_not_be_empty(cls, value: dict[str, MobMenuEntry]) -> dict[str, MobMenuEntry]:
        if not value:
            raise ValueError("combat tier must contain at least one mob")
        if any(not MINECRAFT_ID_RE.fullmatch(key) for key in value):
            raise ValueError("combat mob keys must be safe Minecraft identifiers")
        return value


class TierRule(BaseModel):
    points: float
    target_kinds: int
    target_difficulty_units: float
    count_choice_slack: float = 1.0

    @field_validator("points", "target_difficulty_units")
    @classmethod
    def points_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("tier points and difficulty units must be positive")
        return value

    @field_validator("count_choice_slack")
    @classmethod
    def slack_must_be_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("count_choice_slack must be non-negative")
        return value

    @field_validator("target_kinds")
    @classmethod
    def target_kinds_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("target_kinds must be positive")
        return value


def default_tier_rules() -> dict[Tier, TierRule]:
    return {
        "easy": TierRule(points=20.0, target_kinds=1, target_difficulty_units=7.0),
        "medium": TierRule(points=35.0, target_kinds=2, target_difficulty_units=5.5),
        "hard": TierRule(points=45.0, target_kinds=2, target_difficulty_units=4.0),
    }


class PhaseRules(BaseModel):
    preparation_seconds: int = 480
    combat_seconds: int = 420
    wave_offsets_seconds: list[int] = Field(default_factory=lambda: [0, 90, 180, 270])
    wave_tiers: list[list[Tier]] = Field(
        default_factory=lambda: [
            ["easy"],
            ["easy", "medium"],
            ["medium", "hard"],
            ["hard"],
        ]
    )
    warning_offsets_seconds: list[int] = Field(default_factory=lambda: [60, 10])
    spawn_mobs_naturally: bool = False

    @field_validator("preparation_seconds", "combat_seconds")
    @classmethod
    def phase_duration_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("combat phase durations must be positive")
        return value

    @field_validator("wave_offsets_seconds")
    @classmethod
    def waves_must_fit_combat(cls, values: list[int]) -> list[int]:
        if not values or any(value < 0 for value in values):
            raise ValueError("wave offsets must be non-negative and non-empty")
        if values != sorted(set(values)):
            raise ValueError("wave offsets must be unique and strictly increasing")
        return values

    @field_validator("warning_offsets_seconds")
    @classmethod
    def warnings_must_be_positive(cls, values: list[int]) -> list[int]:
        if any(value <= 0 for value in values):
            raise ValueError("warning offsets must be positive")
        return sorted(set(values), reverse=True)

    @model_validator(mode="after")
    def validate_schedule(self) -> PhaseRules:
        if self.wave_offsets_seconds[-1] >= self.combat_seconds:
            raise ValueError("every wave offset must occur before combat ends")
        if len(self.wave_tiers) != len(self.wave_offsets_seconds):
            raise ValueError("wave_tiers must contain one entry per wave offset")
        if any(not tiers for tiers in self.wave_tiers):
            raise ValueError("every wave must contain at least one target tier")
        missing_tiers = [
            tier for tier in TIER_ORDER if not any(tier in tiers for tiers in self.wave_tiers)
        ]
        if missing_tiers:
            raise ValueError(f"wave schedule is missing tiers: {', '.join(missing_tiers)}")
        if any(value >= self.preparation_seconds for value in self.warning_offsets_seconds):
            raise ValueError("warning offsets must be shorter than preparation_seconds")
        return self


class CombatScoringRules(BaseModel):
    death_penalty_points: float = 10.0

    @field_validator("death_penalty_points")
    @classmethod
    def penalties_must_be_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("combat death penalties must be non-negative")
        return value


class CombatTargetSpec(BaseModel):
    key: str
    entity_type: str
    display_name: str
    tier: Tier
    target_count: int
    difficulty_weight: float
    points: float
    spawn_count: int
    drop_items: list[str] = Field(default_factory=list)
    objective: str

    @field_validator("key", "entity_type")
    @classmethod
    def identifiers_must_be_safe(cls, value: str) -> str:
        if not MINECRAFT_ID_RE.fullmatch(value):
            raise ValueError("combat target identifiers must be safe Minecraft identifiers")
        return value

    @field_validator("objective")
    @classmethod
    def objective_must_be_safe(cls, value: str) -> str:
        if len(value) > 16 or not MINECRAFT_ID_RE.fullmatch(value):
            raise ValueError("combat objective must be a safe identifier of at most 16 characters")
        return value

    @field_validator("drop_items")
    @classmethod
    def target_drops_must_be_safe(cls, values: list[str]) -> list[str]:
        if any(not MINECRAFT_ID_RE.fullmatch(value) for value in values):
            raise ValueError("combat target drops must be safe Minecraft identifiers")
        return values

    @model_validator(mode="after")
    def validate_counts_and_weights(self) -> CombatTargetSpec:
        if self.target_count <= 0 or self.spawn_count < self.target_count:
            raise ValueError("combat target needs positive count and enough controlled spawns")
        if self.difficulty_weight <= 0 or self.points < 0:
            raise ValueError("combat target weight must be positive and points non-negative")
        return self


class WaveSpawn(BaseModel):
    target_key: str
    entity_type: str
    tier: Tier
    count: int
    offsets: list[tuple[int, int]]

    @field_validator("target_key", "entity_type")
    @classmethod
    def wave_identifiers_must_be_safe(cls, value: str) -> str:
        if not MINECRAFT_ID_RE.fullmatch(value):
            raise ValueError("wave identifiers must be safe Minecraft identifiers")
        return value

    @model_validator(mode="after")
    def offsets_match_count(self) -> WaveSpawn:
        if self.count <= 0 or len(self.offsets) != self.count:
            raise ValueError("wave spawn offset count must match mob count")
        return self


class CombatWave(BaseModel):
    index: int
    offset_seconds: int
    spawns: list[WaveSpawn] = Field(default_factory=list)


class CombatMissionConfig(MissionConfig):
    id: str = "combat"
    duration_seconds: int = 900
    difficulty: Literal["easy", "normal", "hard"] = "normal"
    keep_inventory: bool = True
    phase: PhaseRules = Field(default_factory=PhaseRules)
    scoring: CombatScoringRules = Field(default_factory=CombatScoringRules)
    tier_rules: dict[Tier, TierRule] = Field(default_factory=default_tier_rules)
    menu: dict[Tier, TierMenu] | None = None
    targets: list[CombatTargetSpec] = Field(default_factory=list)
    waves: list[CombatWave] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_mission(self) -> CombatMissionConfig:
        missing = [tier for tier in TIER_ORDER if tier not in self.tier_rules]
        if missing:
            raise ValueError(f"missing combat tier rules: {', '.join(missing)}")
        if abs(sum(rule.points for rule in self.tier_rules.values()) - 100.0) > 1e-9:
            raise ValueError("combat tier points must total exactly 100")
        if self.duration_seconds != self.phase.preparation_seconds + self.phase.combat_seconds:
            raise ValueError("duration_seconds must equal preparation plus combat seconds")
        if self.menu is not None:
            for tier in TIER_ORDER:
                if tier not in self.menu:
                    raise ValueError(f"combat menu is missing tier {tier}")
                available = len(self.menu[tier].mobs)
                required = self.tier_rules[tier].target_kinds
                if available < required:
                    raise ValueError(
                        f"combat tier {tier} needs {required} target kinds, found {available}"
                    )
        if self.targets:
            if len({target.key for target in self.targets}) != len(self.targets):
                raise ValueError("combat targets must have unique keys")
            if len({target.objective for target in self.targets}) != len(self.targets):
                raise ValueError("combat targets must have unique objectives")
            for tier in TIER_ORDER:
                selected = [target for target in self.targets if target.tier == tier]
                if len(selected) != self.tier_rules[tier].target_kinds:
                    raise ValueError(f"combat task has the wrong number of {tier} targets")
                selected_points = sum(target.points for target in selected)
                if abs(selected_points - self.tier_rules[tier].points) > 1e-6:
                    raise ValueError(f"combat task {tier} target points do not match tier points")
        return self
