"""Docker container lifecycle for one evaluation slot."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .models.competition import ResourceCompetitionConfig
from .paths import DOCKER_DIR
from .slot import CompetitionSlot

# world_preset id provided by the datapack we write for single-biome worlds.
SINGLE_BIOME_LEVEL_TYPE = "mcbench:single_biome"


def _write_biome_datapack(data_dir: Path, biome: str) -> None:
    """Write a world-preset datapack that pins the overworld to a single biome.

    Referenced by LEVEL_TYPE=mcbench:single_biome, it makes the whole overworld
    generate as `biome` over normal terrain (so trees/sand/etc. are guaranteed
    near spawn). Must exist before the world is generated; it then travels with
    the copied world to every slot. server.properties level-type is ignored once
    a world already exists, so only the template build actually consumes it.
    """
    preset_dir = data_dir / "world" / "datapacks" / "mcbench_biome"
    worldgen_dir = preset_dir / "data" / "mcbench" / "worldgen" / "world_preset"
    worldgen_dir.mkdir(parents=True, exist_ok=True)
    (preset_dir / "pack.mcmeta").write_text(
        json.dumps(
            {
                "pack": {
                    "pack_format": 48,
                    "description": "mcbench single-biome world",
                    "supported_formats": {"min_inclusive": 4, "max_inclusive": 999},
                }
            }
        )
    )
    preset = {
        "dimensions": {
            "minecraft:overworld": {
                "type": "minecraft:overworld",
                "generator": {
                    "type": "minecraft:noise",
                    "settings": "minecraft:overworld",
                    "biome_source": {"type": "minecraft:fixed", "biome": biome},
                },
            },
            "minecraft:the_nether": {
                "type": "minecraft:the_nether",
                "generator": {
                    "type": "minecraft:noise",
                    "settings": "minecraft:nether",
                    "biome_source": {"type": "minecraft:multi_noise", "preset": "minecraft:nether"},
                },
            },
            "minecraft:the_end": {
                "type": "minecraft:the_end",
                "generator": {
                    "type": "minecraft:noise",
                    "settings": "minecraft:end",
                    "biome_source": {"type": "minecraft:the_end"},
                },
            },
        }
    }
    (worldgen_dir / "single_biome.json").write_text(json.dumps(preset))


def _start_slot(
    slot: CompetitionSlot,
    cfg: ResourceCompetitionConfig,
    world_template: Path | None = None,
) -> None:
    _stop_slot(slot, quiet=True)
    shutil.rmtree(slot.data_dir, ignore_errors=True)
    if world_template is not None:
        if not world_template.exists():
            raise RuntimeError(f"world template does not exist: {world_template}")
        shutil.copytree(world_template, slot.data_dir)
    else:
        slot.data_dir.mkdir(parents=True, exist_ok=True)

    level_type = cfg.world_type
    if cfg.biome:
        level_type = SINGLE_BIOME_LEVEL_TYPE
        # Only a fresh build generates the world; slot copies already carry the
        # datapack + generated terrain from the template.
        if world_template is None:
            _write_biome_datapack(slot.data_dir, cfg.biome)

    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        slot.container_name,
        "-p",
        f"{slot.game_port}:25565",
        # RCON is the score oracle — publish it on loopback only so it is never
        # reachable off-host, and pair that with the per-slot random password.
        "-p",
        f"{slot.host}:{slot.rcon_port}:25575",
        "-v",
        f"{slot.data_dir}:/data",
        "-v",
        f"{DOCKER_DIR / 'bukkit.yml'}:/data/bukkit.yml:ro",
        "-e",
        "EULA=TRUE",
        "-e",
        "TYPE=PAPER",
        "-e",
        f"VERSION={cfg.minecraft_version}",
        "-e",
        f"MEMORY={cfg.memory}",
        "-e",
        "ONLINE_MODE=FALSE",
        "-e",
        "ENABLE_RCON=TRUE",
        "-e",
        f"RCON_PASSWORD={slot.rcon_password}",
        "-e",
        "RCON_PORT=25575",
        "-e",
        "MODE=survival",
        "-e",
        f"DIFFICULTY={cfg.difficulty}",
        "-e",
        f"LEVEL_TYPE={level_type}",
        "-e",
        f"GENERATE_STRUCTURES={str(cfg.generate_structures).upper()}",
        "-e",
        "SPAWN_PROTECTION=0",
        "-e",
        "VIEW_DISTANCE=10",
        "-e",
        "ALLOW_FLIGHT=TRUE",
        "-e",
        f"SEED={cfg.seed}",
        "itzg/minecraft-server:latest",
    ]
    _run(cmd, f"starting competition slot {slot.slot_id}")


def _stop_slot(slot: CompetitionSlot, quiet: bool = False) -> None:
    result = subprocess.run(
        ["docker", "rm", "-f", slot.container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and not quiet and "No such container" not in result.stderr:
        raise RuntimeError(
            f"docker rm -f {slot.container_name} failed\n"
            f"--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
        )


def _run(cmd: list[str], label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"{label} failed (exit {result.returncode})\n"
            f"command: {' '.join(cmd)}\n"
            f"--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
        )
    return result


