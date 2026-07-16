from __future__ import annotations

from npabench.evaluation.movement_monitor import MovementMonitor


def make_monitor(**kwargs) -> MovementMonitor:
    params = dict(
        host="127.0.0.1",
        rcon_port=25575,
        rcon_password="pw",
        username="agent",
    )
    params.update(kwargs)
    return MovementMonitor(**params)


def test_movement_monitor_allows_normal_surface_movement() -> None:
    monitor = make_monitor()

    monitor.record_sample(0.0, (0.0, 64.0, 0.0))
    monitor.record_sample(0.5, (2.0, 64.0, 0.0))
    monitor.record_sample(1.0, (4.0, 64.8, 0.0))

    report = monitor.report()
    assert report["violated"] is False
    assert report["violation_count"] == 0
    assert report["sample_count"] == 3


def test_single_fast_window_is_not_flagged() -> None:
    # An 8-block move in a 0.5s window is indistinguishable from a jittered legit sprint
    # once the latency budget is applied (8 / (0.5 + 1.0) = 5.3 b/s). Not a violation.
    monitor = make_monitor()

    monitor.record_sample(0.0, (0.0, 64.0, 0.0))
    monitor.record_sample(0.5, (8.0, 64.0, 0.0))

    report = monitor.report()
    assert report["violated"] is False
    assert report["violation_count"] == 0


def test_reported_legitimate_run_is_not_flagged() -> None:
    # Regression for the real false positive: a 7.47-block window at dt=0.51 read ~14.6 b/s
    # on the raw timer, but with the latency budget it is ~4.9 b/s — legitimate sprinting.
    monitor = make_monitor()

    monitor.record_sample(0.0, (10.188693262901932, 63.0, 69.9304466164962))
    monitor.record_sample(0.5100467780139297, (16.30358064788843, 63.0, 74.21400345138696))

    report = monitor.report()
    assert report["violated"] is False
    assert report["violation_count"] == 0


def test_slow_rcon_stall_does_not_false_positive() -> None:
    # A 3s gap between samples (slow RCON) covering 18 blocks is 6 b/s of legit sprinting,
    # not a teleport. The padded window keeps it well under the cap.
    monitor = make_monitor()

    monitor.record_sample(0.0, (0.0, 64.0, 0.0))
    monitor.record_sample(3.0, (18.0, 64.0, 0.0))

    report = monitor.report()
    assert report["violated"] is False
    assert report["violation_count"] == 0


def test_teleport_scale_movement_is_flagged() -> None:
    # Scout-ring / long-hop teleports move tens of blocks per window: impossible even after
    # latency padding (25 / 1.5 = 16.7 b/s). A couple of these trips the flag.
    monitor = make_monitor()

    for i in range(5):  # 4 transitions of +25 blocks / 0.5s
        monitor.record_sample(i * 0.5, (i * 25.0, 64.0, 0.0))

    report = monitor.report()
    assert report["violated"] is True
    assert report["violation_count"] >= monitor.min_violations
    assert report["violation"]["reason"] == "impossible_horizontal_movement"


def test_teleport_scale_upward_movement_is_flagged() -> None:
    monitor = make_monitor()

    for i in range(5):  # +12 blocks up / 0.5s -> 8 b/s after padding, impossible
        monitor.record_sample(i * 0.5, (0.0, 64.0 + i * 12.0, 0.0))

    report = monitor.report()
    assert report["violated"] is True
    assert report["violation"]["reason"] == "impossible_upward_movement"


def test_respawn_teleport_is_not_counted_as_violation() -> None:
    # Death respawns the player at spawn: a large legitimate teleport. When the death
    # counter increments across a transition, that transition is dropped.
    monitor = make_monitor()

    monitor.record_sample(0.0, (48.0, 64.0, 40.0), death_count=0)
    monitor.record_sample(0.5, (0.0, 64.0, 0.0), death_count=1)  # died -> respawn at spawn

    report = monitor.report()
    assert report["violated"] is False
    assert report["violation_count"] == 0
    assert report["respawns_skipped"] == 1


def test_repeated_deaths_do_not_accumulate_violations() -> None:
    monitor = make_monitor()

    deaths = 0
    t = 0.0
    monitor.record_sample(t, (0.0, 64.0, 0.0), death_count=deaths)
    for _ in range(6):
        # walk out a few normal steps, then take a fatal fall and respawn at spawn
        for step in range(1, 4):
            t += 0.5
            monitor.record_sample(t, (float(step * 2), 64.0, 0.0), death_count=deaths)
        deaths += 1
        t += 0.5
        monitor.record_sample(t, (0.0, 64.0, 0.0), death_count=deaths)

    report = monitor.report()
    assert report["violated"] is False
    assert report["violation_count"] == 0
    assert report["respawns_skipped"] == 6


def test_downward_drilling_is_not_flagged_known_gap() -> None:
    # KNOWN LIMITATION: only upward movement is bounded (fast descent is legal free-fall
    # from a position sample's view), so the teleport-down mining cheat is invisible to
    # this sampler. Documented here so the gap is explicit; the durable fix is a
    # server-side collision-aware plugin, not RCON position sampling.
    monitor = make_monitor()

    for i in range(10):  # drop 4 blocks straight down every 0.5s
        monitor.record_sample(i * 0.5, (0.0, 100.0 - i * 4.0, 0.0))

    report = monitor.report()
    assert report["violated"] is False
    assert report["violation_count"] == 0
