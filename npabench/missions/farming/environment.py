from __future__ import annotations

import random
from collections import Counter
from typing import Any

from mcrcon import MCRcon

from npabench.minecraft.rcon_client import command_with_retry
from npabench.minecraft.rcon_helpers import read_score
from npabench.minecraft.spawn import set_exact_spawn, use_world_spawn
from npabench.missions.farming.config_schema import (
    PLOT_COLORS,
    FarmingMissionConfig,
    FarmingTargetSpec,
)
from npabench.missions.farming.datapack import (
    HARVEST_OBJECTIVE,
    MARKER_TAG,
    PACK_SCORE_HOLDER,
    READY_OBJECTIVE,
    SYSTEM_OBJECTIVE,
)

DEATH_OBJECTIVE = "mcb_deaths"


def configure_farming_world(rcon: MCRcon, mission_config: FarmingMissionConfig) -> None:
    command_with_retry(
        rcon,
        "gamerule keep_inventory " + ("true" if mission_config.keep_inventory else "false"),
    )
    command_with_retry(rcon, "gamerule advance_time false")
    command_with_retry(rcon, "gamerule advance_weather false")
    command_with_retry(rcon, "gamerule spawn_mobs false")
    command_with_retry(rcon, "gamerule mob_griefing false")
    command_with_retry(rcon, "gamerule random_tick_speed 0")
    command_with_retry(rcon, f"difficulty {mission_config.difficulty}")
    command_with_retry(rcon, f"time set {mission_config.spawn_time}")
    command_with_retry(rcon, "weather clear")
    command_with_retry(rcon, "worldborder center 0 0")
    command_with_retry(rcon, f"worldborder set {mission_config.world_size}")


def setup_farming_agent(
    rcon: MCRcon,
    mission_config: FarmingMissionConfig,
) -> dict[str, Any]:
    command_with_retry(rcon, f"op {mission_config.username}")
    command_with_retry(rcon, f"clear {mission_config.username}")
    command_with_retry(rcon, f"effect clear {mission_config.username}")
    command_with_retry(rcon, f"kill @e[tag={MARKER_TAG}]")
    command_with_retry(rcon, "kill @e[type=item]")

    # The reference world receives the pack after its template server stops.
    # Explicitly enabling and reloading it makes cloned run worlds deterministic.
    rcon.command(f'datapack enable "file/{mission_config.environment.datapack_name}"')
    command_with_retry(rcon, "reload", attempts=1)
    if read_score(rcon, PACK_SCORE_HOLDER, SYSTEM_OBJECTIVE) != 1:
        raise RuntimeError("farming harvest datapack did not load")

    for objective in (HARVEST_OBJECTIVE, READY_OBJECTIVE):
        command_with_retry(rcon, f"scoreboard objectives remove {objective}")
        command_with_retry(rcon, f"scoreboard objectives add {objective} dummy")
    for target in mission_config.targets:
        command_with_retry(
            rcon,
            f"scoreboard players set {target.harvest_holder} {HARVEST_OBJECTIVE} 0",
        )

    world_spawn = use_world_spawn(rcon, mission_config.username)
    hub = _build_farm_hub(rcon, mission_config, world_spawn)
    plots = _build_crop_plots(rcon, mission_config, hub)
    _fill_supply_barrel(rcon, mission_config, hub)
    spawn = set_exact_spawn(
        rcon,
        mission_config.username,
        hub["center_x"],
        hub["ground_y"],
        hub["center_z"] + 3,
    )

    command_with_retry(rcon, f"scoreboard objectives remove {DEATH_OBJECTIVE}")
    command_with_retry(
        rcon,
        f"scoreboard objectives add {DEATH_OBJECTIVE} minecraft.custom:minecraft.deaths",
    )
    command_with_retry(
        rcon, f"scoreboard players add {mission_config.username} {DEATH_OBJECTIVE} 0"
    )
    death_baseline = read_score(rcon, mission_config.username, DEATH_OBJECTIVE)
    command_with_retry(rcon, f"gamemode survival {mission_config.username}")
    command_with_retry(rcon, f"deop {mission_config.username}")
    return {
        "death_baseline": death_baseline,
        "spawn": spawn,
        "hub": hub,
        "plots": plots,
    }


