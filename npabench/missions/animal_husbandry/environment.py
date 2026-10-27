import math
import time
import uuid

from npabench.minecraft.rcon_client import command_with_retry
from npabench.minecraft.rcon_helpers import parse_pos

from .config_schema import supply_kit
from .plugin import command, manifest_hash


def configure_world(rcon, config):
    for text in (
        "gamerule keep_inventory true",
        "gamerule random_tick_speed 3",
        "gamerule respawn_radius 0",
        "gamerule advance_weather false",
        "weather clear",
        "difficulty peaceful",
        f"time set {config.spawn_time}",
        "worldborder center 0 0",
        f"worldborder set {config.world_size}",
    ):
        command_with_retry(rcon, text)


def setup_agent(rcon, config):
    supplies = supply_kit(config)
    command(rcon, "health")
    position = None
    for _ in range(75):
        position = parse_pos(rcon.command(f"data get entity {config.username} Pos"))
        ground = rcon.command(f"data get entity {config.username} OnGround")
        if ground.strip().endswith("1b"):
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("natural spawn did not settle on the ground")
    if position is None:
        raise RuntimeError("cannot read natural player spawn")
    x, y, z = (math.floor(value) for value in position)
    # Only the two supply-chest cells are supported/cleared. No farm is prepared.
    for text in (
        f"clear {config.username}",
        f"effect clear {config.username}",
        f"fill {x + 2} {y - 1} {z} {x + 3} {y - 1} {z} dirt",
        f"fill {x + 2} {y} {z} {x + 3} {y + 1} {z} air",
        f"setblock {x + 2} {y} {z} chest[facing=north,type=left]",
        f"setblock {x + 3} {y} {z} chest[facing=north,type=right]",
        f"data merge block {x + 2} {y} {z} {{Items:[]}}",
        f"data merge block {x + 3} {y} {z} {{Items:[]}}",
        f"execute in minecraft:overworld run spawnpoint {config.username} {x} {y} {z}",
        f"execute in minecraft:overworld run tp {config.username} {x + 0.5} {y} {z + 0.5}",
        f"gamemode survival {config.username}",
        f"deop {config.username}",
    ):
        command_with_retry(rcon, text)
    stacks = [
        (item, min(64, count - start))
        for item, count in sorted(supplies.items())
        for start in range(0, count, 64)
    ]
    if len(stacks) > 54:
        raise RuntimeError("animal supplies exceed double chest")
    for i, (item, count) in enumerate(stacks):
        bx = x + 2 + i // 27
        command_with_retry(
            rcon,
            f"item replace block {bx} {y} {z} container.{i % 27} with minecraft:{item} {count}",
        )
    run_id = uuid.uuid4().hex
    result = command(rcon, "start " + run_id)
    return {
        "run_id": run_id,
        "spawn": [x, y, z],
        "supply_chest": [x + 2, y, z],
        "starter_crops": dict(config.starter_crops),
        "tracker": result,
        "manifest_sha256": manifest_hash(config),
    }
