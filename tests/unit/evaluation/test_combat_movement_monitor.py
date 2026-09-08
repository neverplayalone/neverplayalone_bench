from npabench.evaluation.run_slot import ServerEndpoint
from npabench.evaluation.single_runner import _create_movement_monitor
from npabench.missions.base import MissionConfig
from npabench.missions.combat.config_schema import CombatMissionConfig


def test_combat_monitor_uses_combat_death_objective_for_custom_task_ids() -> None:
    monitor = _create_movement_monitor(
        ServerEndpoint(), CombatMissionConfig(id="custom-fight-task")
    )
    assert monitor.deaths_objective == "ncb_deaths"
    monitor.record_sample(0, (0, 64, 0), death_count=0)
    monitor.record_sample(0.5, (100, 80, 100), death_count=1)
    assert monitor.report()["respawns_skipped"] == 1
    assert not monitor.violated
    monitor.record_sample(1, (200, 80, 200), death_count=1)
    monitor.record_sample(1.5, (300, 80, 300), death_count=1)
    assert monitor.violated


def test_other_missions_keep_their_existing_death_objective() -> None:
    monitor = _create_movement_monitor(ServerEndpoint(), MissionConfig(id="combat-looking-id"))
    assert monitor.deaths_objective == "mcb_deaths"
