from __future__ import annotations

import unittest

from mcbench.competition import ResourceCompetitionConfig, ResourceTarget
from mcbench.resource_batch import (
    ResourceCatalog,
    ResourceCatalogEntry,
    generate_challenge,
)


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


if __name__ == "__main__":
    unittest.main()
