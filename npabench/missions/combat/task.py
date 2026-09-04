from __future__ import annotations

import math
import random
from collections import defaultdict
from itertools import product

from pydantic import Field

from npabench.missions.base import Task, TaskTarget
from npabench.missions.combat.config_schema import (
    TIER_ORDER,
    CombatMissionConfig,
    CombatTargetSpec,
    CombatWave,
    MobMenuEntry,
    Tier,
    WaveSpawn,
)

SPAWN_OFFSETS: tuple[tuple[int, int], ...] = (
    (12, 0),
    (-12, 0),
    (0, 12),
    (0, -12),
    (9, 9),
    (9, -9),
    (-9, 9),
    (-9, -9),
    (15, 4),
    (15, -4),
    (-15, 4),
    (-15, -4),
    (4, 15),
    (-4, 15),
    (4, -15),
    (-4, -15),
)


class CombatTaskTarget(TaskTarget):
    tier: Tier
    entity_type: str
    difficulty_weight: float
    spawn_count: int
    drop_items: list[str] = Field(default_factory=list)
    objective: str


class CombatTask(Task):
    targets: list[CombatTaskTarget] = Field(default_factory=list)
    waves: list[CombatWave] = Field(default_factory=list)
    preparation_seconds: int
    combat_seconds: int
    death_penalty_points: float
    maximum_death_penalty: float


def generate_task(
    base_config: CombatMissionConfig,
    seed: int,
    task_id: str | None = None,
) -> CombatTask:
    if base_config.menu is None:
        raise ValueError("combat config has no `menu` section")
    rng = random.Random(seed)
    targets = resolve_task_targets(base_config, rng)
    waves = build_waves(base_config, targets, rng)
    minecraft_seed = rng.getrandbits(64)
    selected_id = task_id or build_task_id(seed, targets)
    return CombatTask(
        task_id=selected_id,
        seed=seed,
        minecraft_seed=minecraft_seed,
        targets=targets,
        waves=waves,
        preparation_seconds=base_config.phase.preparation_seconds,
        combat_seconds=base_config.phase.combat_seconds,
        death_penalty_points=base_config.scoring.death_penalty_points,
        maximum_death_penalty=base_config.scoring.maximum_death_penalty,
    )


def resolve_task_targets(
    base_config: CombatMissionConfig,
    rng: random.Random,
) -> list[CombatTaskTarget]:
    assert base_config.menu is not None
    targets: list[CombatTaskTarget] = []
    objective_index = 0
    for tier in TIER_ORDER:
        rule = base_config.tier_rules[tier]
        menu = base_config.menu[tier].mobs
        keys = rng.sample(sorted(menu), rule.target_kinds)
        counts = _choose_balanced_counts(
            [menu[key] for key in keys],
            target_units=rule.target_difficulty_units,
            slack=rule.count_choice_slack,
            rng=rng,
        )
        tier_targets: list[CombatTaskTarget] = []
        for key, target_count in zip(keys, counts, strict=True):
            entry = menu[key]
            spawn_count = max(
                target_count + 1,
                math.ceil(target_count * entry.spawn_reserve_multiplier),
            )
            tier_targets.append(
                _target_from_entry(
                    key,
                    entry,
                    tier=tier,
                    target_count=target_count,
                    spawn_count=spawn_count,
                    objective=f"ncb_k{objective_index:02d}",
                )
            )
            objective_index += 1
        _allocate_tier_points(tier_targets, rule.points)
        targets.extend(tier_targets)
    return targets


def _choose_balanced_counts(
    entries: list[MobMenuEntry],
    *,
    target_units: float,
    slack: float,
    rng: random.Random,
) -> tuple[int, ...]:
    combinations = list(
        product(*(range(entry.target_range[0], entry.target_range[1] + 1) for entry in entries))
    )
    ranked = sorted(
        combinations,
        key=lambda counts: abs(
            sum(
                count * entry.difficulty_weight
                for count, entry in zip(counts, entries, strict=True)
            )
            - target_units
        ),
    )
    best_error = abs(
        sum(
            count * entry.difficulty_weight for count, entry in zip(ranked[0], entries, strict=True)
        )
        - target_units
    )
    balanced = [
        counts
        for counts in ranked
        if abs(
            sum(
                count * entry.difficulty_weight
                for count, entry in zip(counts, entries, strict=True)
            )
            - target_units
        )
        <= best_error + slack
    ]
    return rng.choice(balanced)


def _target_from_entry(
    key: str,
    entry: MobMenuEntry,
    *,
    tier: Tier,
    target_count: int,
    spawn_count: int,
    objective: str,
) -> CombatTaskTarget:
    return CombatTaskTarget(
        key=key,
        display_name=entry.display_name,
        items=[],
        target_count=target_count,
        points=0.0,
        tier=tier,
        entity_type=entry.entity_type,
        difficulty_weight=entry.difficulty_weight,
        spawn_count=spawn_count,
        drop_items=list(entry.drop_items),
        objective=objective,
    )


def _allocate_tier_points(targets: list[CombatTaskTarget], tier_points: float) -> None:
    total_units = sum(target.target_count * target.difficulty_weight for target in targets)
    allocated = 0.0
    for index, target in enumerate(targets):
        if index == len(targets) - 1:
            points = tier_points - allocated
        else:
            share = target.target_count * target.difficulty_weight / total_units
            points = round(tier_points * share, 9)
            allocated += points
        targets[index] = target.model_copy(update={"points": points})


def build_waves(
    base_config: CombatMissionConfig,
    targets: list[CombatTaskTarget],
    rng: random.Random,
) -> list[CombatWave]:
    wave_offsets = base_config.phase.wave_offsets_seconds
    distributed: dict[int, list[tuple[CombatTaskTarget, int]]] = defaultdict(list)
    for target in targets:
        quotient, remainder = divmod(target.spawn_count, len(wave_offsets))
        for wave_index in range(len(wave_offsets)):
            count = quotient + (1 if wave_index < remainder else 0)
            if count:
                distributed[wave_index].append((target, count))

    waves: list[CombatWave] = []
    for wave_index, offset_seconds in enumerate(wave_offsets):
        offsets = list(SPAWN_OFFSETS)
        rng.shuffle(offsets)
        offset_index = 0
        spawns: list[WaveSpawn] = []
        entries = list(distributed[wave_index])
        rng.shuffle(entries)
        for target, count in entries:
            selected_offsets = [offsets[(offset_index + i) % len(offsets)] for i in range(count)]
            offset_index += count
            spawns.append(
                WaveSpawn(
                    target_key=target.key,
                    entity_type=target.entity_type,
                    tier=target.tier,
                    count=count,
                    offsets=selected_offsets,
                )
            )
        waves.append(CombatWave(index=wave_index, offset_seconds=offset_seconds, spawns=spawns))
    return waves


def build_task_id(seed: int, targets: list[CombatTaskTarget]) -> str:
    keys = "_".join(target.key for target in targets)
    return f"combat_{seed}_{keys}"


def target_specs(targets: list[CombatTaskTarget]) -> list[CombatTargetSpec]:
    return [CombatTargetSpec.model_validate(target.model_dump()) for target in targets]
