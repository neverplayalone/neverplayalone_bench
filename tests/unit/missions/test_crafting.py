from __future__ import annotations

from collections import Counter

from npabench.evaluation.run_trace import AgentRunTrace, FinalAgentState, TraceEvent
from npabench.missions.crafting import CraftingMission
from npabench.missions.crafting.config_schema import CraftingMissionConfig, RecipeSpec
from npabench.missions.crafting.prompting import (
    fallback_prompt,
    unfaithful_targets,
)
from npabench.missions.crafting.scoring import distance_from_spawn_3d, score_crafting_run
from npabench.missions.crafting.task import (
    BAND_POINTS,
    BAND_SLOTS,
    BULK_TARGET_THRESHOLD,
    MIN_BULK_TARGETS,
    generate_task,
)
from npabench.missions.registry import get_mission

SEEDS = range(120)


def _mission_and_config() -> tuple[CraftingMission, CraftingMissionConfig]:
    mission = CraftingMission()
    return mission, mission.load_config(mission.default_config_path())


# --- config + catalog ---------------------------------------------------------

def test_crafting_is_registered() -> None:
    assert isinstance(get_mission("crafting"), CraftingMission)


def test_bundled_config_loads() -> None:
    _, config = _mission_and_config()
    assert config.id == "crafting"
    assert config.minecraft_version == "1.21.11"
    assert config.menu is not None
    # Empty inventory and no structures are both load-bearing: bootstrapping from
    # nothing is the mission, and a village would hand over breakable crafting
    # tables, furnaces and looted tools that inventory scoring cannot distinguish
    # from crafted ones.
    assert config.starting_items == []
    assert config.generate_structures is False
    assert config.biomes


def test_catalog_covers_every_band_with_room_to_sample() -> None:
    _, config = _mission_and_config()
    assert config.menu is not None
    for band, slots in BAND_SLOTS.items():
        assert len(config.menu.keys_in_band(band)) >= slots


def test_catalog_entries_are_well_formed() -> None:
    _, config = _mission_and_config()
    assert config.menu is not None
    for key, entry in config.menu.recipes.items():
        assert entry.display_name, f"{key} has no display_name for the prompt"
        assert entry.items == [key]
        lo, hi = entry.target_range
        assert 1 <= lo <= hi
        # Band must agree with the derived cost, since band is what gates the
        # agent and what the sampler stratifies on.
        if entry.band == "C":
            assert entry.cost.smelt_ops > 0
        elif entry.band == "B":
            assert entry.cost.smelt_ops == 0 and entry.cost.cobble > 0
        else:
            assert entry.cost.smelt_ops == 0 and entry.cost.cobble == 0


def test_pure_intermediates_are_not_sampleable() -> None:
    _, config = _mission_and_config()
    assert config.menu is not None
    # Every agent crafts these en route to everything else, so scoring them
    # measures nothing.
    assert "oak_planks" not in config.menu.recipes
    assert "stick" not in config.menu.recipes


# --- task generation ----------------------------------------------------------

def test_generate_task_is_deterministic() -> None:
    _, config = _mission_and_config()
    assert generate_task(config, seed=7) == generate_task(config, seed=7)


def test_every_task_has_the_band_composition_and_sums_to_100() -> None:
    _, config = _mission_and_config()
    for seed in SEEDS:
        task = generate_task(config, seed=seed)
        bands = Counter(target.band for target in task.targets)
        assert bands == Counter(BAND_SLOTS), f"seed {seed} produced {bands}"
        assert len(task.targets) == sum(BAND_SLOTS.values()) == 7
        assert len({target.key for target in task.targets}) == 7
        assert sum(target.points for target in task.targets) == 100.0
        for target in task.targets:
            assert target.points == BAND_POINTS[target.band]


def test_every_task_meets_the_bulk_target_rule() -> None:
    # Score granularity: count-1 targets are binary, so without some
    # bulk-countable targets two agents with the same tier reach identical
    # scores and a tie hands the round to the incumbent champion.
    _, config = _mission_and_config()
    for seed in SEEDS:
        task = generate_task(config, seed=seed)
        bulk = [t for t in task.targets if t.target_count >= BULK_TARGET_THRESHOLD]
        assert len(bulk) >= MIN_BULK_TARGETS, f"seed {seed} produced only {len(bulk)} bulk targets"


def test_bulk_targets_come_from_bands_a_and_b() -> None:
    # Once smelt time is costed, band C items land at counts of 1-3, so they
    # cannot carry the granularity rule.
    _, config = _mission_and_config()
    for seed in SEEDS:
        for target in generate_task(config, seed=seed).targets:
            if target.band == "C":
                assert target.target_count < BULK_TARGET_THRESHOLD


