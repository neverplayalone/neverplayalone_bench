from __future__ import annotations

import re
import threading
import time
from typing import Any

from npabench.evaluation.run_slot import ServerEndpoint
from npabench.minecraft.rcon_client import command_with_retry, rcon_session
from npabench.missions.farming.config_schema import FarmingMissionConfig, FarmingTargetSpec
from npabench.missions.farming.datapack import HARVEST_OBJECTIVE, READY_OBJECTIVE


class FarmingController:
    """Advance plot crops and report the datapack's verified harvest ledger."""

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
        self._growth_steps: dict[str, int] = {}
        self._harvests: dict[str, int] = {}
        self._next_growth: dict[str, float] = {}

    def start(self) -> FarmingController:
        if self._thread is not None:
            return self
        self._started_at = time.time()
        started = time.monotonic()
        self._next_growth = {
            target.key: started + target.growth_step_seconds for target in self.config.targets
        }
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
                "growth_steps": dict(self._growth_steps),
                "harvests": dict(self._harvests),
                "events": list(self._events),
                "errors": list(self._errors),
            }

    def _run(self) -> None:
        try:
            with self._rcon() as rcon:
                while not self._stop.is_set():
                    self._sanitize_unearned_states(rcon)
                    now = time.monotonic()
                    for target in self.config.targets:
                        while now >= self._next_growth[target.key] and not self._stop.is_set():
                            self._advance_target(rcon, target)
                            self._next_growth[target.key] += target.growth_step_seconds
                            with self._lock:
                                step = self._growth_steps.get(target.key, 0) + 1
                                self._growth_steps[target.key] = step
                                self._events.append(
                                    {
                                        "kind": "growth",
                                        "crop": target.key,
                                        "step": step,
                                        "at": time.time(),
                                    }
                                )
                    self._observe_harvests(rcon)
                    self._stop.wait(self.config.environment.runtime_poll_seconds)
        except Exception as exc:  # noqa: BLE001 - runtime failures invalidate farming fairness
            self._record_error(str(exc))

    def _advance_target(self, rcon: Any, target: FarmingTargetSpec) -> None:
        plot = self._plot(target.key)
        if target.template in {"farmland", "sweet_berries", "cocoa", "nether_wart"}:
            self._advance_ages(rcon, target, plot)
        elif target.template == "stem":
            self._advance_ages(rcon, target, plot)
            self._generate_cells(
                rcon,
                target,
                plot,
                source_state=f"{target.planted_block}[age={target.max_age}]",
            )
        elif target.template in {"water_edge", "cactus", "bamboo"}:
            self._generate_cells(rcon, target, plot, source_state=target.planted_block)
        elif target.template == "kelp":
            self._generate_kelp(rcon, target, plot)
        elif target.template == "mushroom":
            self._generate_cells(rcon, target, plot, source_state=target.planted_block)
        elif target.template == "glow_berries":
            self._ripen_glow_berries(rcon, target, plot)
        elif target.template == "tree":
            self._grow_trees(rcon, target, plot)
        else:  # pragma: no cover - schema and dispatcher must stay exhaustive
            raise RuntimeError(f"unsupported farming plot template: {target.template}")

    def _advance_ages(
        self,
        rcon: Any,
        target: FarmingTargetSpec,
        plot: dict[str, Any],
    ) -> None:
        if target.max_age is None:
            raise RuntimeError(f"{target.key} has no max_age")
        for age in range(target.max_age - 1, -1, -1):
            command_with_retry(
                rcon,
                f"fill {plot['x1']} {plot['ground_y']} {plot['z1']} "
                f"{plot['x2']} {plot['ground_y'] + 3} {plot['z2']} "
                f"minecraft:{target.planted_block}[age={age + 1}] replace "
                f"minecraft:{target.planted_block}[age={age}]",
            )

    def _generate_cells(
        self,
        rcon: Any,
        target: FarmingTargetSpec,
        plot: dict[str, Any],
        *,
        source_state: str,
    ) -> None:
        assert target.generated_block is not None
        for cell in plot["cells"]:
            source = cell.get("source")
            if source is None:
                continue
            sx, sy, sz = source
            empty = cell.get("empty_block", "air")
            selector = f"@e[type=minecraft:marker,tag={cell['tag']},limit=1]"
            command_with_retry(
                rcon,
                f"execute as {selector} at @s if score @s {READY_OBJECTIVE} matches 0 "
                f"if block {sx} {sy} {sz} minecraft:{source_state} "
                f"if block ~ ~ ~ minecraft:{empty} store success score @s {READY_OBJECTIVE} "
                f"run setblock ~ ~ ~ minecraft:{target.generated_block}",
            )

    def _generate_kelp(
        self,
        rcon: Any,
        target: FarmingTargetSpec,
        plot: dict[str, Any],
    ) -> None:
        assert target.generated_block is not None
        for cell in plot["cells"]:
            sx, sy, sz = cell["source"]
            selector = f"@e[type=minecraft:marker,tag={cell['tag']},limit=1]"
            command_with_retry(
                rcon,
                f"execute as {selector} at @s if score @s {READY_OBJECTIVE} matches 0 "
                f"if block {sx} {sy} {sz} minecraft:kelp if block ~ ~ ~ minecraft:water "
                f"store success score @s {READY_OBJECTIVE} run setblock ~ ~ ~ minecraft:kelp",
            )
            command_with_retry(
                rcon,
                f"execute as {selector} at @s if score @s {READY_OBJECTIVE} matches 0 "
                f"if block {sx} {sy} {sz} minecraft:kelp_plant if block ~ ~ ~ minecraft:water "
                f"store success score @s {READY_OBJECTIVE} run setblock ~ ~ ~ minecraft:kelp",
            )

    def _ripen_glow_berries(
        self,
        rcon: Any,
        target: FarmingTargetSpec,
        plot: dict[str, Any],
    ) -> None:
        del target
        for cell in plot["cells"]:
            selector = f"@e[type=minecraft:marker,tag={cell['tag']},limit=1]"
            command_with_retry(
                rcon,
                f"execute as {selector} at @s if score @s {READY_OBJECTIVE} matches 0 "
                "if block ~ ~ ~ minecraft:cave_vines[berries=false] "
                f"store success score @s {READY_OBJECTIVE} run setblock ~ ~ ~ "
                "minecraft:cave_vines[berries=true]",
            )

    def _sanitize_unearned_states(self, rcon: Any) -> None:
        for target in self.config.targets:
            if target.template != "glow_berries":
                continue
            for cell in self._plot(target.key)["cells"]:
                selector = f"@e[type=minecraft:marker,tag={cell['tag']},limit=1]"
                command_with_retry(
                    rcon,
                    f"execute as {selector} at @s if score @s {READY_OBJECTIVE} matches 0 "
                    "if block ~ ~ ~ minecraft:cave_vines[berries=true] run setblock ~ ~ ~ "
                    "minecraft:cave_vines[berries=false]",
                )

    def _grow_trees(
        self,
        rcon: Any,
        target: FarmingTargetSpec,
        plot: dict[str, Any],
    ) -> None:
        del target
        for cell in plot["cells"]:
            selector = f"@e[type=minecraft:marker,tag={cell['tag']},limit=1]"
            command_with_retry(
                rcon,
                f"execute as {selector} at @s if score @s {READY_OBJECTIVE} matches 0 "
                f"if block ~ ~ ~ minecraft:oak_sapling store success score @s {READY_OBJECTIVE} "
                "run setblock ~ ~ ~ minecraft:oak_log",
            )
            command_with_retry(
                rcon,
                f"execute as {selector} at @s if score @s {READY_OBJECTIVE} matches 1 "
                "run fill ~ ~1 ~ ~ ~3 ~ minecraft:oak_log",
            )
            command_with_retry(
                rcon,
                f"execute as {selector} at @s if score @s {READY_OBJECTIVE} matches 1 "
                "run fill ~-1 ~3 ~-1 ~1 ~4 ~1 minecraft:oak_leaves[persistent=true] replace air",
            )

    def _observe_harvests(self, rcon: Any) -> None:
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

    def _plot(self, target_key: str) -> dict[str, Any]:
        for plot in self.setup_state.get("plots", []):
            if plot.get("target_key") == target_key:
                return plot
        raise RuntimeError(f"farming runtime is missing plot for {target_key}")

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
