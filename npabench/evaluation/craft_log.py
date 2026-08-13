from __future__ import annotations

import json
import threading
from typing import Any

from npabench.minecraft.rcon_client import command_with_retry, rcon_session
from npabench.minecraft.rcon_helpers import count_item

POLL_SECONDS = 2.0


def _targets(mission_config: Any) -> list[tuple[str, list[str], int]]:
    """(label, item_ids, target_count) for each mission target -- crafting's
    ``recipes`` or mining/gathering's ``resources``."""
    specs = getattr(mission_config, "recipes", None) or getattr(mission_config, "resources", None) or []
    out: list[tuple[str, list[str], int]] = []
    for spec in specs:
        item = getattr(spec, "item", "") or ""
        label = getattr(spec, "display_name", None) or item
        items = [i for i in (getattr(spec, "items", None) or ([item] if item else [])) if i]
        count = int(getattr(spec, "target_count", 0) or 0)
        if items and label and count > 0:
            out.append((label, items, count))
    return out


class CraftAnnouncer:
    """Benchmark-side "crafted X" messages for the recording.

    The recorder is a spectator; it never receives the agent's inventory/window
    packets, and crafting has no world-visible event, so the harness cannot hook
    a craft directly. Instead it polls the agent's target-item counts over RCON
    and, whenever one goes up, broadcasts a ``tellraw`` system message -- which
    the recorder captures and ReplayMod replays. Independent of the agent (reads
    real inventory, not agent self-report) and read-only (count_item is a
    ``clear ... 0`` dry run)."""

    def __init__(
        self,
        *,
        host: str,
        rcon_port: int,
        rcon_password: str,
        username: str,
        mission_config: Any,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self.host = host
        self.rcon_port = rcon_port
        self.rcon_password = rcon_password
        self.username = username
        self.targets = _targets(mission_config)
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None and self.targets:
            self._thread = threading.Thread(target=self._run, name="npabench-craft-log", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.poll_seconds * 3))
            self._thread = None

    def _run(self) -> None:
        last: dict[str, int] = {}
        while not self._stop.is_set():
            try:
                with rcon_session(
                    self.host, self.rcon_port, self.rcon_password, connect_timeout=5, socket_timeout=5
                ) as rcon:
                    while not self._stop.is_set():
                        for label, items, target_count in self.targets:
                            have = sum(count_item(rcon, self.username, item) for item in items)
                            if have > last.get(label, 0):
                                self._announce(rcon, label, min(have, target_count), target_count)
                            last[label] = have
                        self._stop.wait(self.poll_seconds)
            except Exception:  # noqa: BLE001 - a broadcast failure must never affect the run
                self._stop.wait(self.poll_seconds)

    def _announce(self, rcon: Any, label: str, have: int, target_count: int) -> None:
        message = {"text": f"crafted {label} ({have}/{target_count})", "color": "aqua"}
        try:
            command_with_retry(rcon, f"tellraw @a {json.dumps(message)}", attempts=1)
        except Exception:  # noqa: BLE001
            pass