def _build_farm_hub(
    rcon: MCRcon,
    mission_config: FarmingMissionConfig,
    world_spawn: tuple[int, int, int],
) -> dict[str, int]:
    x, y, z = world_spawn
    radius = mission_config.environment.hub_radius
    x1, x2 = x - radius, x + radius
    z1, z2 = z - radius, z + radius
    command_with_retry(rcon, f"fill {x1} {y} {z1} {x2} {y + 10} {z2} air")
    command_with_retry(rcon, f"fill {x1} {y - 3} {z1} {x2} {y - 2} {z2} dirt")
    command_with_retry(rcon, f"fill {x1} {y - 1} {z1} {x2} {y - 1} {z2} grass_block")
    for command in (
        f"fill {x1} {y} {z1} {x2} {y} {z1} oak_fence",
        f"fill {x1} {y} {z2} {x2} {y} {z2} oak_fence",
        f"fill {x1} {y} {z1} {x1} {y} {z2} oak_fence",
        f"fill {x2} {y} {z1} {x2} {y} {z2} oak_fence",
    ):
        command_with_retry(rcon, command)
    command_with_retry(rcon, f"setblock {x} {y} {z1} oak_fence_gate")
    command_with_retry(rcon, f"setblock {x} {y} {z2} oak_fence_gate")
    command_with_retry(rcon, f"setblock {x1} {y} {z} oak_fence_gate[facing=east]")
    command_with_retry(rcon, f"setblock {x2} {y} {z} oak_fence_gate[facing=east]")

    barrel_x, barrel_z = x, z - 2
    command_with_retry(rcon, f"setblock {barrel_x} {y} {barrel_z} barrel")
    command_with_retry(rcon, f"setblock {x + 1} {y} {barrel_z} crafting_table")
    command_with_retry(rcon, f"setblock {x + 2} {y} {barrel_z} furnace[facing=south]")
    return {
        "center_x": x,
        "center_z": z,
        "ground_y": y,
        "barrel_x": barrel_x,
        "barrel_z": barrel_z,
    }


def _build_crop_plots(
    rcon: MCRcon,
    mission_config: FarmingMissionConfig,
    hub: dict[str, int],
) -> list[dict[str, Any]]:
    offsets = [(-12, -12), (12, -12), (-12, 12), (12, 12)]
    rng = random.Random(mission_config.layout_seed)
    rng.shuffle(offsets)
    plots: list[dict[str, Any]] = []
    for index, (target, (dx, dz)) in enumerate(zip(mission_config.targets, offsets, strict=True)):
        plot = _build_crop_plot(
            rcon,
            target,
            index,
            hub["center_x"] + dx,
            hub["ground_y"],
            hub["center_z"] + dz,
            mission_config.environment.plot_size,
            rng,
        )
        plots.append(plot)
    command_with_retry(rcon, f"scoreboard players set @e[tag={MARKER_TAG}] {READY_OBJECTIVE} 0")
    return plots


