from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from npabench.minecraft.rcon_client import command_with_retry, rcon_session
from npabench.minecraft.rcon_helpers import parse_pos

DEFAULT_SAMPLE_INTERVAL_SECONDS = 0.5
MAX_HORIZONTAL_SPEED_BLOCKS_PER_SECOND = 9.0
MAX_UPWARD_SPEED_BLOCKS_PER_SECOND = 6.0

# Position is sampled over RCON and timestamped at response-receipt, not at server-tick
# time. RCON latency jitter means the wall-clock gap between two samples (dt) can
# UNDER-measure the true elapsed time between the two server-side positions, which
# inflates apparent speed — a legitimate ~1s sprint compressed into a 0.5s window reads
# as ~15 b/s (the observed false positive). The DISTANCE moved is real; only the TIME is
# unreliable. So we divide displacement by a latency-padded window: jitter can no longer
# manufacture an impossible speed, while a real teleport (e.g. a 50-block scout hop) is
# still tens of b/s after padding and is caught. Padding covers RCON round-trip swings up
# to ~2x this value between consecutive reads.
LATENCY_BUDGET_SECONDS = 1.0

# With latency padding, a legitimate window can no longer look impossible, so this is a
# glitch-guard (absorb a lone freak RCON position read), NOT a cheat allowance: the
# teleport cheat produces dozens of impossible windows (every hop + 8-way scout ring), so
# it still trips almost immediately, while an honest run produces zero.
MIN_VIOLATIONS_TO_FLAG = 2
DEFAULT_DEATHS_OBJECTIVE = "mcb_deaths"


@dataclass(frozen=True)
class MovementSample:
    t: float
    position: tuple[float, float, float]


