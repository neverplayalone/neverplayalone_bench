from itertools import combinations
from unittest.mock import Mock

import pytest

from npabench.evaluation.run_trace import AgentRunTrace
from npabench.missions.animal_husbandry.config_schema import SPECIES
from npabench.missions.animal_husbandry.plugin import snapshot
from npabench.missions.registry import get_mission


@pytest.fixture
def mission():
    return get_mission("animal_husbandry")


def resolved(mission, seed=42):
    base = mission.load_config(mission.default_config_path())
    return mission.build_mission_config(base, mission.generate_task(base, seed))


def trace():
    return AgentRunTrace(task_id="test", agent_name="agent", started_at=1, agent_ready_at=2)


def evidence(config, animals):
    return {
        "run_id": "test",
        "animal_snapshot": {
            "run_id": "test",
            "status": "ok",
            "closed": True,
            "targets": sorted(t.key for t in config.targets),
            "animals": animals,
            "enclosure": {"valid": True},
        },
    }


def animal(species, index, **changes):
    return {
        "uuid": f"animal-{index}",
        "species": species,
        "birth_inside": True,
        "matured_inside": True,
        "alive": True,
        "adult": True,
        "inside": True,
        **changes,
    }


def test_all_10_crop_fed_pairs_are_deterministic(mission):
    base = mission.load_config(mission.default_config_path())
    pairs = set()
    for seed in range(1500):
        task = mission.generate_task(base, seed)
        assert task == mission.generate_task(base, seed)
        assert len({t.key for t in task.targets}) == 2
        assert sum(t.points for t in task.targets) == 100
        pairs.add(tuple(sorted(t.key for t in task.targets)))
        assert resolved(mission, seed).seed == task.minecraft_seed
    assert pairs == set(combinations(sorted(SPECIES), 2))


def test_per_species_cap(mission):
    config = resolved(mission)
    first, second = (t.key for t in config.targets)
    animals = [animal(first, i) for i in range(10)]
    assert mission.score(config, trace(), evidence(config, animals))["score"] == 50
    animals.append(animal(second, 20))
    assert mission.score(config, trace(), evidence(config, animals))["score"] == pytest.approx(
        50 + 50 / config.targets[1].target_count
    )
    animals.extend(animal(second, 20 + i) for i in range(1, config.targets[1].target_count))
    assert mission.score(config, trace(), evidence(config, animals))["score"] == 100


@pytest.mark.parametrize(
    "condition", ["birth_inside", "matured_inside", "alive", "adult", "inside"]
)
def test_all_conditions_required(mission, condition):
    config = resolved(mission)
    row = animal(config.targets[0].key, 0, **{condition: False})
    assert mission.score(config, trace(), evidence(config, [row]))["score"] == 0


def test_invalid_evidence_fails_closed(mission):
    config = resolved(mission)
    row = animal(config.targets[0].key, 0)
    report = mission.score(config, trace(), evidence(config, [row, row]))
    assert report["status"] == "error" and report["score"] == 0
    data = evidence(config, [row])
    data["run_id"] = "different"
    assert mission.score(config, trace(), data)["score"] == 0
    assert mission.score(config, trace(), {})["status"] == "error"


def test_non_targets_do_not_count(mission):
    config = resolved(mission)
    assert mission.score(config, trace(), evidence(config, [animal("mule", 0)]))["score"] == 0


def test_never_ready(mission):
    run = trace()
    run.agent_ready_at = None
    result = mission.score(resolved(mission), run, {})
    assert result["score"] == 0 and result["status"] == "agent_never_spawned"


def test_snapshot_checksum():
    rcon = Mock()
    rcon.command.side_effect = [
        'NPA_ANIMALS {"status":"ok","pages":1,"sha256":"invalid"}',
        'NPA_ANIMALS {"status":"ok","data":"e30="}',
    ]
    with pytest.raises(RuntimeError, match="checksum"):
        snapshot(rcon, "test")


def test_task_supply_kit_and_no_wildlife_search(mission):
    from collections import Counter

    from npabench.missions.animal_husbandry.config_schema import supply_kit
    from npabench.missions.animal_husbandry.environment import setup_agent

    for seed in (1, 2, 3, 4):
        import json

        config = resolved(mission, seed)
        starters = [
            {"uuid": f"{t.key}-{i}", "species": t.key, "initial_age": -24000}
            for t in config.targets
            for i in range(2)
        ]
        rcon = Mock()
        rcon.command.side_effect = lambda cmd, starters=starters: (
            "NPA_ANIMALS "
            + json.dumps({"status": "ok", "spawn": [10, 65, -3], "starters": starters})
            if cmd.startswith("npaanimals")
            else "player has the following entity data: 1b"
            if "OnGround" in cmd
            else "player has the following entity data: [10.5d, 65.0d, -2.5d]"
            if " Pos" in cmd
            else "ok"
        )
        state = setup_agent(rcon, resolved(mission, seed))
        assert state["spawn"] == [10, 65, -3]
        assert "starter_animals" not in state
        commands = [call.args[0] for call in rcon.command.call_args_list]
        assert not any("@e" in cmd or "summon" in cmd or "locate" in cmd for cmd in commands)
        kit = Counter()
        for cmd in commands:
            if cmd.startswith("item replace"):
                item, count = cmd.split(" with minecraft:")[1].split()
                kit[item] += int(count)
        assert kit == supply_kit(config)
        assert state["starter_crops"] == config.starter_crops
        assert kit["oak_fence"] == 128 and kit["oak_fence_gate"] == 8
        assert not any("yellow_terracotta" in cmd or "grass_block" in cmd for cmd in commands)
        assert "lead" not in kit
        assert not any(name.startswith("stone_") for name in kit)