def _build_crop_plot(
    rcon: MCRcon,
    target: FarmingTargetSpec,
    index: int,
    center_x: int,
    ground_y: int,
    center_z: int,
    size: int,
    rng: random.Random,
) -> dict[str, Any]:
    border_color = PLOT_COLORS[index % len(PLOT_COLORS)]
    half = size // 2
    x1, x2 = center_x - half, center_x + half
    z1, z2 = center_z - half, center_z + half
    command_with_retry(
        rcon,
        f"fill {x1 - 1} {ground_y} {z1 - 1} {x2 + 1} {ground_y + 7} {z2 + 1} air",
    )
    command_with_retry(
        rcon,
        f"fill {x1 - 1} {ground_y - 1} {z1 - 1} {x2 + 1} {ground_y - 1} "
        f"{border_color}_concrete outline",
    )
    plot: dict[str, Any] = {
        "index": index,
        "target_key": target.key,
        "plot_tag": target.plot_tag,
        "template": target.template,
        "border_color": border_color,
        "center_x": center_x,
        "center_z": center_z,
        "ground_y": ground_y,
        "x1": x1,
        "x2": x2,
        "z1": z1,
        "z2": z2,
        "cells": [],
    }
    builder = _PLOT_BUILDERS[target.template]
    builder(rcon, target, plot, rng)
    _summon_plot_markers(rcon, target, plot)
    return plot


def _set_floor(rcon: MCRcon, plot: dict[str, Any], block: str) -> None:
    command_with_retry(
        rcon,
        f"fill {plot['x1']} {plot['ground_y'] - 1} {plot['z1']} "
        f"{plot['x2']} {plot['ground_y'] - 1} {plot['z2']} {block}",
    )


def _age_plot(
    rcon: MCRcon,
    target: FarmingTargetSpec,
    plot: dict[str, Any],
    rng: random.Random,
) -> None:
    substrate = {
        "farmland": "farmland[moisture=7]",
        "sweet_berries": "grass_block",
        "nether_wart": "soul_sand",
    }[target.template]
    _set_floor(rcon, plot, substrate)
    if target.template == "farmland":
        water_x = plot["center_x"] + rng.choice((-1, 0, 1))
        water_z = plot["center_z"] + rng.choice((-1, 0, 1))
        command_with_retry(rcon, f"setblock {water_x} {plot['ground_y'] - 1} {water_z} water")
    else:
        water_x = water_z = None
    for x in range(plot["x1"], plot["x2"] + 1):
        for z in range(plot["z1"], plot["z2"] + 1):
            if (x, z) != (water_x, water_z):
                plot["cells"].append({"pos": (x, plot["ground_y"], z)})


def _water_edge_plot(
    rcon: MCRcon, target: FarmingTargetSpec, plot: dict[str, Any], rng: random.Random
) -> None:
    del target, rng
    _set_floor(rcon, plot, "grass_block")
    z = plot["center_z"]
    command_with_retry(
        rcon,
        f"fill {plot['x1']} {plot['ground_y'] - 1} {z} "
        f"{plot['x2']} {plot['ground_y'] - 1} {z} water",
    )
    for x in range(plot["x1"], plot["x2"] + 1):
        for base_z in (z - 1, z + 1):
            plot["cells"].append(
                {
                    "pos": (x, plot["ground_y"] + 1, base_z),
                    "source": (x, plot["ground_y"], base_z),
                }
            )


def _cactus_plot(
    rcon: MCRcon, target: FarmingTargetSpec, plot: dict[str, Any], rng: random.Random
) -> None:
    del target, rng
    _set_floor(rcon, plot, "grass_block")
    for x in range(plot["x1"], plot["x2"] + 1, 3):
        for z in range(plot["z1"], plot["z2"] + 1, 3):
            command_with_retry(rcon, f"setblock {x} {plot['ground_y'] - 1} {z} sand")
            plot["cells"].append(
                {
                    "pos": (x, plot["ground_y"] + 1, z),
                    "source": (x, plot["ground_y"], z),
                }
            )


def _bamboo_plot(
    rcon: MCRcon, target: FarmingTargetSpec, plot: dict[str, Any], rng: random.Random
) -> None:
    del target, rng
    _set_floor(rcon, plot, "stone")
    for x in range(plot["x1"], plot["x2"] + 1, 2):
        for z in range(plot["z1"], plot["z2"] + 1, 2):
            command_with_retry(rcon, f"setblock {x} {plot['ground_y'] - 1} {z} dirt")
            plot["cells"].append(
                {
                    "pos": (x, plot["ground_y"] + 1, z),
                    "source": (x, plot["ground_y"], z),
                }
            )


