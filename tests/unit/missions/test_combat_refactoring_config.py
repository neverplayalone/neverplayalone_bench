from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from npabench.missions.combat import CombatMission
from npabench.missions.combat.config_schema import (
    TIER_ORDER,
    CombatMissionConfig,
    PhaseRules,
)
from npabench.missions.combat.prompting import (
    PROMPT_SCHEMA_VERSION,
    fallback_prompt,
    materialize_task_prompt,
)
from npabench.missions.combat.task import CombatTask, generate_task


def default_config() -> CombatMissionConfig:
    mission = CombatMission()
    return mission.load_config(mission.default_config_path())


def test_schema_and_yaml_defaults_agree_on_refactored_schedule() -> None:
    config = default_config()
    schema = CombatMissionConfig()
    assert config.phase == schema.phase == PhaseRules()
    assert config.duration_seconds == schema.duration_seconds == 1080
    assert config.phase.preparation_seconds == 600
    assert config.phase.combat_seconds == 480
    assert config.phase.max_active_mobs == 3
    assert config.phase.wave_offsets_seconds == list(range(0, 480, 60))
    assert config.phase.wave_tiers == [["easy"]] * 3 + [["medium"]] * 3 + [["hard"]] * 2
    assert config.scoring.death_penalty_points == schema.scoring.death_penalty_points == 10.0
    assert (
        {tier: config.tier_rules[tier].points for tier in TIER_ORDER}
        == {tier: schema.tier_rules[tier].points for tier in TIER_ORDER}
        == {"easy": 30.0, "medium": 40.0, "hard": 30.0}
    )


@pytest.mark.parametrize("cap", [0, -1, True, 1.5, "3", None])
def test_active_mob_cap_requires_a_positive_integer(cap: object) -> None:
    with pytest.raises(ValidationError, match="max_active_mobs"):
        PhaseRules(max_active_mobs=cap)


def test_natural_spawning_cannot_bypass_active_mob_cap() -> None:
    with pytest.raises(ValidationError, match="natural mob spawning must stay disabled"):
        PhaseRules(spawn_mobs_naturally=True)


def test_custom_active_mob_cap_is_preserved_in_task_and_built_config() -> None:
    config = default_config()
    data = config.model_dump()
    data["phase"]["max_active_mobs"] = 2
    custom_config = CombatMissionConfig.model_validate(data)
    task = generate_task(custom_config, 19)
    assert task.max_active_mobs == 2
    assert CombatTask.model_validate(task.model_dump()).max_active_mobs == 2
    built = CombatMission().build_mission_config(config, task)
    assert built.phase.max_active_mobs == 2


@pytest.mark.parametrize("seed", [0, 1, 42, 2**128 + 17])
def test_world_seed_is_independent_of_wave_schedule_and_phase_durations(seed: int) -> None:
    config = default_config()
    changed = config.model_dump()
    changed["duration_seconds"] = 1082
    changed["phase"].update(
        preparation_seconds=601,
        combat_seconds=481,
        wave_offsets_seconds=[0, 90, 180, 270],
        wave_tiers=[["easy"], ["easy", "medium"], ["medium", "hard"], ["hard"]],
    )
    changed_config = CombatMissionConfig.model_validate(changed)
    original = generate_task(config, seed)
    modified = generate_task(changed_config, seed)
    assert original.minecraft_seed == modified.minecraft_seed
    assert original.targets == modified.targets
    assert original.waves != modified.waves
    assert original == generate_task(config, seed)


def test_world_seed_is_independent_of_target_random_draws() -> None:
    config = default_config()
    changed = config.model_dump()
    changed["tier_rules"]["easy"]["target_kinds"] = 2
    changed_config = CombatMissionConfig.model_validate(changed)
    original = generate_task(config, 7)
    modified = generate_task(changed_config, 7)
    assert original.minecraft_seed == modified.minecraft_seed
    assert original.targets != modified.targets
    assert original.minecraft_seed != generate_task(config, 8).minecraft_seed