def test_two_targets_are_mandatory_and_distinct(mission):
    from pydantic import ValidationError

    from npabench.missions.animal_husbandry.config_schema import AnimalHusbandryConfig

    config = resolved(mission).model_dump()
    config["targets"] = [config["targets"][0]] * 2
    with pytest.raises(ValidationError, match="two distinct"):
        AnimalHusbandryConfig.model_validate(config)
    config["targets"] = config["targets"][:1]
    with pytest.raises(ValidationError, match="two distinct"):
        AnimalHusbandryConfig.model_validate(config)


def test_random_goals_cover_each_species_range_and_match_prompt(mission):
    base = mission.load_config(mission.default_config_path())
    observed = {species: set() for species in SPECIES}
    for seed in range(1000):
        task = mission.generate_task(base, seed)
        for target in task.targets:
            low, high = base.goal_count_ranges[target.key]
            assert low <= target.target_count <= high
            observed[target.key].add(target.target_count)
            assert f"{target.target_count} adult {target.key} offspring" in task.prompt
    assert all(counts == {2, 3, 4} for counts in observed.values())


def test_custom_ranges_preserve_pair_world_and_other_species_goal(mission):
    base = mission.load_config(mission.default_config_path())
    original = mission.generate_task(base, 42)
    first, second = original.targets
    raw = base.model_dump()
    new_count = 4 if first.target_count != 4 else 2
    raw["goal_count_ranges"][first.key] = (new_count, new_count)
    custom = type(base).model_validate(raw)
    changed = mission.generate_task(custom, 42)
    assert [t.key for t in changed.targets] == [t.key for t in original.targets]
    assert changed.minecraft_seed == original.minecraft_seed
    assert changed.targets[0].target_count == new_count
    assert changed.targets[1].target_count == second.target_count
    resolved_config = mission.build_mission_config(custom, changed)
    assert resolved_config.targets == changed.targets


@pytest.mark.parametrize("invalid", [(4, 2), (0, 2), (1, 2), (2, 5), (2.5, 3)])
def test_invalid_goal_ranges_rejected(mission, invalid):
    from pydantic import ValidationError

    base = mission.load_config(mission.default_config_path())
    raw = base.model_dump()
    raw["goal_count_ranges"]["cow"] = invalid
    with pytest.raises(ValidationError):
        type(base).model_validate(raw)


def test_missing_species_range_and_old_fixed_count_rejected(mission):
    from pydantic import ValidationError

    base = mission.load_config(mission.default_config_path())
    raw = base.model_dump()
    del raw["goal_count_ranges"]["cow"]
    with pytest.raises(ValidationError, match="all five"):
        type(base).model_validate(raw)
    raw = base.model_dump()
    raw["target_count"] = 2
    with pytest.raises(ValidationError, match="goal_count_ranges"):
        type(base).model_validate(raw)


def test_starters_never_score_even_if_marked_grown_and_alive(mission):
    config = resolved(mission)
    row = animal(config.targets[0].key, "starter")
    data = evidence(config, [row])
    data["animal_snapshot"]["starters"] = [{"uuid": row["uuid"], "species": row["species"]}]
    assert mission.score(config, trace(), data)["score"] == 0
    data["animal_snapshot"]["animals"].append(animal(config.targets[0].key, "offspring"))
    assert mission.score(config, trace(), data)["score"] == pytest.approx(
        50 / config.targets[0].target_count
    )


def test_task_prompt_contains_only_targets_and_time_limit(mission):
    task = mission.generate_task(mission.load_config(mission.default_config_path()), 10)
    first, second = task.targets
    assert task.generator_version == "4.1.0"
    assert task.prompt == (
        f"Raise {first.target_count} adult {first.key} offspring and "
        f"{second.target_count} adult {second.key} offspring within 20 minutes."
    )


@pytest.mark.parametrize("valid", [False, None])
def test_invalid_enclosure_gates_otherwise_complete_score(mission, valid):
    config = resolved(mission)
    rows = [animal(t.key, f"{t.key}-{i}") for t in config.targets for i in range(t.target_count)]
    data = evidence(config, rows)
    data["animal_snapshot"]["enclosure"] = {"valid": valid}
    result = mission.score(config, trace(), data)
    assert result["score"] == 0
    assert result["animal_score_before_gate"] == 100


