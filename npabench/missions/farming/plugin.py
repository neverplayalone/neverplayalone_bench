from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from npabench.missions.farming.config_schema import FarmingMissionConfig

PLUGIN_NAME = "NpaFarmingLedger"
PLUGIN_JAR = Path(__file__).resolve().parent / "plugin" / "npabench-farming-ledger.jar"


def install_farming_plugin(data_dir: Path, mission_config: FarmingMissionConfig) -> Path:
    """Install the event-based scorer before the agent's Paper server starts."""
    if not PLUGIN_JAR.is_file():
        raise RuntimeError(f"farming scorer plugin is missing: {PLUGIN_JAR}")
    plugin_dir = data_dir / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    installed = plugin_dir / PLUGIN_JAR.name
    shutil.copy2(PLUGIN_JAR, installed)
    config_dir = plugin_dir / PLUGIN_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "username": mission_config.username,
        "targets": [
            {
                "holder": target.harvest_holder,
                "planted_block": target.planted_block,
                "mature_block": target.mature_block,
            }
            for target in mission_config.targets
        ],
    }
    (config_dir / "config.yml").write_text(yaml.safe_dump(config, sort_keys=False))
    return installed