def test_prompt_briefly_covers_preparation_and_combat_and_preserves_the_task(tmp_path: Path) -> None:
    task = generate_task(default_config(), 1)
    materialized = materialize_task_prompt(task, tmp_path)
    assert materialized.prompt_metadata is not None
    assert materialized.prompt_metadata.schema_version == PROMPT_SCHEMA_VERSION == "combat.v5"
    prompt = materialized.prompt
    assert prompt == (
        "Gather resources and craft your combat gear, then kill 8 Spiders, 3 Drowned, "
        "2 Husks, 1 Witch, and 2 Creepers as enemy waves arrive. "
        "Adapt to each wave and stay alive until the mission ends."
    )
    assert materialized.model_dump(exclude={"prompt", "prompt_metadata"}) == task.model_dump(
        exclude={"prompt", "prompt_metadata"}
    )


def test_brief_prompt_omits_detailed_schedule_scoring_and_inventory_rules() -> None:
    data = default_config().model_dump()
    data["duration_seconds"] = 1082
    data["phase"].update(preparation_seconds=601, combat_seconds=481, max_active_mobs=2)
    data["phase"]["wave_offsets_seconds"][1] = 61
    for tier, points in {"easy": 25.0, "medium": 35.0, "hard": 40.0}.items():
        data["tier_rules"][tier]["points"] = points
    data["scoring"]["death_penalty_points"] = 7.5
    data["keep_inventory"] = False
    task = generate_task(CombatMissionConfig.model_validate(data), 5)
    prompt = fallback_prompt(task)
    assert prompt == fallback_prompt(generate_task(default_config(), 5))
    for detail in ["minutes", "seconds", "points", "inventory", "queue", "reserve", "ready", "done"]:
        assert detail not in prompt.lower()


@pytest.mark.parametrize("seed", [0, 1, 42, 2**128 + 17])
def test_objective_prompt_preserves_every_seeded_target_and_count(seed: int) -> None:
    task = generate_task(default_config(), seed)
    prompt = fallback_prompt(task)
    for target in task.targets:
        name = target.display_name
        if target.target_count != 1:
            name = {"Drowned": "Drowned", "Witch": "Witches", "Enderman": "Endermen"}.get(
                name, f"{name}s"
            )
        assert f"{target.target_count} {name}" in prompt
    assert len(prompt.split()) <= 55


@pytest.mark.parametrize(
    ("name", "count", "expected_target"),
    [
        ("Zombie", 1, "1 Zombie"),
        ("Zombie", 2, "2 Zombies"),
        ("Spider", 8, "8 Spiders"),
        ("Skeleton", 3, "3 Skeletons"),
        ("Husk", 2, "2 Husks"),
        ("Stray", 2, "2 Strays"),
        ("Creeper", 2, "2 Creepers"),
        ("Cave Spider", 2, "2 Cave Spiders"),
        ("Drowned", 1, "1 Drowned"),
        ("Drowned", 3, "3 Drowned"),
        ("Witch", 1, "1 Witch"),
        ("Witch", 2, "2 Witches"),
        ("Enderman", 1, "1 Enderman"),
        ("Enderman", 2, "2 Endermen"),
    ],
)
def test_objective_prompt_uses_natural_mob_names(name: str, count: int, expected_target: str) -> None:
    task = generate_task(default_config(), 0)
    target = task.targets[0].model_copy(update={"display_name": name, "target_count": count})
    assert f"kill {expected_target}." in fallback_prompt(task.model_copy(update={"targets": [target]}))


def test_objective_prompt_joins_two_targets_without_an_extra_comma() -> None:
    task = generate_task(default_config(), 1)
    prompt = fallback_prompt(task.model_copy(update={"targets": task.targets[:2]}))
    assert "kill 8 Spiders and 3 Drowned as enemy waves arrive" in prompt


def test_empty_objective_does_not_request_unspecified_mobs() -> None:
    task = generate_task(default_config(), 0).model_copy(update={"targets": []})
    assert "kill no mobs." in fallback_prompt(task)


def test_prompt_wording_varies_by_seed_but_stays_reproducible() -> None:
    task = generate_task(default_config(), 1)
    prompts = []
    for seed in range(3):
        variant = task.model_copy(update={"seed": seed})
        prompt = fallback_prompt(variant)
        assert prompt == fallback_prompt(variant)
        assert "8 Spiders, 3 Drowned, 2 Husks, 1 Witch, and 2 Creepers" in prompt
        assert "Adapt to each wave and stay alive until the mission ends." in prompt
        assert len(prompt.split()) <= 55
        prompts.append(prompt)
    assert len(set(prompts)) == 3
