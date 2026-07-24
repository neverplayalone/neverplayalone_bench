from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from npabench.missions.base import MissionConfig, StartingItem

# Station prerequisite, which is what actually gates an agent:
#   A -- crafting table only
#   B -- requires a wooden pickaxe (to obtain cobblestone)
#   C -- requires a placed furnace (smelting; a fixed 10s per operation)
# Raw material cost alone mis-ranks band C badly: a 0.5-cobble item can still sit
# behind a furnace, 8 cobblestone and fuel.
Band = Literal["A", "B", "C"]


class RecipeCost(BaseModel):
    """Minimum raw materials per unit, as derived by tools/catalog."""

    logs: float = 0.0
    cobble: float = 0.0
    smelt_ops: float = 0.0


class RecipeSpec(BaseModel):
    """One resolved target for a run.

    Deliberately has no ``role`` field. In mining/resource_gathering
    ``role="essential"`` means *fixed key* -- coal, iron and gold appear every
    round while the others are rolled. Crafting samples all of its targets, so
    that distinction does not exist here, and mapping role onto bands would just
    be a lossy re-encoding of ``band`` under a name that means something else in
    the other two missions.
    """

    item: str
    items: list[str] = Field(default_factory=list)
    display_name: str | None = None
    band: Band = "A"
    target_count: int
    points: float = 0.0

    @field_validator("target_count")
    @classmethod
    def target_count_must_be_positive(cls, input_value: int) -> int:
        if input_value <= 0:
            raise ValueError("recipe target_count must be positive")
        return input_value


class ScoringRules(BaseModel):
    # Reused from mining, and measured in 3D for the same reason: the target list
    # is a delivery manifest, so an agent standing 15 blocks underground beneath
    # spawn has not handed anything over.
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
    band: Band
    cost: RecipeCost = Field(default_factory=RecipeCost)
    display_name: str | None = None

    @field_validator("items")
    @classmethod
    def items_must_not_be_empty(cls, input_value: list[str]) -> list[str]:
        if not input_value:
            raise ValueError("catalog recipe must count at least one item")
        return input_value

    @field_validator("target_range")
    @classmethod
    def target_range_must_be_valid(cls, input_value: tuple[int, int]) -> tuple[int, int]:
        lo, hi = input_value
        if lo <= 0 or hi <= 0 or lo > hi:
            raise ValueError("target_range must be positive and increasing")
        return input_value


class RecipeMenu(BaseModel):
    recipes: dict[str, MenuEntry]

    @field_validator("recipes")
    @classmethod
    def recipes_must_not_be_empty(
        cls, input_value: dict[str, MenuEntry]
    ) -> dict[str, MenuEntry]:
        if not input_value:
            raise ValueError("recipe catalog cannot be empty")
        return input_value

    def keys_in_band(self, band: Band) -> list[str]:
        return sorted(key for key, entry in self.recipes.items() if entry.band == band)


class CraftingMissionConfig(MissionConfig):
    id: str = "crafting"
    prompt: str = "Craft the requested items and return to spawn holding all of them."
    # Empty by default and by design: bootstrapping the tech tree from bare hands
    # is the mission.
    starting_items: list[StartingItem] = Field(default_factory=list)
    # Pool the per-seed biome is rolled from. From an empty inventory a treeless
    # spawn would be an unrecoverable zero, so this is pinned to tree-dense
    # temperate biomes; terrain and spawn position still vary with the world seed.
    biomes: list[str] = Field(default_factory=list)
    scoring: ScoringRules = Field(default_factory=ScoringRules)
    menu: RecipeMenu | None = None
    recipes: list[RecipeSpec] = Field(default_factory=list)
