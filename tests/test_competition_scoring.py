from __future__ import annotations

import time
import unittest

from mcbench.competition import (
    CompetitionScoringConfig,
    KitItem,
    ResourceCompetitionConfig,
    ResourceTarget,
    _kit_item_stack,
    _prepare_playable_spawn,
    score_resource_gathering,
)
from mcbench.trace import FinalState, Trace, TraceEvent


def _config() -> ResourceCompetitionConfig:
    return ResourceCompetitionConfig(
        id="test_gather",
        duration_seconds=1200,
        resources=[
            ResourceTarget(item="oak_log", target_count=64, points=100),
        ],
        scoring=CompetitionScoringConfig(
            survival_points=50,
            efficiency_points=50,
            efficiency_min_resource_score=100,
        ),
    )


class FakeRcon:
    def __init__(self):
        self.commands: list[str] = []
        self.blocks: dict[tuple[int, int, int], str] = {}

    def command(self, command: str) -> str:
        self.commands.append(command)
        if command == "data get entity BenchmarkBot Pos":
            return "BenchmarkBot has the following entity data: [12.3d, 72.0d, -8.8d]"
        if command.startswith("execute if block "):
            parts = command.split()
            pos = (int(parts[3]), int(parts[4]), int(parts[5]))
            block = parts[6]
            return "The time is 12345" if self.blocks.get(pos, "minecraft:air") == block else "Test failed"
        return ""


class CompetitionScoringTest(unittest.TestCase):
    def test_resource_config_defaults_to_minecraft_1_21_11(self) -> None:
        cfg = _config()

        self.assertEqual(cfg.minecraft_version, "1.21.11")
        self.assertEqual(cfg.world_type, "normal")

    def test_kit_item_stack_uses_1_21_item_components(self) -> None:
        item = _kit_item_stack(
            KitItem(
                item="netherite_pickaxe",
                enchantments=["efficiency:5", "unbreaking:3", "fortune:3"],
            )
        )

        self.assertEqual(
            item,
            'minecraft:netherite_pickaxe[minecraft:enchantments={"minecraft:efficiency":5,'
            '"minecraft:unbreaking":3,"minecraft:fortune":3}]',
        )

    def test_target_count_scoring_caps_logical_resource_group(self) -> None:
        cfg = ResourceCompetitionConfig(
            id="target_logs",
            duration_seconds=1200,
            resources=[
                ResourceTarget(
                    item="logs",
                    items=["oak_log", "birch_log"],
                    target_count=10,
                    points=100,
                )
            ],
            scoring=CompetitionScoringConfig(
                survival_points=0,
                efficiency_points=0,
                efficiency_min_resource_score=100,
            ),
        )
        trace = Trace(challenge_id=cfg.id, agent_name="agent", started_at=time.time() - 1200)
        trace.ended_at = time.time()
        trace.timed_out = True
        trace.final_state = FinalState(inventory={"oak_log": 3, "birch_log": 2}, health=20)

        report = score_resource_gathering(cfg, trace, {"alive": True, "deaths": 0})

        self.assertEqual(report["resource_score"], 50)
        self.assertEqual(report["resources"][0]["count"], 5)

        trace.final_state.inventory = {"oak_log": 30}
        report = score_resource_gathering(cfg, trace, {"alive": True, "deaths": 0})

        self.assertEqual(report["resource_score"], 100)
        self.assertEqual(report["resources"][0]["count"], 30)

    def test_playable_spawn_teleports_without_placing_block(self) -> None:
        mcr = FakeRcon()
        mcr.blocks[(12, 71, -9)] = "minecraft:grass_block"

        spawn_pos = _prepare_playable_spawn(mcr, "BenchmarkBot")

        self.assertEqual(spawn_pos, (12, 72, -9))
        self.assertIn("gamerule spawnRadius 0", mcr.commands)
        self.assertIn("setworldspawn 12 72 -9", mcr.commands)
        self.assertIn("spawnpoint BenchmarkBot 12 72 -9", mcr.commands)
        self.assertIn("tp BenchmarkBot 12.5 72 -8.5 0 0", mcr.commands)
        self.assertFalse(any(command.startswith("setblock ") for command in mcr.commands))

    def test_scores_target_count_and_survival(self) -> None:
        cfg = _config()
        trace = Trace(challenge_id=cfg.id, agent_name="agent", started_at=time.time() - 1200)
        trace.ended_at = time.time()
        trace.timed_out = True
        trace.final_state = FinalState(inventory={"oak_log": 32}, health=20)

        report = score_resource_gathering(cfg, trace, {"alive": True, "deaths": 0})

        self.assertEqual(report["resource_score"], 50)
        self.assertEqual(report["survival_score"], 50)
        self.assertEqual(report["efficiency_score"], 0)
        self.assertEqual(report["score"], 100)

    def test_resources_do_not_score_when_agent_finishes_far_from_spawn(self) -> None:
        cfg = _config()
        trace = Trace(challenge_id=cfg.id, agent_name="agent", started_at=time.time() - 1200)
        trace.ended_at = time.time()
        trace.timed_out = True
        trace.final_state = FinalState(inventory={"oak_log": 64}, health=20)

        report = score_resource_gathering(
            cfg,
            trace,
            {
                "alive": True,
                "deaths": 0,
                "distance_from_spawn": 21.0,
                "return_radius": 20.0,
                "within_return_radius": False,
            },
        )

        self.assertEqual(report["resources"][0]["count"], 64)
        self.assertEqual(report["resources"][0]["achieved"], 0)
        self.assertEqual(report["resource_score"], 0)
        self.assertFalse(report["within_return_radius"])

    def test_efficiency_bonus_requires_early_done_and_resource_floor(self) -> None:
        cfg = _config()
        trace = Trace(challenge_id=cfg.id, agent_name="agent", started_at=time.time() - 300)
        trace.ended_at = time.time()
        trace.final_state = FinalState(inventory={"oak_log": 32}, health=20)
        trace.append(TraceEvent(kind="done", data={}))

        report = score_resource_gathering(cfg, trace, {"alive": True, "deaths": 0})

        self.assertEqual(report["resource_score"], 50)
        self.assertEqual(report["efficiency_score"], 0)

        trace.final_state.inventory["oak_log"] = 64
        report = score_resource_gathering(cfg, trace, {"alive": True, "deaths": 0})

        self.assertEqual(report["resource_score"], 100)
        self.assertGreater(report["efficiency_score"], 0)


if __name__ == "__main__":
    unittest.main()
