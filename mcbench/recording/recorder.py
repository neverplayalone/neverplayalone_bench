"""Sidecar packet recorder process: spawns the Node recorder under recording/sidecar/."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

RECORDER_DIR = Path(__file__).resolve().parent / "sidecar"


def _node_bin() -> str:
    local_node = RECORDER_DIR / "node_modules" / "node" / "bin" / "node"
    if local_node.exists():
        return str(local_node)
    return "node"


@dataclass
class RecordOptions:
    target_username: str
    packet_output: Path | None = None
    packet_manifest: Path | None = None
    replay_output: Path | None = None
    # Minecraft caps usernames at 16 chars — keep this short.
    recorder_username: str = "RecorderCam"
    host: str = "127.0.0.1"
    port: int = 25565


def is_available() -> tuple[bool, str | None]:
    """Quick preflight: report whether the packet recorder can run on this machine."""
    if not (RECORDER_DIR / "node_modules").exists():
        return False, (
            f"recorder Node deps missing — run: "
            f"(cd {RECORDER_DIR} && npm install)"
        )
    probe = subprocess.run(
        [
            _node_bin(),
            "-e",
            "require('mineflayer')",
        ],
        cwd=RECORDER_DIR,
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
            f"Node probe failed with:\n{tail}"
        )
    return True, None


class Recorder:
    """Manages the recorder subprocess lifecycle."""

    def __init__(self, opts: RecordOptions):
        self.opts = opts
        self.proc: subprocess.Popen | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_lines: list[str] = []

    def start(self) -> None:
        packet_output = self.opts.packet_output or Path("packets.jsonl.gz").resolve()
        packet_manifest = self.opts.packet_manifest or packet_output.with_name(
            "packets.manifest.json"
        )
        self.opts.packet_output = packet_output
        self.opts.packet_manifest = packet_manifest
        self.opts.replay_output = self.opts.replay_output or packet_output.with_name("recording.mcpr")
        env = {
            **os.environ,
            "MCBENCH_REC_HOST": self.opts.host,
            "MCBENCH_REC_PORT": str(self.opts.port),
            "MCBENCH_REC_USERNAME": self.opts.recorder_username,
            "MCBENCH_REC_TARGET": self.opts.target_username,
            "MCBENCH_REC_PACKET_OUTPUT": str(packet_output),
            "MCBENCH_REC_PACKET_MANIFEST": str(packet_manifest),
        }
        packet_output.parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(
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

    def stop(self, grace_seconds: float = 3.0) -> None:
        if not self.proc:
            return
        if self.proc.poll() is None:
            try:
                self.proc.send_signal(signal.SIGTERM)
                self.proc.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
        self.proc = None

    @property
    def stderr_log(self) -> list[str]:
        return list(self._stderr_lines)

    def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            self._stderr_lines.append(line.rstrip("\n"))


def wait_for_settle(seconds: float = 2.0) -> None:
    """Give the recorder a moment to connect before the agent acts."""
    time.sleep(seconds)
