import base64
import hashlib
import json
import shutil
from pathlib import Path

import yaml

PLUGIN_DIR = Path(__file__).parent / "plugin"
PLUGIN_JAR = PLUGIN_DIR / "npabench-animal-husbandry.jar"
PREFIX = "NPA_ANIMALS "
PLUGIN_VERSION = "4.0.0"


def manifest_hash(config):
    raw = json.dumps(config.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def install_plugin(data_dir, config):
    if not PLUGIN_JAR.is_file():
        raise RuntimeError("animal husbandry plugin missing; run plugin/build.sh first")
    plugins = data_dir / "plugins"
    plugins.mkdir(exist_ok=True)
    shutil.copy2(PLUGIN_JAR, plugins / PLUGIN_JAR.name)
    settings = plugins / "NpaAnimalHusbandry"
    settings.mkdir(exist_ok=True)
    (settings / "config.yml").write_text(
        yaml.safe_dump(
            {
                "manifest_sha256": manifest_hash(config),
                "username": config.username,
                "world": "world",
                "targets": [t.key for t in config.targets],
                "starter_babies_per_species": config.starter_babies_per_species,
                "min_farm_size": config.min_farm_size,
                "max_farm_size": config.max_farm_size,
            }
        )
    )


def command(rcon, text):
    response = rcon.command("npaanimals " + text)
    if PREFIX not in response:
        raise RuntimeError(f"animal tracker failed: {response[:300]}")
    value = json.loads(response.split(PREFIX, 1)[1].strip())
    if value.get("status") != "ok":
        raise RuntimeError(f"animal tracker error: {value}")
    return value


def snapshot(rcon, run_id):
    header = command(rcon, f"close {run_id}")
    pages = header.get("pages")
    if not isinstance(pages, int) or not 1 <= pages <= 10000:
        raise RuntimeError("invalid animal snapshot page count")
    encoded = "".join(command(rcon, f"read {run_id} {i}")["data"] for i in range(pages))
    raw = base64.b64decode(encoded, validate=True)
    if hashlib.sha256(raw).hexdigest() != header.get("sha256"):
        raise RuntimeError("animal snapshot checksum mismatch")
    result = json.loads(raw)
    if result.get("run_id") != run_id or result.get("plugin_version") != PLUGIN_VERSION:
        raise RuntimeError("animal snapshot identity mismatch")
    return result
