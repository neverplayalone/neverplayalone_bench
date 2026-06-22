from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from rich.console import Console

from mcbench.config import DOCKER_DIR
from mcbench.evaluation.run_slot import AgentRunSlot
from mcbench.minecraft.rcon_client import rcon_session
from mcbench.minecraft.server_probe import wait_for_ready
from mcbench.missions.base import Mission, MissionConfig

console = Console()

SINGLE_BIOME_WORLD_PRESET = "mcbench:single_biome"


def write_biome_datapack(data_dir: Path, biome: str) -> None:
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


def start_agent_run_slot(
    agent_run_slot: AgentRunSlot,
    mission_config: MissionConfig,
    reference_world_dir: Path | None = None,
) -> None:
    stop_agent_run_slot(agent_run_slot, quiet=True)
    shutil.rmtree(agent_run_slot.data_dir, ignore_errors=True)
    if reference_world_dir is not None:
        if not reference_world_dir.exists():
            raise RuntimeError(f"reference world does not exist: {reference_world_dir}")
        shutil.copytree(reference_world_dir, agent_run_slot.data_dir)
    else:
        agent_run_slot.data_dir.mkdir(parents=True, exist_ok=True)

    level_type = mission_config.world_type
    if mission_config.biome:
        level_type = SINGLE_BIOME_WORLD_PRESET
        if reference_world_dir is None:
            write_biome_datapack(agent_run_slot.data_dir, mission_config.biome)

    env = {
        "EULA": "TRUE",
        "TYPE": "PAPER",
        "VERSION": mission_config.minecraft_version,
        "MEMORY": mission_config.memory,
        "ONLINE_MODE": "FALSE",
        "ENABLE_RCON": "TRUE",
        "RCON_PASSWORD": agent_run_slot.rcon_password,
        "RCON_PORT": "25575",
        "MODE": "survival",
        "DIFFICULTY": mission_config.difficulty,
        "LEVEL_TYPE": level_type,
        "GENERATE_STRUCTURES": str(mission_config.generate_structures).upper(),
        "SPAWN_PROTECTION": "0",
        "VIEW_DISTANCE": "10",
        "ALLOW_FLIGHT": "TRUE",
        "SEED": str(mission_config.seed),
    }

    command = ["docker", "run", "-d", "--name", agent_run_slot.container_name]
    command += ["-p", f"{agent_run_slot.game_port}:25565"]
    command += ["-p", f"{agent_run_slot.host}:{agent_run_slot.rcon_port}:25575"]
    command += ["-v", f"{agent_run_slot.data_dir}:/data"]
    command += ["-v", f"{DOCKER_DIR / 'bukkit.yml'}:/data/bukkit.yml:ro"]
    for key, value in env.items():
        command += ["-e", f"{key}={value}"]
    command += ["itzg/minecraft-server:latest"]
    try:
        ensure_run_slot_network(agent_run_slot)
        run_docker_command(command, f"starting task slot {agent_run_slot.slot_id}")
        run_docker_command(
            ["docker", "network", "connect", agent_run_slot.network_name, agent_run_slot.container_name],
            f"connecting task slot {agent_run_slot.slot_id} to dedicated network",
        )
    except Exception:
        stop_agent_run_slot(agent_run_slot, quiet=True)
        raise


def stop_agent_run_slot(agent_run_slot: AgentRunSlot, quiet: bool = False) -> None:
    result = subprocess.run(
        ["docker", "rm", "-f", agent_run_slot.container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and not quiet and not docker_resource_missing(result.stderr):
        raise RuntimeError(
            f"docker rm -f {agent_run_slot.container_name} failed\n"
            f"--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
        )
    network_result = subprocess.run(
        ["docker", "network", "rm", agent_run_slot.network_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if (
        network_result.returncode != 0
        and not quiet
        and not docker_resource_missing(network_result.stderr)
    ):
        raise RuntimeError(
            f"docker network rm {agent_run_slot.network_name} failed\n"
            f"--- stderr ---\n{network_result.stderr}\n--- stdout ---\n{network_result.stdout}"
        )


def ensure_run_slot_network(agent_run_slot: AgentRunSlot) -> None:
    result = subprocess.run(
        ["docker", "network", "inspect", agent_run_slot.network_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    run_docker_command(
        ["docker", "network", "create", "--internal", agent_run_slot.network_name],
        f"creating dedicated network for slot {agent_run_slot.slot_id}",
    )


def cleanup_run_worlds(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def run_docker_command(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"{label} failed (exit {result.returncode})\n"
            f"command: {' '.join(command)}\n"
            f"--- stderr ---\n{result.stderr}\n--- stdout ---\n{result.stdout}"
        )
    return result


def docker_resource_missing(stderr: str) -> bool:
    message = stderr.lower()
    return "no such" in message or "not found" in message


class ReferenceWorldBuilder:
    def build(
        self,
        mission: Mission,
        mission_config: MissionConfig,
        output_dir: Path,
        *,
        base_game_port: int,
        base_rcon_port: int,
    ) -> Path:
        output_dir = output_dir.resolve()
        cleanup_run_worlds(output_dir)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        builder_slot = AgentRunSlot.allocate(
            slot_id=0,
            base_game_port=base_game_port,
            base_rcon_port=base_rcon_port,
            container_prefix="mcbench-template",
            data_root=output_dir.parent / "_builder",
        )
        start_agent_run_slot(builder_slot, mission_config)
        try:
            server_endpoint = builder_slot.server_endpoint()
            wait_for_ready(server_endpoint, timeout=600)
            with rcon_session(
                server_endpoint.host,
                server_endpoint.rcon_port,
                server_endpoint.rcon_password,
                socket_timeout=20,
            ) as rcon:
                mission.configure_world(rcon, mission_config)
                rcon.command("save-all flush")
        finally:
            stop_agent_run_slot(builder_slot, quiet=True)
        shutil.copytree(builder_slot.data_dir, output_dir)
        cleanup_run_worlds(builder_slot.data_root)
        console.log(f"Reference world ready: {output_dir}")
        return output_dir
