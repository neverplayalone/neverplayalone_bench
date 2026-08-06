from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from npabench.missions.base import MissionConfig, StartingItem

# crafting_v2 is a separate module from v1 on purpose: it starts the agent with a
# stone-tool kit, restructures the tiers, and reaches into iron, so "v1 with a
# different config" no longer describes it. Only the stable, band-agnostic
# scoring/inventory helpers are shared with v1 (imported in mission.py).
#
# The three bands are the real capability ladder once stone tools are provided
# (cobblestone is then free, so v1's "wooden pickaxe" gate disappears):
#   A -- Basic:    crafting table only (wood + cobblestone items)
#   B -- Smelting: a placed furnace + fuel (the stone family, charcoal, torch)
#   C -- Iron:     mine iron ore, smelt it (a second smelting stage), craft iron
#                  tools and functional items -- the mission's focus
# Letters keep their natural order (A < B < C) so "the highest band present" is
# always the iron one; see task.build_task_id.
Band = Literal["A", "B", "C"]


class RecipeCost(BaseModel):
    """Minimum raw materials per unit."""

    logs: float = 0.0
    cobble: float = 0.0
    smelt_ops: float = 0.0
    # Iron ingots per unit; non-zero only for band C (iron). Catalog metadata.
    iron: float = 0.0


class RecipeSpec(BaseModel):
    """One resolved target for a run.

    No ``role`` field, for the same reason as v1: crafting samples all of its
    targets, so there is no fixed/optional split, and ``band`` already carries
    the structural axis (which tier gates the item)."""

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


class BandRule(BaseModel):
    """How many targets a band contributes and what each is worth. A band's total
    weight is ``slots * points``; bands are weighted by slot count so the sampler
    and the score share one knob."""

    slots: int
    points: float

    @field_validator("slots")
    @classmethod
    def slots_must_be_positive(cls, input_value: int) -> int:
        if input_value <= 0:
            raise ValueError("band slots must be positive")
        return input_value


def _default_band_rules() -> dict[str, BandRule]:
    # 2*7 + 2*10 + 3*22 = 100. Basic+Smelting = 34 (the pre-iron ceiling); iron
    # carries 66, so an agent that reaches stone tier but never produces iron
    # caps at 34. Two Basic slots keep the bulk-target granularity rule easy.
    return {
        "A": BandRule(slots=2, points=7.0),
        "B": BandRule(slots=2, points=10.0),
        "C": BandRule(slots=3, points=22.0),
    }


class SamplingRules(BaseModel):
    bands: dict[str, BandRule] = Field(default_factory=_default_band_rules)
    # Score granularity guard: count-1 targets are binary, so require some
    # bulk-countable targets. Iron items are mostly count 1-3, so the rule leans
    # on the Basic band (and the two bulk iron items, bars and rail).
    bulk_threshold: int = 8
    min_bulk_targets: int = 2
    max_sample_attempts: int = 64

    @field_validator("bands")
    @classmethod
    def bands_must_not_be_empty(cls, input_value: dict[str, BandRule]) -> dict[str, BandRule]:
        if not input_value:
            raise ValueError("sampling needs at least one band")
        return input_value


class ScoringRules(BaseModel):
    # 3D distance return multiplier, as in v1/mining: the target list is a
    # delivery manifest, so an agent still down the mine has not handed anything
    # over.
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


class CraftingV2MissionConfig(MissionConfig):
    id: str = "crafting_v2"
    prompt: str = "Craft the requested items and return to spawn holding all of them."
    # Given kit: stone pickaxes + axes (so the run is spent on the iron tier, not
    # the wood->stone bootstrap v1 already measures) AND a stack of logs (so wood,
    # the root of the tech tree, is available in ANY biome). Whatever is given
    # here must NOT also appear in the catalog, or it would be free points -- see
    # configs/iron.yaml, where the stone tools and the pure-wood items are both
    # excluded.
    starting_items: list[StartingItem] = Field(default_factory=list)
    # Empty on purpose: because logs are given, a treeless spawn is no longer an
    # unrecoverable zero, so v2 drops v1's tree-dense biome pin. An empty pool
    # leaves biome None -> a normal, varied overworld the agent can spawn anywhere
    # in. Terrain and spawn still vary deterministically with the world seed.
    biomes: list[str] = Field(default_factory=list)
    scoring: ScoringRules = Field(default_factory=ScoringRules)
    sampling: SamplingRules = Field(default_factory=SamplingRules)
    menu: RecipeMenu | None = None
    recipes: list[RecipeSpec] = Field(default_factory=list)