def test_biome_is_rolled_from_the_configured_pool() -> None:
    _, config = _mission_and_config()
    seen = {generate_task(config, seed=seed).biome for seed in SEEDS}
    assert seen <= set(config.biomes)
    assert len(seen) > 1, "biome should vary across seeds"


def test_task_ids_name_the_smelting_targets() -> None:
    _, config = _mission_and_config()
    task = generate_task(config, seed=42)
    smelt_keys = [t.key for t in task.targets if t.band == "C"]
    assert task.task_id.startswith("crafting_42_")
    for key in smelt_keys:
        assert key in task.task_id


def test_build_mission_config_expands_targets_and_pins_biome() -> None:
    mission, base = _mission_and_config()
    task = generate_task(base, seed=3)
    config = mission.build_mission_config(base, task)

    assert config.id == task.task_id
    assert config.seed == task.minecraft_seed
    assert config.biome == task.biome  # drives the single-biome world preset
    assert len(config.recipes) == 7
    assert config.menu is None  # catalog dropped from the built config
    assert config.starting_items == []


# --- scoring ------------------------------------------------------------------

def _config_with_targets() -> CraftingMissionConfig:
    return CraftingMissionConfig(
        id="crafting_test",
        duration_seconds=600,
        recipes=[
            RecipeSpec(item="ladder", items=["ladder"], band="A", target_count=12, points=7.5),
            RecipeSpec(item="chest", items=["chest"], band="A", target_count=2, points=7.5),
            RecipeSpec(item="stone_pickaxe", items=["stone_pickaxe"], band="B", target_count=1, points=12.5),
            RecipeSpec(item="cobblestone_stairs", items=["cobblestone_stairs"], band="B", target_count=8, points=12.5),
            RecipeSpec(item="torch", items=["torch"], band="C", target_count=4, points=20),
            RecipeSpec(item="stone", items=["stone"], band="C", target_count=2, points=20),
            RecipeSpec(item="stone_bricks", items=["stone_bricks"], band="C", target_count=2, points=20),
        ],
    )


_FULL_HAUL = {
    "ladder": 12,
    "chest": 2,
    "stone_pickaxe": 1,
    "cobblestone_stairs": 8,
    "torch": 4,
    "stone": 2,
    "stone_bricks": 2,
}
_NO_SMELTING = {"ladder": 12, "chest": 2, "stone_pickaxe": 1, "cobblestone_stairs": 8}


def _trace(inventory: dict[str, int], position, *, done: bool = True) -> AgentRunTrace:
    trace = AgentRunTrace(
        task_id="crafting_test",
        agent_name="crafter",
        started_at=0.0,
        agent_ready_at=1.0,
        ended_at=300.0,
        final_state=FinalAgentState(inventory=dict(inventory), position=position, health=20.0),
    )
    if done:
        trace.events.append(TraceEvent(kind="done"))
    return trace


def test_full_delivery_at_spawn_scores_100() -> None:
    report = score_crafting_run(
        _config_with_targets(),
        _trace(_FULL_HAUL, (3.0, 64.0, 0.0)),
        final_snapshot={"alive": True, "deaths": 0, "distance_from_spawn": 3.0},
    )
    assert report["max_score"] == 100
    assert report["score"] == 100.0
    assert report["distance_multiplier"] == 1.0


def test_agent_that_cannot_smelt_caps_at_40() -> None:
    # The design's central bet: the furnace is one binary prerequisite gating
    # 60 points, so a perfect non-smelting run stops dead at 40.
    report = score_crafting_run(
        _config_with_targets(),
        _trace(_NO_SMELTING, (3.0, 64.0, 0.0)),
        final_snapshot={"alive": True, "deaths": 0, "distance_from_spawn": 3.0},
    )
    assert report["score"] == 40.0


def test_partial_credit_is_linear_within_a_target() -> None:
    report = score_crafting_run(
        _config_with_targets(),
        _trace({**_FULL_HAUL, "ladder": 6}, (0.0, 64.0, 0.0)),
        final_snapshot={"alive": True, "deaths": 0, "distance_from_spawn": 0.0},
    )
    assert report["score"] == 100.0 - 7.5 / 2


def test_undelivered_haul_is_penalized_by_3d_distance() -> None:
    # Crafted everything but stayed down the hole: not delivered.
    deep = distance_from_spawn_3d((3.0, -20.0, 0.0), (0, 64, 0))
    assert deep is not None and deep > 30
    report = score_crafting_run(
        _config_with_targets(),
        _trace(_FULL_HAUL, (3.0, -20.0, 0.0)),
        final_snapshot={"alive": True, "deaths": 0, "distance_from_spawn": deep},
    )
    assert report["distance_multiplier"] < 1.0
    assert report["score"] < 100.0


def test_distance_from_spawn_3d_includes_vertical() -> None:
    assert distance_from_spawn_3d(None, (0, 64, 0)) is None
    assert distance_from_spawn_3d((3.0, 64.0, 4.0), (0, 64, 0)) == 5.0
    assert distance_from_spawn_3d((0.0, 4.0, 0.0), (0, 64, 0)) == 60.0


