from __future__ import annotations

import json
from pathlib import Path

from npabench.config import DATAPACK_FORMAT
from npabench.missions.farming.config_schema import FarmingMissionConfig

HARVEST_OBJECTIVE = "nff_harvest"
READY_OBJECTIVE = "nff_ready"
SYSTEM_OBJECTIVE = "nff_system"
MARKER_TAG = "nff_farm_marker"
PACK_SCORE_HOLDER = "#pack"


def write_farming_datapack(data_dir: Path, mission_config: FarmingMissionConfig) -> Path:
    """Install tick-level, plot-scoped mature-harvest tracking."""
    pack_dir = data_dir / "world" / "datapacks" / mission_config.environment.datapack_name
    function_dir = pack_dir / "data" / "npabench" / "function" / "farming"
    tag_dir = pack_dir / "data" / "minecraft" / "tags" / "function"
    function_dir.mkdir(parents=True, exist_ok=True)
    tag_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        pack_dir / "pack.mcmeta",
        {
            "pack": {
                "description": "NPABench plot-scoped farming harvest tracker",
                "min_format": DATAPACK_FORMAT,
                "max_format": DATAPACK_FORMAT,
            }
        },
    )
    _write_json(tag_dir / "load.json", {"values": ["npabench:farming/load"]})
    _write_json(tag_dir / "tick.json", {"values": ["npabench:farming/tick"]})

    load_lines = [
        f"scoreboard objectives add {HARVEST_OBJECTIVE} dummy",
        f"scoreboard objectives add {READY_OBJECTIVE} dummy",
        f"scoreboard objectives add {SYSTEM_OBJECTIVE} dummy",
        f"scoreboard players set {PACK_SCORE_HOLDER} {SYSTEM_OBJECTIVE} 1",
    ]
    (function_dir / "load.mcfunction").write_text("\n".join(load_lines) + "\n")

    tick_lines: list[str] = []
    for target in mission_config.targets:
        selector = f"@e[type=minecraft:marker,tag={MARKER_TAG},tag={target.plot_tag}"
        block = f"minecraft:{target.mature_block}"
        if target.harvest_mode == "mature_state":
            tick_lines.append(
                f"execute as {selector},scores={{{READY_OBJECTIVE}=0}}] at @s "
                f"if block ~ ~ ~ {block} run scoreboard players set @s "
                f"{READY_OBJECTIVE} 1"
            )
        ready_selector = f"{selector},scores={{{READY_OBJECTIVE}=1}}]"
        tick_lines.extend(
            [
                f"execute as {ready_selector} at @s unless block ~ ~ ~ {block} run "
                f"scoreboard players add {target.harvest_holder} {HARVEST_OBJECTIVE} 1",
                f"execute as {ready_selector} at @s unless block ~ ~ ~ {block} run "
                f"scoreboard players set @s {READY_OBJECTIVE} 0",
            ]
        )
    (function_dir / "tick.mcfunction").write_text("\n".join(tick_lines) + "\n")
    return pack_dir


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, separators=(",", ":")))
