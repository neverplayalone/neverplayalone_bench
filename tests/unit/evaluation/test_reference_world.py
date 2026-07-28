from __future__ import annotations

import json

from npabench.config import DATAPACK_FORMAT
from npabench.evaluation.reference_world import write_biome_datapack


def test_biome_datapack_declares_the_format_fields_the_server_requires(tmp_path) -> None:
    """A malformed pack.mcmeta fails *silently*.

    1.21.11 rejects a pack that omits ``min_format`` / ``max_format`` ("missing
    mandatory fields"), logs it, drops the pack, and then falls back to default
    worldgen -- so the requested single-biome preset never applies and nothing
    raises. The only symptom is a world that is not the biome that was asked for.
    """
    write_biome_datapack(tmp_path, "minecraft:taiga")

    meta_path = tmp_path / "world" / "datapacks" / "npabench_biome" / "pack.mcmeta"
    pack = json.loads(meta_path.read_text())["pack"]

    assert pack["min_format"] == DATAPACK_FORMAT
    assert pack["max_format"] == DATAPACK_FORMAT
    # The pre-1.21.11 spelling is what the server refused; it must not come back.
    assert "supported_formats" not in pack


def test_biome_datapack_pins_the_requested_biome(tmp_path) -> None:
    write_biome_datapack(tmp_path, "minecraft:taiga")

    preset_path = (
        tmp_path / "world" / "datapacks" / "npabench_biome"
        / "data" / "npabench" / "worldgen" / "world_preset" / "single_biome.json"
    )
    preset = json.loads(preset_path.read_text())
    overworld = preset["dimensions"]["minecraft:overworld"]

    assert overworld["generator"]["biome_source"]["type"] == "minecraft:fixed"
    assert overworld["generator"]["biome_source"]["biome"] == "minecraft:taiga"
