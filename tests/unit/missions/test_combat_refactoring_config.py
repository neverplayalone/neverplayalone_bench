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


def test_prompt_explains_capped_release_queue_and_conditional_reserves(tmp_path: Path) -> None:
    task = generate_task(default_config(), 0)
    materialized = materialize_task_prompt(task, tmp_path)
    assert materialized.prompt_metadata is not None
    assert materialized.prompt_metadata.schema_version == PROMPT_SCHEMA_VERSION == "combat.v2"
    prompt = materialized.prompt
    assert "first 10 minutes" in prompt
    assert "Combat then lasts 8 minutes" in prompt
    assert "New spawns pause while 3 or more living, loaded mission enemies are present" in prompt
    assert "Unloaded survivors can reappear" in prompt
    assert "Released enemies wait in a queue" in prompt
    assert "does not guarantee an immediate spawn" in prompt
    assert "Reserve enemies replace missing kill opportunities only" in prompt
    assert "combat time limit" in prompt
    assert "- 0 seconds: easy" in prompt
    assert "- 3 minutes: medium" in prompt
    assert "- 6 minutes: hard" in prompt
    assert "Easy (30 points)" in prompt
    assert "Medium (40 points)" in prompt
    assert "Hard (30 points)" in prompt
    assert "Each death subtracts 10 points" in prompt


def test_prompt_uses_precise_durations_and_task_points() -> None:
    data = default_config().model_dump()
    data["duration_seconds"] = 1082
    data["phase"].update(preparation_seconds=601, combat_seconds=481, max_active_mobs=2)
    data["phase"]["wave_offsets_seconds"][1] = 61
    for tier, points in {"easy": 25.0, "medium": 35.0, "hard": 40.0}.items():
        data["tier_rules"][tier]["points"] = points
    task = generate_task(CombatMissionConfig.model_validate(data), 5)
    prompt = fallback_prompt(task)
    assert "first 10 minutes 1 second" in prompt
    assert "Combat then lasts 8 minutes 1 second" in prompt
    assert "New spawns pause while 2 or more living, loaded mission enemies" in prompt
    assert "- 1 minute 1 second: easy" in prompt
    assert "Easy (25 points)" in prompt
    assert "Medium (35 points)" in prompt
    assert "Hard (40 points)" in prompt