def test_snapshot_requires_enclosure_evidence(mission):
    from unittest.mock import patch

    from npabench.missions.animal_husbandry.final_state import collect_state
    from npabench.missions.animal_husbandry.plugin import manifest_hash

    config = resolved(mission)
    saved = {"manifest_sha256": manifest_hash(config)}
    with (
        patch("npabench.missions.animal_husbandry.final_state.snapshot", return_value=saved),
        pytest.raises(TypeError, match="enclosure"),
    ):
        collect_state(Mock(), config, {"run_id": "test"})


@pytest.mark.parametrize("minimum,maximum", [(19, 64), (20, 65), (30, 20)])
def test_invalid_enclosure_limits_rejected(mission, minimum, maximum):
    from pydantic import ValidationError

    base = mission.load_config(mission.default_config_path())
    raw = base.model_dump()
    raw.update(min_farm_size=minimum, max_farm_size=maximum)
    with pytest.raises(ValidationError):
        type(base).model_validate(raw)


def test_setup_rejects_airborne_spawn(mission):
    from unittest.mock import patch

    from npabench.missions.animal_husbandry.environment import setup_agent

    rcon = Mock()
    rcon.command.side_effect = lambda cmd: (
        'NPA_ANIMALS {"status":"ok"}'
        if cmd.startswith("npaanimals")
        else "player data: [0.5d, 100.0d, 0.5d]"
        if cmd.endswith(" Pos")
        else "player data: 0b"
    )
    with (
        patch("npabench.missions.animal_husbandry.environment.time.sleep"),
        pytest.raises(RuntimeError, match="settle"),
    ):
        setup_agent(rcon, resolved(mission))
    assert not any("setblock" in call.args[0] for call in rcon.command.call_args_list)


def test_valid_enclosure_requires_registered_bounds(mission):
    from unittest.mock import patch

    from npabench.missions.animal_husbandry.final_state import collect_state
    from npabench.missions.animal_husbandry.plugin import manifest_hash

    config = resolved(mission)
    saved = {"manifest_sha256": manifest_hash(config), "enclosure": {"valid": True}}
    with (
        patch("npabench.missions.animal_husbandry.final_state.snapshot", return_value=saved),
        pytest.raises(RuntimeError, match="registered bounds"),
    ):
        collect_state(Mock(), config, {"run_id": "test"})


def test_starter_crop_counts_are_reproducible_and_cover_1_through_4(mission):
    from npabench.missions.animal_husbandry.config_schema import CROP_ITEMS

    base = mission.load_config(mission.default_config_path())
    seen = {item: set() for item in CROP_ITEMS}
    for seed in range(200):
        task = mission.generate_task(base, seed)
        assert task.starter_crops == mission.generate_task(base, seed).starter_crops
        config = mission.build_mission_config(base, task)
        assert config.starter_crops == task.starter_crops
        for item, count in task.starter_crops.items():
            assert 1 <= count < 5
            seen[item].add(count)
    assert all(values == {1, 2, 3, 4} for values in seen.values())
    assert any(
        len(set(mission.generate_task(base, seed).starter_crops.values())) > 1 for seed in range(20)
    )


@pytest.mark.parametrize("count", [0, 5, -1, 1.5, True, "2"])
def test_starter_crop_count_bounds_are_strict(mission, count):
    from pydantic import ValidationError

    raw = resolved(mission).model_dump()
    raw["starter_crops"]["carrot"] = count
    with pytest.raises(ValidationError):
        type(resolved(mission)).model_validate(raw)


def test_missing_crop_items_rejected(mission):
    from pydantic import ValidationError

    from npabench.missions.animal_husbandry.config_schema import supply_kit

    raw = resolved(mission).model_dump()
    del raw["starter_crops"]["carrot"]
    with pytest.raises(ValidationError, match="all four"):
        type(resolved(mission)).model_validate(raw)
    with pytest.raises(ValueError, match="resolve"):
        supply_kit(mission.load_config(mission.default_config_path()))


def test_combined_prompt_and_fixed_twenty_minute_budget(mission):
    from pydantic import ValidationError

    base = mission.load_config(mission.default_config_path())
    task = mission.generate_task(base, 10)
    assert base.duration_seconds == 1200
    assert task.schema_version == 5 and task.task_id.startswith("animal_husbandry_v4_1_")
    assert task.prompt.endswith("within 20 minutes.")
    raw = base.model_dump()
    raw["duration_seconds"] = 1800
    with pytest.raises(ValidationError):
        type(base).model_validate(raw)


def test_non_crop_fed_targets_rejected(mission):
    from pydantic import ValidationError

    base = mission.load_config(mission.default_config_path())
    for species in ("horse", "goat", "donkey", "llama", "armadillo"):
        raw = base.model_dump()
        raw["targets"] = [
            {"key": species, "display_name": species},
            {"key": "cow", "display_name": "Cow"},
        ]
        with pytest.raises(ValidationError):
            type(base).model_validate(raw)