class MovementMonitor:
    """Sample server-side position and flag movement impossible for survival play."""

    def __init__(
        self,
        *,
        host: str,
        rcon_port: int,
        rcon_password: str,
        username: str,
        sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
        min_violations: int = MIN_VIOLATIONS_TO_FLAG,
        deaths_objective: str = DEFAULT_DEATHS_OBJECTIVE,
    ) -> None:
        self.host = host
        self.rcon_port = rcon_port
        self.rcon_password = rcon_password
        self.username = username
        self.sample_interval_seconds = sample_interval_seconds
        self.min_violations = max(1, min_violations)
        self.deaths_objective = deaths_objective
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._stopped_at: float | None = None
        self._last_sample: MovementSample | None = None
        self._last_death_count: int | None = None
        self._sample_count = 0
        self._error_count = 0
        self._errors: list[str] = []
        self._violation: dict[str, Any] | None = None
        self._violation_count = 0
        self._respawns_skipped = 0
        self._max_horizontal_delta = 0.0
        self._max_horizontal_speed = 0.0
        self._max_upward_delta = 0.0
        self._max_upward_speed = 0.0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._run, name="npabench-movement-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.sample_interval_seconds * 4))
        self._stopped_at = time.time()
        return self.report()

    @property
    def violated(self) -> bool:
        return self._violation_count >= self.min_violations

    def record_sample(
        self,
        sampled_at: float,
        position: tuple[float, float, float],
        death_count: int | None = None,
    ) -> None:
        sample = MovementSample(sampled_at, position)
        with self._lock:
            previous = self._last_sample
            previous_deaths = self._last_death_count
            self._last_sample = sample
            self._sample_count += 1
            if death_count is not None:
                self._last_death_count = death_count
            if previous is None:
                return
            # A death respawns the player at spawn — that teleport is legitimate,
            # not cheating. Drop the transition that straddles the respawn.
            if (
                death_count is not None
                and previous_deaths is not None
                and death_count > previous_deaths
            ):
                self._respawns_skipped += 1
                return
            self._check_transition(previous, sample)

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": True,
                "violated": self._violation_count >= self.min_violations,
                "violation": self._violation,
                "violation_count": self._violation_count,
                "min_violations": self.min_violations,
                "respawns_skipped": self._respawns_skipped,
                "sample_count": self._sample_count,
                "error_count": self._error_count,
                "errors": list(self._errors),
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "sample_interval_seconds": self.sample_interval_seconds,
                "thresholds": {
                    "max_horizontal_speed_blocks_per_second": MAX_HORIZONTAL_SPEED_BLOCKS_PER_SECOND,
                    "max_upward_speed_blocks_per_second": MAX_UPWARD_SPEED_BLOCKS_PER_SECOND,
                    "latency_budget_seconds": LATENCY_BUDGET_SECONDS,
                },
                "max_observed": {
                    "horizontal_delta_blocks": self._max_horizontal_delta,
                    "horizontal_speed_blocks_per_second": self._max_horizontal_speed,
                    "upward_delta_blocks": self._max_upward_delta,
                    "upward_speed_blocks_per_second": self._max_upward_speed,
                },
                "last_position": list(self._last_sample.position) if self._last_sample else None,
            }

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with rcon_session(
                    self.host,
                    self.rcon_port,
                    self.rcon_password,
                    connect_timeout=5,
                    socket_timeout=5,
                ) as rcon:
                    while not self._stop.is_set():
                        response = command_with_retry(
                            rcon,
                            f"data get entity {self.username} Pos",
                            attempts=1,
                        )
                        position = parse_pos(response)
                        if position is not None:
                            self.record_sample(
                                time.monotonic(),
                                position,
                                death_count=self._read_deaths(rcon),
                            )
                        self._stop.wait(self.sample_interval_seconds)
            except Exception as exc:
                self._record_error(exc)
                self._stop.wait(self.sample_interval_seconds)

    def _read_deaths(self, rcon: Any) -> int | None:
        """Read the death counter; None when unavailable (guard just stays inactive)."""
        try:
            response = command_with_retry(
                rcon,
                f"scoreboard players get {self.username} {self.deaths_objective}",
                attempts=1,
            )
        except Exception:
            return None
        match = re.search(r"has (-?\d+)", response)
        return int(match.group(1)) if match else None

    def _record_error(self, exc: Exception) -> None:
        with self._lock:
            self._error_count += 1
            if len(self._errors) < 5:
                self._errors.append(str(exc))

    def _check_transition(self, previous: MovementSample, current: MovementSample) -> None:
        dt = current.t - previous.t
        if dt <= 0.05:
            return

        px, py, pz = previous.position
        cx, cy, cz = current.position
        horizontal_delta = math.hypot(cx - px, cz - pz)
        upward_delta = max(0.0, cy - py)
        # Latency-padded window: an upper bound on how fast the move could truly have been.
        # Legitimate movement stays under the caps even at worst-case latency; only movement
        # impossible over the padded window counts.
        effective_dt = dt + LATENCY_BUDGET_SECONDS
        horizontal_speed = horizontal_delta / effective_dt
        upward_speed = upward_delta / effective_dt

        self._max_horizontal_delta = max(self._max_horizontal_delta, horizontal_delta)
        self._max_horizontal_speed = max(self._max_horizontal_speed, horizontal_speed)
        self._max_upward_delta = max(self._max_upward_delta, upward_delta)
        self._max_upward_speed = max(self._max_upward_speed, upward_speed)

        if horizontal_speed > MAX_HORIZONTAL_SPEED_BLOCKS_PER_SECOND:
            self._register_violation(
                "impossible_horizontal_movement",
                previous,
                current,
                dt,
                horizontal_delta,
                upward_delta,
                horizontal_speed,
                upward_speed,
            )
            return

        if upward_speed > MAX_UPWARD_SPEED_BLOCKS_PER_SECOND:
            self._register_violation(
                "impossible_upward_movement",
                previous,
                current,
                dt,
                horizontal_delta,
                upward_delta,
                horizontal_speed,
                upward_speed,
            )

    def _register_violation(
        self,
        reason: str,
        previous: MovementSample,
        current: MovementSample,
        dt: float,
        horizontal_delta: float,
        upward_delta: float,
        horizontal_speed: float,
        upward_speed: float,
    ) -> None:
        self._violation_count += 1
        details = {
            "reason": reason,
            "previous_position": list(previous.position),
            "current_position": list(current.position),
            "dt_seconds": dt,
            "horizontal_delta_blocks": horizontal_delta,
            "upward_delta_blocks": upward_delta,
            "horizontal_speed_blocks_per_second": horizontal_speed,
            "upward_speed_blocks_per_second": upward_speed,
            "violation_index": self._violation_count,
        }
        # Keep the first violation as the representative record; keep counting the
        # rest so a sustained cheat crosses the flag threshold while isolated
        # jitter blips on a legitimate run do not.
        if self._violation is None:
            self._violation = details
