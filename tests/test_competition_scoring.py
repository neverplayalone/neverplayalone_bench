from __future__ import annotations

import time
import unittest

from mcbench.competition import (
    CompetitionScoringConfig,
    KitItem,
    ResourceCompetitionConfig,
    ResourceTarget,
    _distance_multiplier,
    _kit_item_stack,
    _prepare_playable_spawn,
    score_resource_gathering,
)
from mcbench.trace import FinalState, Trace, TraceEvent


def _config(duration_seconds: int = 1200) -> ResourceCompetitionConfig:
    return ResourceCompetitionConfig(
        id="test_gather",
        duration_seconds=duration_seconds,
        resources=[
            ResourceTarget(item="oak_log", target_count=64, points=100),
        ],
        scoring=CompetitionScoringConfig(),  # default band table
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


BANDS = [(20.0, 1.0), (50.0, 0.9), (80.0, 0.8), (110.0, 0.7), (140.0, 0.6)]


class DistanceMultiplierTest(unittest.TestCase):
    def test_full_credit_within_radius(self) -> None:
        self.assertEqual(_distance_multiplier(0, BANDS, 0.5), 1.0)
        self.assertEqual(_distance_multiplier(20, BANDS, 0.5), 1.0)

    def test_bands_apply_at_or_below_bound(self) -> None:
        self.assertEqual(_distance_multiplier(35, BANDS, 0.5), 0.9)
        self.assertEqual(_distance_multiplier(50, BANDS, 0.5), 0.9)
        self.assertEqual(_distance_multiplier(80, BANDS, 0.5), 0.8)
        self.assertEqual(_distance_multiplier(110, BANDS, 0.5), 0.7)
        self.assertEqual(_distance_multiplier(140, BANDS, 0.5), 0.6)

    def test_beyond_last_band_is_floor(self) -> None:
        self.assertEqual(_distance_multiplier(141, BANDS, 0.5), 0.5)
        self.assertEqual(_distance_multiplier(5000, BANDS, 0.5), 0.5)

    def test_unknown_distance_is_floor(self) -> None:
        self.assertEqual(_distance_multiplier(None, BANDS, 0.5), 0.5)


class CompetitionScoringTest(unittest.TestCase):
    def test_resource_config_defaults_to_minecraft_1_21_11(self) -> None:
        cfg = _config()

        self.assertEqual(cfg.minecraft_version, "1.21.11")
        self.assertEqual(cfg.world_type, "normal")

    def test_kit_item_stack_defaults_to_plain_item_for_agent_compatibility(self) -> None:
        item = _kit_item_stack(KitItem(item="netherite_pickaxe"))

        self.assertEqual(item, "minecraft:netherite_pickaxe")

    def test_kit_item_stack_can_still_encode_enchantments_for_custom_configs(self) -> None:
        item = _kit_item_stack(KitItem(item="netherite_pickaxe", enchantments=["efficiency:5"]))

        self.assertEqual(
            item,
            'minecraft:netherite_pickaxe[minecraft:enchantments={"minecraft:efficiency":5}]',
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

    def test_score_is_resource_times_distance_multiplier_at_spawn(self) -> None:
        cfg = _config()
        trace = Trace(challenge_id=cfg.id, agent_name="agent", started_at=time.time() - 1200)
        trace.ended_at = time.time()
        trace.timed_out = True
        trace.final_state = FinalState(inventory={"oak_log": 64}, health=20)

        report = score_resource_gathering(
            cfg, trace, {"alive": True, "deaths": 0, "distance_from_spawn": 10.0}
        )

        self.assertEqual(report["resource_score"], 100)
        self.assertEqual(report["distance_multiplier"], 1.0)
        self.assertEqual(report["score"], 100)
        self.assertNotIn("survival_score", report)
        self.assertNotIn("efficiency_score", report)
        self.assertNotIn("distance_score", report)

    def test_distance_multiplier_reduces_score_when_far(self) -> None:
        cfg = _config()
        trace = Trace(challenge_id=cfg.id, agent_name="agent", started_at=time.time() - 1200)
        trace.ended_at = time.time()
        trace.timed_out = True
        trace.final_state = FinalState(inventory={"oak_log": 64}, health=20)

        report = score_resource_gathering(
            cfg, trace, {"alive": True, "deaths": 0, "distance_from_spawn": 50.0}
        )

        # full resources, 0.9 multiplier at 50 blocks
        self.assertEqual(report["resource_score"], 100)
        self.assertAlmostEqual(report["distance_multiplier"], 0.9)
        self.assertAlmostEqual(report["score"], 90)

    def test_resources_never_zero_out_far_away(self) -> None:
        cfg = _config()
        trace = Trace(challenge_id=cfg.id, agent_name="agent", started_at=time.time() - 1200)
        trace.ended_at = time.time()
        trace.timed_out = True
        trace.final_state = FinalState(inventory={"oak_log": 64}, health=20)

        report = score_resource_gathering(
            cfg, trace, {"alive": True, "deaths": 0, "distance_from_spawn": 9999.0}
        )

        self.assertEqual(report["distance_multiplier"], 0.5)
        self.assertEqual(report["score"], 50)  # 100 * 0.5 floor

    def test_partial_resources_scale(self) -> None:
        cfg = _config()
        trace = Trace(challenge_id=cfg.id, agent_name="agent", started_at=time.time() - 1200)
        trace.ended_at = time.time()
        trace.timed_out = True
        trace.final_state = FinalState(inventory={"oak_log": 32}, health=20)

        report = score_resource_gathering(
            cfg, trace, {"alive": True, "deaths": 0, "distance_from_spawn": 10.0}
        )

        self.assertEqual(report["resource_score"], 50)
        self.assertEqual(report["score"], 50)

    def test_time_efficiency_is_tiebreaker_not_score(self) -> None:
        cfg = _config()
        # finished early with full resources, far from spawn
        trace = Trace(challenge_id=cfg.id, agent_name="agent", started_at=time.time() - 300)
        trace.ended_at = time.time()
        trace.final_state = FinalState(inventory={"oak_log": 64}, health=20)
        trace.append(TraceEvent(kind="done", data={}))

        report = score_resource_gathering(
            cfg, trace, {"alive": True, "deaths": 0, "distance_from_spawn": 10.0}
        )

        # score depends only on resources * distance, not on time
        self.assertEqual(report["score"], 100)
        # but time_efficiency is reported for tie-breaking: ~ (1200 - 300) / 1200
        self.assertGreater(report["time_efficiency"], 0)
        self.assertAlmostEqual(report["time_efficiency"], 0.75, delta=0.02)

    def test_time_efficiency_zero_when_timed_out(self) -> None:
        cfg = _config()
        trace = Trace(challenge_id=cfg.id, agent_name="agent", started_at=time.time() - 1200)
        trace.ended_at = time.time()
        trace.timed_out = True
        trace.final_state = FinalState(inventory={"oak_log": 64}, health=20)

        report = score_resource_gathering(
            cfg, trace, {"alive": True, "deaths": 0, "distance_from_spawn": 10.0}
        )

        self.assertEqual(report["time_efficiency"], 0.0)

    def test_time_efficiency_clock_excludes_boot_via_agent_ready_at(self) -> None:
        cfg = _config(duration_seconds=200)
        now = time.time()
        trace = Trace(
            challenge_id=cfg.id,
            agent_name="agent",
            started_at=now - 500,      # 400s of boot/load before the agent spawned
            agent_ready_at=now - 100,  # agent actually played for 100s
        )
        trace.ended_at = now
        trace.final_state = FinalState(inventory={"oak_log": 64}, health=20)
        trace.append(TraceEvent(kind="done", data={}))

        report = score_resource_gathering(
            cfg, trace, {"alive": True, "deaths": 0, "distance_from_spawn": 10.0}
        )

        self.assertAlmostEqual(report["elapsed_seconds"], 100, delta=1)
        self.assertAlmostEqual(report["time_efficiency"], 0.5, delta=0.02)


if __name__ == "__main__":
    unittest.main()
