from __future__ import annotations

from collections import Counter

from npabench.missions.crafting_v3 import CraftingV3Mission
from npabench.missions.crafting_v3.config_schema import CraftingV3MissionConfig
from npabench.missions.crafting_v3.environment import setup_crafting_v3_agent
from npabench.missions.crafting_v3.prompting import (
    PROMPT_SCHEMA_VERSION,
    fallback_prompt,
    unfaithful_targets,
)
from npabench.missions.crafting_v3.task import generate_task
from npabench.missions.registry import get_mission

SEEDS = range(200)

# The v2 band table lives in the config; tests read the expected shape from the
# loaded config rather than a module constant. A=Basic, B=Smelting, C=Iron.
EXPECTED_BANDS = {"A": 2, "B": 2, "C": 3}
GIVEN_TOOLS = {"iron_pickaxe", "iron_axe"}
GIVEN_ITEMS = {"iron_pickaxe", "iron_axe", "oak_log"}


class FakeRcon:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        if command.endswith("Pos"):
            return "npabench_agent has the following entity data: [0.5d, 72.0d, 0.5d]"
        return ""


def _mission_and_config() -> tuple[CraftingV3Mission, CraftingV3MissionConfig]:
    mission = CraftingV3Mission()
    return mission, mission.load_config(mission.default_config_path())


# --- registration + config ----------------------------------------------------

def test_crafting_v3_is_registered_and_distinct_from_v1() -> None:
    assert isinstance(get_mission("crafting_v3"), CraftingV3Mission)
    assert get_mission("crafting_v1").id == "crafting_v1"
    assert get_mission("crafting_v3").id == "crafting_v3"


def test_iron_config_loads_with_the_v2_shape() -> None:
    _, config = _mission_and_config()
    assert config.id == "crafting_v3"
    assert config.minecraft_version == "1.21.11"
    assert config.duration_seconds == 900
    # Structures OFF even though iron is needed: ore is terrain, not structures.
    assert config.generate_structures is False
    # Biome unpinned: because logs are given, v2 runs in all world types.
    assert config.biomes == []
    assert config.menu is not None


def test_given_items_are_not_also_scoring_targets() -> None:
    # Anything handed to the agent (stone tools + logs) must be excluded from the
    # catalog, or it is free points.
    _, config = _mission_and_config()
    assert config.menu is not None
    given = {item.item for item in config.starting_items}
    assert given == GIVEN_ITEMS
    counts = {item.item: item.count for item in config.starting_items}
    assert counts["iron_pickaxe"] == 3 and counts["iron_axe"] == 3  # durability non-issue
    for key, entry in config.menu.recipes.items():
        assert key not in GIVEN_ITEMS
        assert not (set(entry.items) & GIVEN_ITEMS), f"{key} counts a given item"


def test_wood_items_are_not_scoring_targets() -> None:
    # Logs are given, so pure-wood items would be trivial one-step crafts; they
    # are not targets, which is also what keeps the given logs from being free
    # points. Band A is cobblestone + basic stone tools instead.
    _, config = _mission_and_config()
    assert config.menu is not None
    for removed in ("any_slab", "any_door", "any_stairs", "crafting_table", "chest", "ladder", "barrel"):
        assert removed not in config.menu.recipes, f"{removed} should not be a v2 target"
    assert not any(key.startswith("any_") for key in config.menu.recipes)


def test_sampling_bands_sum_to_100_with_iron_carrying_the_capstone() -> None:
    _, config = _mission_and_config()
    bands = config.sampling.bands
    assert {band: rule.slots for band, rule in bands.items()} == EXPECTED_BANDS
    total = sum(rule.slots * rule.points for rule in bands.values())
    assert total == 100.0
    pre_iron = sum(bands[b].slots * bands[b].points for b in ("A", "B"))
    assert pre_iron == 34.0  # the pre-iron ceiling
    assert bands["C"].slots * bands["C"].points == 66.0  # the iron capstone


def test_catalog_covers_every_band_with_room_to_sample() -> None:
    _, config = _mission_and_config()
    assert config.menu is not None
    for band, rule in config.sampling.bands.items():
        assert len(config.menu.keys_in_band(band)) >= rule.slots


def test_bands_are_the_stone_iron_diamond_ascent() -> None:
    # A Basic (cobble, no smelt/iron/diamond) -> B Iron (mine+smelt iron) ->
    # C Diamond (mined + crafted directly, no smelting).
    _, config = _mission_and_config()
    assert config.menu is not None
    for key, entry in config.menu.recipes.items():
        if entry.band == "C":
            assert entry.cost.diamond > 0, f"{key} is diamond band but needs no diamond"
            assert entry.cost.iron == 0 and entry.cost.smelt_ops == 0, f"{key} diamond needs no iron/smelt"
        elif entry.band == "B":
            assert entry.cost.iron > 0 and entry.cost.smelt_ops > 0, f"{key} is iron band but no iron/smelt"
            assert entry.cost.diamond == 0
        else:  # A -- Basic
            assert entry.cost.smelt_ops == 0 and entry.cost.iron == 0 and entry.cost.diamond == 0