def _stem_plot(
    rcon: MCRcon, target: FarmingTargetSpec, plot: dict[str, Any], rng: random.Random
) -> None:
    del target, rng
    _set_floor(rcon, plot, "grass_block")
    command_with_retry(
        rcon,
        f"setblock {plot['center_x']} {plot['ground_y'] - 1} {plot['center_z']} water",
    )
    source_zs = (plot["center_z"] - 2, plot["center_z"] + 2)
    for row, source_z in enumerate(source_zs):
        fruit_dz = 1 if row == 0 else -1
        for x in range(plot["x1"], plot["x2"] + 1, 2):
            command_with_retry(
                rcon,
                f"setblock {x} {plot['ground_y'] - 1} {source_z} farmland[moisture=7]",
            )
            plot["cells"].append(
                {
                    "pos": (x, plot["ground_y"], source_z + fruit_dz),
                    "source": (x, plot["ground_y"], source_z),
                }
            )


def _cocoa_plot(
    rcon: MCRcon, target: FarmingTargetSpec, plot: dict[str, Any], rng: random.Random
) -> None:
    del target, rng
    _set_floor(rcon, plot, "grass_block")
    log_x = plot["center_x"]
    for y in (plot["ground_y"], plot["ground_y"] + 1):
        for z in range(plot["center_z"] - 2, plot["center_z"] + 2):
            command_with_retry(rcon, f"setblock {log_x} {y} {z} jungle_log")
            plot["cells"].append({"pos": (log_x + 1, y, z)})


def _kelp_plot(
    rcon: MCRcon, target: FarmingTargetSpec, plot: dict[str, Any], rng: random.Random
) -> None:
    del target, rng
    _set_floor(rcon, plot, "glass")
    command_with_retry(
        rcon,
        f"fill {plot['x1']} {plot['ground_y']} {plot['z1']} "
        f"{plot['x2']} {plot['ground_y'] + 3} {plot['z2']} water",
    )
    for command in (
        f"fill {plot['x1']} {plot['ground_y']} {plot['z1']} "
        f"{plot['x2']} {plot['ground_y'] + 3} {plot['z1']} glass",
        f"fill {plot['x1']} {plot['ground_y']} {plot['z2']} "
        f"{plot['x2']} {plot['ground_y'] + 3} {plot['z2']} glass",
        f"fill {plot['x1']} {plot['ground_y']} {plot['z1']} "
        f"{plot['x1']} {plot['ground_y'] + 3} {plot['z2']} glass",
        f"fill {plot['x2']} {plot['ground_y']} {plot['z1']} "
        f"{plot['x2']} {plot['ground_y'] + 3} {plot['z2']} glass",
    ):
        command_with_retry(rcon, command)
    for x in range(plot["center_x"] - 2, plot["center_x"] + 2):
        for z in (plot["center_z"] - 1, plot["center_z"] + 1):
            command_with_retry(rcon, f"setblock {x} {plot['ground_y'] - 1} {z} sand")
            plot["cells"].append(
                {
                    "pos": (x, plot["ground_y"] + 1, z),
                    "source": (x, plot["ground_y"], z),
                    "empty_block": "water",
                }
            )


def _mushroom_plot(
    rcon: MCRcon, target: FarmingTargetSpec, plot: dict[str, Any], rng: random.Random
) -> None:
    del target, rng
    _set_floor(rcon, plot, "podzol")
    command_with_retry(
        rcon,
        f"fill {plot['x1']} {plot['ground_y'] + 4} {plot['z1']} "
        f"{plot['x2']} {plot['ground_y'] + 4} {plot['z2']} dark_oak_planks",
    )
    for z in (plot["center_z"] - 2, plot["center_z"], plot["center_z"] + 2):
        command_with_retry(
            rcon,
            f"setblock {plot['center_x'] - 1} {plot['ground_y'] - 1} {z} mycelium",
        )
        plot["cells"].append(
            {
                "pos": (plot["center_x"] + 1, plot["ground_y"], z),
                "source": (plot["center_x"] - 1, plot["ground_y"], z),
            }
        )


