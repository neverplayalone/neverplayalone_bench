from __future__ import annotations

import re
import threading
import time
from typing import Any

from npabench.evaluation.run_slot import ServerEndpoint
from npabench.minecraft.rcon_client import rcon_session
from npabench.missions.farming.config_schema import FarmingMissionConfig
from npabench.missions.farming.ledger import (
    HARVEST_OBJECTIVE,
    PLUGIN_SCORE_HOLDER,
    SYSTEM_OBJECTIVE,
)


class FarmingController:
    """Observe naturally grown harvests without changing crop state."""

    def __init__(
        self,
        server_endpoint: ServerEndpoint,
        mission_config: FarmingMissionConfig,
        setup_state: dict[str, Any] | None = None,
    ) -> None:
        self.endpoint = server_endpoint
        self.config = mission_config
        self.setup_state = setup_state or {}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started_at: float | None = None
        self._stopped_at: float | None = None
        self._status = "not_started"
        self._errors: list[str] = []
        self._events: list[dict[str, Any]] = []
        self._harvests: dict[str, int] = {}

    def start(self) -> FarmingController:
        if self._thread is not None:
            return self
        self._started_at = time.time()
        self._status = "running"
        self._thread = threading.Thread(
            target=self._run,
            name="npabench-farming-runtime",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            if self._thread.is_alive():
                self._record_error("farming controller did not stop within 10 seconds")
        if self._status != "error":
            try:
                # Give the server tick function time to observe the final block change.
                time.sleep(0.1)
                with self._rcon() as rcon:
                    self._observe_harvests(rcon)
            except Exception as exc:  # noqa: BLE001 - final ledger is required for scoring
                self._record_error(f"final farming observation failed: {exc}")
        self._stopped_at = time.time()
        if self._status != "error":
            self._status = "stopped"
        return self.report()

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self._status,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "growth_mode": "minecraft_random_ticks",
                "growth_steps": {},
                "harvests": dict(self._harvests),
                "events": list(self._events),
                "errors": list(self._errors),
            }

    def _run(self) -> None:
        try:
            with self._rcon() as rcon:
                while not self._stop.is_set():
                    self._observe_harvests(rcon)
                    self._stop.wait(self.config.environment.runtime_poll_seconds)
        except Exception as exc:  # noqa: BLE001 - runtime failures invalidate farming fairness
            self._record_error(str(exc))

    def _observe_harvests(self, rcon: Any) -> None:
        if _read_score_strict(rcon, PLUGIN_SCORE_HOLDER, SYSTEM_OBJECTIVE) != 1:
            raise RuntimeError("farming harvest plugin is not ready")
        observed = {
            target.key: _read_score_strict(rcon, target.harvest_holder, HARVEST_OBJECTIVE)
            for target in self.config.targets
        }
        with self._lock:
            for target in self.config.targets:
                previous = self._harvests.get(target.key, 0)
                count = max(previous, observed[target.key])
                self._harvests[target.key] = count
                if count > previous:
                    self._events.append(
                        {
                            "kind": "harvest",
                            "crop": target.key,
                            "count": count,
                            "at": time.time(),
                        }
                    )

    def _rcon(self):
        return rcon_session(
            self.endpoint.host,
            self.endpoint.rcon_port,
            self.endpoint.rcon_password,
            connect_timeout=5,
            socket_timeout=5,
        )

    def _record_error(self, message: str) -> None:
        with self._lock:
            self._errors.append(message)
            self._status = "error"


def _read_score_strict(rcon: Any, holder: str, objective: str) -> int:
    response = rcon.command(f"scoreboard players get {holder} {objective}")
    match = re.fullmatch(
        rf"{re.escape(holder)} has (-?\d+) \[{re.escape(objective)}\]",
        response.strip(),
    )
    if match is None:
        raise RuntimeError(f"could not read scoreboard {holder}/{objective}: {response!r}")
    return int(match.group(1))
