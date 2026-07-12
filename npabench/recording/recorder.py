from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from npabench.config import DEFAULT_RECORDER_USERNAME, RECORDER_DIR

_NODE_IPC_ENV_KEYS = (
    "NODE_CHANNEL_FD",
    "NODE_CHANNEL_SERIALIZATION_MODE",
    "NODE_UNIQUE_ID",
)


def _node_bin() -> str:
    configured = os.environ.get("NPABENCH_NODE_BIN")
    if configured:
        return configured
    local_node = RECORDER_DIR / "node_modules" / "node" / "bin" / "node"
    if local_node.exists():
        return str(local_node)
    resolved = shutil.which("node")
    if resolved:
        return resolved
    return "node"


def _node_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    # PM2 injects Node IPC variables into managed processes. If a Python
    # validator launched by PM2 spawns node, the child node process inherits
    # those variables and can abort before loading mineflayer.
    for key in _NODE_IPC_ENV_KEYS:
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


@dataclass
class RecordingOptions:
    target_username: str
    packet_output: Path | None = None
    packet_manifest: Path | None = None
    replay_output: Path | None = None
    recorder_username: str = DEFAULT_RECORDER_USERNAME
    host: str = "127.0.0.1"
    port: int = 25565


def is_available() -> tuple[bool, str | None]:
    if not (RECORDER_DIR / "node_modules").exists():
        return False, (
            f"recorder Node deps missing — run: "
            f"(cd {RECORDER_DIR} && npm install)"
        )
    node_bin = _node_bin()
    probe = subprocess.run(
        [
            node_bin,
            "-e",
            "require('mineflayer')",
        ],
        cwd=RECORDER_DIR,
        env=_node_env(),
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout).strip().splitlines()
        tail = "\n".join(detail[-8:])
        return False, (
            "recorder Node deps are installed but mineflayer does not load. "
            f"Reinstall the recorder dependencies:\n"
            f"    cd {RECORDER_DIR} && rm -rf node_modules package-lock.json && npm install\n"
            f"Node probe failed returncode={probe.returncode} node={node_bin} cwd={RECORDER_DIR} "
            f"PATH={os.environ.get('PATH', '')!r} with:\n{tail or '<empty output>'}"
        )
    return True, None


class Recorder:
    def __init__(self, options: RecordingOptions):
        self.options = options
        self.child_process: subprocess.Popen | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lines: list[str] = []

    def start(self) -> None:
        packet_output = self.options.packet_output or Path("packets.jsonl.gz").resolve()
        packet_manifest = self.options.packet_manifest or packet_output.with_name(
            "packets.manifest.json"
        )
        self.options.packet_output = packet_output
        self.options.packet_manifest = packet_manifest
        self.options.replay_output = (
            self.options.replay_output or packet_output.with_name("recording.mcpr")
        )
        env = _node_env(
            {
                "NPABENCH_RECORDER_HOST": self.options.host,
                "NPABENCH_RECORDER_PORT": str(self.options.port),
                "NPABENCH_RECORDER_USERNAME": self.options.recorder_username,
                "NPABENCH_RECORDER_TARGET": self.options.target_username,
                "NPABENCH_RECORDER_PACKET_OUTPUT": str(packet_output),
                "NPABENCH_RECORDER_PACKET_MANIFEST": str(packet_manifest),
            }
        )
        packet_output.parent.mkdir(parents=True, exist_ok=True)
        self.child_process = subprocess.Popen(
            [_node_bin(), "index.js"],
            cwd=RECORDER_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def stop(self, grace_seconds: float = 12.0) -> None:
        if not self.child_process:
            return
        if self.child_process.poll() is None:
            try:
                self.child_process.send_signal(signal.SIGTERM)
                self.child_process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                self.child_process.kill()
                self.child_process.wait(timeout=2)
        self.child_process = None

    @property
    def stderr_log(self) -> list[str]:
        return list(self._stderr_lines)

    def _drain_stderr(self) -> None:
        assert self.child_process and self.child_process.stderr
        for line in self.child_process.stderr:
            self._stderr_lines.append(line.rstrip("\n"))


def wait_for_settle(seconds: float = 2.0) -> None:
    time.sleep(seconds)
