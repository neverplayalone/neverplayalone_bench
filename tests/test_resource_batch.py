from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mcbench.agents import AgentSpec
from mcbench.batch import (
    _cleanup_slot_worlds,
    create_evaluation_batch,
    generate_challenge,
)
from mcbench.container import _write_biome_datapack
from mcbench.models.challenge import ResourceCatalog, ResourceCatalogEntry
from mcbench.models.competition import ResourceCompetitionConfig, ResourceTarget


class ResourceBatchTest(unittest.TestCase):
    def test_generate_challenge_is_deterministic(self) -> None:
        catalog = ResourceCatalog(
            resources={
                "logs": ResourceCatalogEntry(
                    items=["oak_log", "birch_log"], target_range=(16, 128)
                ),
                "coal": ResourceCatalogEntry(items=["coal"], target_range=(8, 64)),
            }
        )
        base = ResourceCompetitionConfig(
            duration_seconds=1200,
            resources=[ResourceTarget(item="coal", target_count=8, points=100)],
        )

        first = generate_challenge(catalog, base, seed=123)
        second = generate_challenge(catalog, base, seed=123)

        self.assertEqual(first, second)
        self.assertIn(first.resource, catalog.resources)
        self.assertIn(str(first.target_count), first.goal)
        self.assertIn("within 20 blocks of spawn", first.goal)

    def test_challenge_converts_to_single_target_competition_config(self) -> None:
        catalog = ResourceCatalog(
            resources={
                "logs": ResourceCatalogEntry(
                    items=["oak_log", "birch_log"],
                    target_range=(16, 16),
                    points=100,
                )
            }
        )
        base = ResourceCompetitionConfig(
            id="base",
            duration_seconds=1200,
            resources=[ResourceTarget(item="coal", target_count=8, points=100)],
        )
        challenge = generate_challenge(catalog, base, seed=1)

        cfg = challenge.to_competition_config(base)

        self.assertEqual(cfg.id, challenge.challenge_id)
        self.assertEqual(cfg.seed, challenge.world_seed)
        self.assertEqual(cfg.goal, challenge.goal)
        self.assertEqual(len(cfg.resources), 1)
        self.assertEqual(cfg.resources[0].item, "logs")
        self.assertEqual(cfg.resources[0].items, ["oak_log", "birch_log"])
        self.assertEqual(cfg.resources[0].target_count, 16)


class CleanupSlotWorldsTest(unittest.TestCase):
    def _batch(self, output_dir: Path):
        catalog = ResourceCatalog(
            resources={"logs": ResourceCatalogEntry(items=["oak_log"], target_range=(8, 8))}
        )
        return create_evaluation_batch(
            catalog=catalog,
            base_cfg=ResourceCompetitionConfig(),
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
        catalog = ResourceCatalog(
            resources={
                "logs": ResourceCatalogEntry(
                    items=["oak_log"], target_range=(8, 8), biome="minecraft:forest"
                )
            }
        )
        base = ResourceCompetitionConfig()
        challenge = generate_challenge(catalog, base, seed=1)
        self.assertEqual(challenge.biome, "minecraft:forest")
        self.assertEqual(challenge.to_competition_config(base).biome, "minecraft:forest")

    def test_no_biome_defaults_to_none(self) -> None:
        catalog = ResourceCatalog(
            resources={"coal": ResourceCatalogEntry(items=["coal"], target_range=(8, 8))}
        )
        base = ResourceCompetitionConfig()
        challenge = generate_challenge(catalog, base, seed=1)
        self.assertIsNone(challenge.biome)
        self.assertIsNone(challenge.to_competition_config(base).biome)

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