# --- task generation ----------------------------------------------------------

def test_generate_task_is_deterministic() -> None:
    _, config = _mission_and_config()
    assert generate_task(config, seed=7) == generate_task(config, seed=7)


def test_biome_is_unpinned_for_world_variety() -> None:
    # No biome pin: every task leaves biome None -> a normal, varied overworld
    # the agent can spawn anywhere in (the whole point of giving logs).
    _, config = _mission_and_config()
    for seed in (1, 7, 42, 99, 123):
        assert generate_task(config, seed=seed).biome is None


def test_every_task_has_the_band_composition_and_sums_to_100() -> None:
    _, config = _mission_and_config()
    for seed in SEEDS:
        task = generate_task(config, seed=seed)
        bands = Counter(target.band for target in task.targets)
        assert bands == Counter(EXPECTED_BANDS), f"seed {seed} produced {bands}"
        assert len(task.targets) == sum(EXPECTED_BANDS.values()) == 7
        assert len({target.key for target in task.targets}) == 7
        assert sum(target.points for target in task.targets) == 100.0


def test_every_task_meets_the_bulk_target_rule() -> None:
    _, config = _mission_and_config()
    threshold = config.sampling.bulk_threshold
    for seed in SEEDS:
        task = generate_task(config, seed=seed)
        bulk = [t for t in task.targets if t.target_count >= threshold]
        assert len(bulk) >= config.sampling.min_bulk_targets, f"seed {seed}: {len(bulk)} bulk"


def test_task_ids_name_the_iron_targets() -> None:
    _, config = _mission_and_config()
    task = generate_task(config, seed=42)
    iron_keys = [t.key for t in task.targets if t.band == "C"]
    assert task.task_id.startswith("crafting_v3_42_")
    for key in iron_keys:
        assert key in task.task_id


def test_build_config_carries_kit_and_unpins_biome() -> None:
    mission, base = _mission_and_config()
    task = generate_task(base, seed=3)
    config = mission.build_mission_config(base, task)

    assert config.id == task.task_id
    assert config.seed == task.minecraft_seed
    assert config.biome is None  # unpinned -> varied overworld
    assert config.menu is None
    assert [r for r in config.recipes if r.band == "C"]  # iron band survives
    # The kit survives into the built config so setup_agent can hand it over.
    assert {i.item for i in config.starting_items} == GIVEN_ITEMS


# --- kit ----------------------------------------------------------------------


def test_setup_agent_gives_the_stone_tool_kit() -> None:
    mission, base = _mission_and_config()
    config = mission.build_mission_config(base, generate_task(base, seed=5))
    rcon = FakeRcon()
    setup_crafting_v3_agent(rcon, config)
    give_cmds = [c for c in rcon.commands if c.startswith("give ")]
    assert any("minecraft:iron_pickaxe 3" in c for c in give_cmds)
    assert any("minecraft:iron_axe 3" in c for c in give_cmds)
    assert any("minecraft:oak_log 16" in c for c in give_cmds)


# --- prompting ----------------------------------------------------------------

def test_prompt_schema_is_v2() -> None:
    assert PROMPT_SCHEMA_VERSION == "crafting.v3"


def test_fallback_prompt_names_every_target() -> None:
    _, config = _mission_and_config()
    for seed in (1, 2, 3, 17, 99):
        task = generate_task(config, seed=seed)
        prompt = fallback_prompt(task.targets)
        assert unfaithful_targets(prompt, task.targets) == []
        assert "20 blocks" in prompt


# --- scoring reuse (shared v1 helpers work with the v2 config) -----------------

def test_scoring_runs_and_reports_bands_via_shared_helper() -> None:
    from npabench.evaluation.run_trace import AgentRunTrace, FinalAgentState, TraceEvent
    from npabench.missions.crafting.scoring import score_crafting_run

    mission, base = _mission_and_config()
    config = mission.build_mission_config(base, generate_task(base, seed=8))
    # Full delivery: hold exactly the target count of every recipe item.
    inventory: dict[str, int] = {}
    for recipe in config.recipes:
        inventory[recipe.item] = recipe.target_count
        for item in recipe.items or [recipe.item]:
            inventory.setdefault(item, recipe.target_count)
    trace = AgentRunTrace(
        task_id=config.id,
        agent_name="crafter",
        started_at=0.0,
        agent_ready_at=1.0,
        ended_at=300.0,
        final_state=FinalAgentState(inventory=inventory, position=(1.0, 72.0, 1.0), health=20.0),
    )
    trace.events.append(TraceEvent(kind="done"))
    report = score_crafting_run(
        config, trace, {"alive": True, "deaths": 0, "distance_from_spawn": 2.0}
    )
    assert report["max_score"] == 100.0
    assert report["score"] > 0
    assert {entry["band"] for entry in report["resources"]} <= {"A", "B", "C"}
