from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Any

from npabench.minecraft.rcon_client import command_with_retry, rcon_session
from npabench.minecraft.rcon_helpers import parse_pos

DEFAULT_SAMPLE_INTERVAL_SECONDS = 0.5
MAX_HORIZONTAL_SPEED_BLOCKS_PER_SECOND = 9.0
MAX_SHORT_HORIZONTAL_DELTA_BLOCKS = 6.0
MAX_UPWARD_SPEED_BLOCKS_PER_SECOND = 6.0
MAX_SHORT_UPWARD_DELTA_BLOCKS = 2.25
SHORT_DELTA_WINDOW_SECONDS = 1.25


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
    ) -> None:
        self.host = host
        self.rcon_port = rcon_port
        self.rcon_password = rcon_password
        self.username = username
        self.sample_interval_seconds = sample_interval_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._stopped_at: float | None = None
        self._last_sample: MovementSample | None = None
        self._sample_count = 0
        self._error_count = 0
        self._errors: list[str] = []
        self._violation: dict[str, Any] | None = None
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
        return self._violation is not None

    def record_sample(self, sampled_at: float, position: tuple[float, float, float]) -> None:
        sample = MovementSample(sampled_at, position)
        with self._lock:
            previous = self._last_sample
            self._last_sample = sample
            self._sample_count += 1
            if previous is None or self._violation is not None:
                return
            self._check_transition(previous, sample)

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": True,
                "violated": self._violation is not None,
                "violation": self._violation,
                "sample_count": self._sample_count,
                "error_count": self._error_count,
                "errors": list(self._errors),
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "sample_interval_seconds": self.sample_interval_seconds,
                "thresholds": {
                    "max_horizontal_speed_blocks_per_second": MAX_HORIZONTAL_SPEED_BLOCKS_PER_SECOND,
                    "max_short_horizontal_delta_blocks": MAX_SHORT_HORIZONTAL_DELTA_BLOCKS,
                    "max_upward_speed_blocks_per_second": MAX_UPWARD_SPEED_BLOCKS_PER_SECOND,
                    "max_short_upward_delta_blocks": MAX_SHORT_UPWARD_DELTA_BLOCKS,
                    "short_delta_window_seconds": SHORT_DELTA_WINDOW_SECONDS,
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
                            self.record_sample(time.monotonic(), position)
                        self._stop.wait(self.sample_interval_seconds)
            except Exception as exc:
                self._record_error(exc)
                self._stop.wait(self.sample_interval_seconds)

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
        horizontal_speed = horizontal_delta / dt
        upward_speed = upward_delta / dt

        self._max_horizontal_delta = max(self._max_horizontal_delta, horizontal_delta)
        self._max_horizontal_speed = max(self._max_horizontal_speed, horizontal_speed)
        self._max_upward_delta = max(self._max_upward_delta, upward_delta)
        self._max_upward_speed = max(self._max_upward_speed, upward_speed)

        if (
            dt <= SHORT_DELTA_WINDOW_SECONDS
            and horizontal_delta > MAX_SHORT_HORIZONTAL_DELTA_BLOCKS
        ) or horizontal_speed > MAX_HORIZONTAL_SPEED_BLOCKS_PER_SECOND:
            self._violate(
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

        if (
            dt <= SHORT_DELTA_WINDOW_SECONDS
            and upward_delta > MAX_SHORT_UPWARD_DELTA_BLOCKS
        ) or upward_speed > MAX_UPWARD_SPEED_BLOCKS_PER_SECOND:
            self._violate(
                "impossible_upward_movement",
                previous,
                current,
                dt,
                horizontal_delta,
                upward_delta,
                horizontal_speed,
                upward_speed,
            )

    def _violate(
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
        self._violation = {
            "reason": reason,
            "previous_position": list(previous.position),
            "current_position": list(current.position),
            "dt_seconds": dt,
            "horizontal_delta_blocks": horizontal_delta,
            "upward_delta_blocks": upward_delta,
            "horizontal_speed_blocks_per_second": horizontal_speed,
            "upward_speed_blocks_per_second": upward_speed,
        }
