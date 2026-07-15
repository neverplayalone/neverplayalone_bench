from __future__ import annotations

from npabench.evaluation.movement_monitor import MovementMonitor


def make_monitor() -> MovementMonitor:
    return MovementMonitor(
        host="127.0.0.1",
        rcon_port=25575,
        rcon_password="pw",
        username="agent",
    )


def test_movement_monitor_allows_normal_surface_movement() -> None:
    monitor = make_monitor()

    monitor.record_sample(0.0, (0.0, 64.0, 0.0))
    monitor.record_sample(0.5, (2.0, 64.0, 0.0))
    monitor.record_sample(1.0, (4.0, 64.8, 0.0))

    report = monitor.report()
    assert report["violated"] is False
    assert report["sample_count"] == 3


def test_movement_monitor_flags_fast_horizontal_jump() -> None:
    monitor = make_monitor()

    monitor.record_sample(0.0, (0.0, 64.0, 0.0))
    monitor.record_sample(0.5, (8.0, 64.0, 0.0))

    report = monitor.report()
    assert report["violated"] is True
    assert report["violation"]["reason"] == "impossible_horizontal_movement"


def test_movement_monitor_flags_fast_upward_jump() -> None:
    monitor = make_monitor()

    monitor.record_sample(0.0, (0.0, 64.0, 0.0))
    monitor.record_sample(0.5, (0.0, 68.0, 0.0))

    report = monitor.report()
    assert report["violated"] is True
    assert report["violation"]["reason"] == "impossible_upward_movement"
