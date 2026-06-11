from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mcbench.agents import AgentSpec
from mcbench.competitions.resource_gathering import ResourceGatheringCompetition
from mcbench.competitions.resource_gathering.challenge import generate_challenge
from mcbench.competitions.resource_gathering.config import (
    ResourceCatalog,
    ResourceCatalogEntry,
    ResourceCompetitionConfig,
    ResourceTarget,
)
from mcbench.core.batch import _cleanup_slot_worlds, create_evaluation_batch
from mcbench.core.container import _write_biome_datapack


def _config_with_catalog(resources: dict, **kwargs) -> ResourceCompetitionConfig:
    return ResourceCompetitionConfig(catalog=ResourceCatalog(resources=resources), **kwargs)


class ResourceBatchTest(unittest.TestCase):
    def test_generate_challenge_is_deterministic(self) -> None:
        base = _config_with_catalog(
            {
                "logs": ResourceCatalogEntry(
                    items=["oak_log", "birch_log"], target_range=(16, 128)
                ),
                "coal": ResourceCatalogEntry(items=["coal"], target_range=(8, 64)),
            },
            duration_seconds=1200,
        )

        first = generate_challenge(base, seed=123)
        second = generate_challenge(base, seed=123)

        self.assertEqual(first, second)
        self.assertIn(first.resource, base.catalog.resources)
        self.assertIn(str(first.target_count), first.goal)
        self.assertIn("within 20 blocks of spawn", first.goal)

    def test_challenge_converts_to_single_target_run_config(self) -> None:
        base = _config_with_catalog(
            {
                "logs": ResourceCatalogEntry(
                    items=["oak_log", "birch_log"], target_range=(16, 16), points=100
                )
            },
            id="base",
            duration_seconds=1200,
        )
        challenge = generate_challenge(base, seed=1)

        cfg = challenge.to_run_config(base)

        self.assertEqual(cfg.id, challenge.challenge_id)
        self.assertEqual(cfg.seed, challenge.world_seed)
        self.assertEqual(cfg.goal, challenge.goal)
        self.assertEqual(len(cfg.resources), 1)
        self.assertEqual(cfg.resources[0].item, "logs")
        self.assertEqual(cfg.resources[0].items, ["oak_log", "birch_log"])
        self.assertEqual(cfg.resources[0].target_count, 16)
        # The catalog (the menu) is dropped from the per-run config.
        self.assertIsNone(cfg.catalog)

    def test_generate_challenge_requires_catalog(self) -> None:
        with self.assertRaises(ValueError):
            generate_challenge(ResourceCompetitionConfig(), seed=1)


class BundledConfigTest(unittest.TestCase):
    def test_bundled_config_loads_with_catalog(self) -> None:
        comp = ResourceGatheringCompetition()
        cfg = comp.load_config(comp.default_config_path())
        self.assertIsNotNone(cfg.catalog)
        self.assertIn("logs", cfg.catalog.resources)
        ch = comp.generate_challenge(cfg, seed=7)
        self.assertIn(ch.resource, cfg.catalog.resources)


class CleanupSlotWorldsTest(unittest.TestCase):
    def _batch(self, output_dir: Path):
        base = _config_with_catalog(
            {"logs": ResourceCatalogEntry(items=["oak_log"], target_range=(8, 8))}
        )
        return create_evaluation_batch(
            competition=ResourceGatheringCompetition(),
            base_cfg=base,
            agents=[AgentSpec(name="m", path="/tmp")],
            seed=1,
            output_dir=output_dir,
        )

    def test_removes_slot_copies_but_keeps_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "batch"
            batch = self._batch(out)

            # Disposable world copies...
            (out / "slots" / "slot-0" / "data").mkdir(parents=True)
            (out / "slots" / "slot-0" / "data" / "level.dat").write_text("x")
            (out / "template_slot" / "slot-999" / "data").mkdir(parents=True)
            # ...and the artifacts that must survive.
            (out / "world_template").mkdir(parents=True)
            (out / "world_template" / "level.dat").write_text("x")
            (out / "miners" / "m__slot0").mkdir(parents=True)
            (out / "miners" / "m__slot0" / "score.json").write_text("{}")

            _cleanup_slot_worlds(batch)

            self.assertFalse((out / "slots").exists())
            self.assertFalse((out / "template_slot").exists())
            self.assertTrue((out / "world_template" / "level.dat").exists())
            self.assertTrue((out / "miners" / "m__slot0" / "score.json").exists())

    def test_idempotent_when_nothing_to_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "batch"
            batch = self._batch(out)
            out.mkdir(parents=True)
            _cleanup_slot_worlds(batch)  # should not raise


class BiomePinningTest(unittest.TestCase):
    def test_biome_propagates_catalog_to_challenge_to_config(self) -> None:
        base = _config_with_catalog(
            {
                "logs": ResourceCatalogEntry(
                    items=["oak_log"], target_range=(8, 8), biome="minecraft:forest"
                )
            }
        )
        challenge = generate_challenge(base, seed=1)
        self.assertEqual(challenge.biome, "minecraft:forest")
        self.assertEqual(challenge.to_run_config(base).biome, "minecraft:forest")

    def test_no_biome_defaults_to_none(self) -> None:
        base = _config_with_catalog(
            {"coal": ResourceCatalogEntry(items=["coal"], target_range=(8, 8))}
        )
        challenge = generate_challenge(base, seed=1)
        self.assertIsNone(challenge.biome)
        self.assertIsNone(challenge.to_run_config(base).biome)

    def test_write_biome_datapack_creates_valid_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            _write_biome_datapack(data_dir, "minecraft:forest")

            dp = data_dir / "world" / "datapacks" / "mcbench_biome"
            mcmeta = dp / "pack.mcmeta"
            preset = dp / "data" / "mcbench" / "worldgen" / "world_preset" / "single_biome.json"
            self.assertTrue(mcmeta.exists())
            self.assertTrue(preset.exists())

            json.loads(mcmeta.read_text())  # valid JSON
            src = json.loads(preset.read_text())["dimensions"]["minecraft:overworld"][
                "generator"
            ]["biome_source"]
            self.assertEqual(src["type"], "minecraft:fixed")
            self.assertEqual(src["biome"], "minecraft:forest")


if __name__ == "__main__":
    unittest.main()