def _glow_berry_plot(
    rcon: MCRcon, target: FarmingTargetSpec, plot: dict[str, Any], rng: random.Random
) -> None:
    del target, rng
    _set_floor(rcon, plot, "grass_block")
    command_with_retry(
        rcon,
        f"fill {plot['x1']} {plot['ground_y'] + 3} {plot['z1']} "
        f"{plot['x2']} {plot['ground_y'] + 3} {plot['z2']} stone",
    )
    for x in range(plot["center_x"] - 2, plot["center_x"] + 2):
        for z in (plot["center_z"] - 1, plot["center_z"] + 1):
            command_with_retry(
                rcon,
                f"setblock {x} {plot['ground_y'] + 3} {z} moss_block",
            )
            plot["cells"].append({"pos": (x, plot["ground_y"] + 2, z)})


def _tree_plot(
    rcon: MCRcon, target: FarmingTargetSpec, plot: dict[str, Any], rng: random.Random
) -> None:
    del target, rng
    _set_floor(rcon, plot, "stone")
    for x in (plot["center_x"] - 2, plot["center_x"] + 2):
        for z in (plot["center_z"] - 2, plot["center_z"] + 2):
            command_with_retry(rcon, f"setblock {x} {plot['ground_y'] - 1} {z} dirt")
            plot["cells"].append(
                {"pos": (x, plot["ground_y"], z), "source": (x, plot["ground_y"], z)}
            )


_PLOT_BUILDERS = {
    "farmland": _age_plot,
    "water_edge": _water_edge_plot,
    "cactus": _cactus_plot,
    "bamboo": _bamboo_plot,
    "sweet_berries": _age_plot,
    "stem": _stem_plot,
    "cocoa": _cocoa_plot,
    "kelp": _kelp_plot,
    "mushroom": _mushroom_plot,
    "glow_berries": _glow_berry_plot,
    "tree": _tree_plot,
    "nether_wart": _age_plot,
}


def _summon_plot_markers(
    rcon: MCRcon,
    target: FarmingTargetSpec,
    plot: dict[str, Any],
) -> None:
    for cell_index, cell in enumerate(plot["cells"]):
        x, y, z = cell["pos"]
        cell_tag = f"nff_cell_{plot['index']:02d}_{cell_index:02d}"
        cell["tag"] = cell_tag
        nbt = f'{{Tags:["{MARKER_TAG}","{target.plot_tag}","{cell_tag}"]}}'
        response = rcon.command(f"summon minecraft:marker {x + 0.5} {y} {z + 0.5} {nbt}")
        if "unable" in response.lower() or "failed" in response.lower():
            raise RuntimeError(f"could not create farming marker: {response}")


def _fill_supply_barrel(
    rcon: MCRcon,
    mission_config: FarmingMissionConfig,
    hub: dict[str, int],
) -> None:
    supplies: Counter[str] = Counter()
    for target in mission_config.targets:
        supplies[target.starter_item] += target.starter_count
    supplies["stone_hoe"] = 1
    supplies["stone_axe"] = 1
    supplies["bucket"] = 1
    supplies["oak_log"] += mission_config.environment.utility_log_count

    bx, by, bz = hub["barrel_x"], hub["ground_y"], hub["barrel_z"]
    for slot, (item, count) in enumerate(sorted(supplies.items())):
        command_with_retry(
            rcon,
            f"item replace block {bx} {by} {bz} container.{slot} with minecraft:{item} {count}",
        )
