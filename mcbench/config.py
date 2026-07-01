from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_repo_env(base_dir: Path | None = None) -> None:
    env_path = (base_dir or REPO_ROOT) / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


load_repo_env()

DOCKER_DIR = REPO_ROOT / "docker"
TOOLS_DIR = REPO_ROOT / "tools"
RECORDER_DIR = TOOLS_DIR / "recorder"
RESULTS_DIR = REPO_ROOT / "results"

DEFAULT_MINECRAFT_VERSION = "1.21.11"
DEFAULT_AGENT_USERNAME = "mcbench_agent"
DEFAULT_BASE_GAME_PORT = 25665
DEFAULT_BASE_RCON_PORT = 25675
DEFAULT_SANDBOX_MEMORY = "1g"
DEFAULT_SANDBOX_PIDS_LIMIT = 256
DEFAULT_RECORDER_USERNAME = "recorder_bot"

REPLAYMOD_FILE_FORMAT_VERSION = 14
MINECRAFT_PROTOCOL_TO_VERSION = {
    774: DEFAULT_MINECRAFT_VERSION,
}