def test_report_carries_band_and_not_role() -> None:
    # `role` means "fixed key" in mining; every crafting target is rolled, so
    # reusing the name would make the same field mean different things per
    # mission. `band` is the real structural axis.
    report = score_crafting_run(
        _config_with_targets(),
        _trace(_FULL_HAUL, (0.0, 64.0, 0.0)),
        final_snapshot={"alive": True, "deaths": 0, "distance_from_spawn": 0.0},
    )
    assert report["resources"], "dashboard and API read the `resources` key"
    for entry in report["resources"]:
        assert entry["band"] in {"A", "B", "C"}
        assert "role" not in entry


# --- prompting ----------------------------------------------------------------

def test_fallback_prompt_names_every_target_and_count() -> None:
    _, config = _mission_and_config()
    for seed in (1, 2, 3, 17, 99):
        task = generate_task(config, seed=seed)
        prompt = fallback_prompt(task.targets)
        assert unfaithful_targets(prompt, task.targets) == []
        assert "20 blocks" in prompt


def test_validation_accepts_natural_plurals() -> None:
    _, config = _mission_and_config()
    targets = generate_task(config, seed=5).targets
    prompt = fallback_prompt(targets) + " Ladders and Torches included."
    assert unfaithful_targets(prompt, targets) == []


def test_validation_rejects_a_renamed_item() -> None:
    _, config = _mission_and_config()
    targets = generate_task(config, seed=5).targets
    renamed = fallback_prompt(targets).replace(
        targets[0].display_name or "", "a mysterious contraption"
    )
    assert targets[0].key in unfaithful_targets(renamed, targets)


def test_validation_rejects_a_dropped_count() -> None:
    targets = generate_task(_mission_and_config()[1], seed=8).targets
    prompt = "Craft some things and come back near where you started."
    missing = unfaithful_targets(prompt, targets)
    assert len(missing) == len(targets)


def test_generation_failure_falls_back_instead_of_killing_the_run(tmp_path, monkeypatch) -> None:
    # The other missions raise when OPENROUTER_API_KEY is absent, which makes a
    # missing key fail the whole evaluation. Crafting degrades to the template.
    from npabench.missions.crafting import prompting

    def _boom(task, *, attempt=0):
        raise RuntimeError("no API key")

    monkeypatch.setattr(prompting, "_generate_prompt", _boom)
    _, config = _mission_and_config()
    task = generate_task(config, seed=4)
    materialized = prompting.materialize_task_prompt(task, tmp_path)

    assert unfaithful_targets(materialized.prompt, task.targets) == []
    assert materialized.prompt_metadata is not None
    assert materialized.prompt_metadata.provider == "template"


def test_unfaithful_generation_is_retried_then_falls_back(tmp_path, monkeypatch) -> None:
    from npabench.missions.base import PromptMetadata
    from npabench.missions.crafting import prompting

    attempts: list[int] = []

    def _renames(task, *, attempt=0):
        attempts.append(attempt)
        return "Build a mysterious contraption or two.", PromptMetadata(
            provider="test", model="test", schema_version=prompting.PROMPT_SCHEMA_VERSION
        )

    monkeypatch.setattr(prompting, "_generate_prompt", _renames)
    _, config = _mission_and_config()
    task = generate_task(config, seed=4)
    materialized = prompting.materialize_task_prompt(task, tmp_path)

    assert attempts == [0, 1], "a renamed item should be retried once"
    assert materialized.prompt_metadata is not None
    assert materialized.prompt_metadata.provider == "template"


def test_faithful_generation_is_kept(tmp_path, monkeypatch) -> None:
    from npabench.missions.base import PromptMetadata
    from npabench.missions.crafting import prompting

    _, config = _mission_and_config()
    task = generate_task(config, seed=4)
    good = "Please assemble " + fallback_prompt(task.targets)

    monkeypatch.setattr(
        prompting,
        "_generate_prompt",
        lambda t, *, attempt=0: (
            good,
            PromptMetadata(provider="test", model="m", schema_version=prompting.PROMPT_SCHEMA_VERSION),
        ),
    )
    materialized = prompting.materialize_task_prompt(task, tmp_path)
    assert materialized.prompt == good
    assert materialized.prompt_metadata is not None
    assert materialized.prompt_metadata.provider == "test"


def test_count_matching_uses_word_boundaries() -> None:
    # A request for 1 must not be satisfied by the "1" inside "12".
    config = _mission_and_config()[1]
    task = generate_task(config, seed=11)
    single = next((t for t in task.targets if t.target_count == 1), None)
    if single is None:
        return
    prompt = f"Make 12 of something and {single.display_name} as well."
    assert single.key in unfaithful_targets(prompt, [single])
