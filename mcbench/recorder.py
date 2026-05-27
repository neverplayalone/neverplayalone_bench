"""Sidecar recorder process: spawns the Node recorder under mcbench/recorder/.

Optional dependency. If `--record` is passed but `node_modules/` or ffmpeg are
missing, the runner logs a warning and continues without video.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RECORDER_DIR = Path(__file__).resolve().parent / "recorder"


def _node_bin() -> str:
    local_node = RECORDER_DIR / "node_modules" / "node" / "bin" / "node"
    if local_node.exists():
        return str(local_node)
    return "node"


@dataclass
class RecordOptions:
    output: Path
    target_username: str
    # Minecraft caps usernames at 16 chars — keep this short.
    recorder_username: str = "RecorderCam"
    width: int = 640
    height: int = 480
    fps: int = 20
    pov: str = "first"  # "first" or "third"
    host: str = "127.0.0.1"
    port: int = 25565


def is_available() -> tuple[bool, str | None]:
    """Quick preflight: report whether the recorder can run on this machine.

    Catches the common breakages so users see a clear message instead of a
    cryptic "createCanvas is not a function" from inside prismarine-viewer.
    """
    if not (RECORDER_DIR / "node_modules").exists():
        return False, (
            f"recorder Node deps missing — run: "
            f"(cd {RECORDER_DIR} && npm install)\n"
            f"  also requires system deps (Linux):\n"
            f"    sudo apt install -y ffmpeg libcairo2-dev libpango1.0-dev "
            f"libjpeg-dev libgif-dev librsvg2-dev pkg-config "
            f"libx11-dev libxi-dev libxrandr-dev libxinerama-dev "
            f"libxcursor-dev libgl1-mesa-dev"
        )
    if shutil.which("ffmpeg") is None:
        return False, "ffmpeg not found on PATH (apt install ffmpeg)"
    if not (RECORDER_DIR / "node_modules" / "node-canvas-webgl").exists():
        return False, (
            "node-canvas-webgl failed to install — usually means the `gl` native "
            "module couldn't build. Install OpenGL/X11 dev headers and retry:\n"
            "    sudo apt install -y libx11-dev libxi-dev libxrandr-dev "
            "libxinerama-dev libxcursor-dev libgl1-mesa-dev\n"
            f"then: (cd {RECORDER_DIR} && rm -rf node_modules && npm install)"
        )
    nested_canvas_binary = (
        RECORDER_DIR
        / "node_modules"
        / "node-canvas-webgl"
        / "node_modules"
        / "canvas"
        / "build"
        / "Release"
        / "canvas.node"
    )
    node_version = subprocess.run(
        [_node_bin(), "-p", "process.versions.node"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if node_version.startswith("22.") and not nested_canvas_binary.exists():
        return False, (
            "recorder native dependency missing: node-canvas-webgl depends on "
            "canvas@2.x, which has no prebuilt binary for Node 22 on this host. "
            "Use Node 20 LTS for the recorder, or install a C++ toolchain plus "
            "the README canvas/OpenGL system packages and reinstall:\n"
            f"    cd {RECORDER_DIR} && rm -rf node_modules package-lock.json && npm install"
        )
    gl_binary = (
        RECORDER_DIR
        / "node_modules"
        / "gl"
        / "build"
        / "Release"
        / "webgl.node"
    )
    if not gl_binary.exists() and shutil.which("g++-11") is None and shutil.which("g++") is None:
        return False, (
            "recorder native dependency missing: the `gl` module is not built, "
            "and no C++ compiler was found on PATH. Install build-essential/g++ "
            "plus the README OpenGL/X11 packages, then reinstall recorder deps:\n"
            f"    cd {RECORDER_DIR} && npm install && npm rebuild gl --update-binary"
        )
    if not gl_binary.exists() and not Path("/usr/include/X11/Xlib.h").exists():
        return False, (
            "recorder native dependency missing: the `gl` module is not built, "
            "and X11 headers are missing (`X11/Xlib.h`). Install the X11/OpenGL "
            "dev packages, then rebuild:\n"
            "    sudo apt install -y libx11-dev libxi-dev libxrandr-dev "
            "libxinerama-dev libxcursor-dev libgl1-mesa-dev\n"
            f"    cd {RECORDER_DIR} && npm rebuild gl --update-binary"
        )
    probe = subprocess.run(
        [
            _node_bin(),
            "-e",
            "require('node-canvas-webgl'); require('prismarine-viewer').headless",
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
            "recorder Node deps are installed but native canvas/WebGL modules "
            "do not load. Reinstall the recorder dependencies after installing "
            "the README system packages:\n"
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
        env = {
            **os.environ,
            "MCBENCH_REC_HOST": self.opts.host,
            "MCBENCH_REC_PORT": str(self.opts.port),
            "MCBENCH_REC_USERNAME": self.opts.recorder_username,
            "MCBENCH_REC_TARGET": self.opts.target_username,
            "MCBENCH_REC_OUTPUT": str(self.opts.output),
            "MCBENCH_REC_WIDTH": str(self.opts.width),
            "MCBENCH_REC_HEIGHT": str(self.opts.height),
            "MCBENCH_REC_FPS": str(self.opts.fps),
            "MCBENCH_REC_POV": self.opts.pov,
        }
        self.opts.output.parent.mkdir(parents=True, exist_ok=True)
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
    """Give the recorder a moment to connect and start ffmpeg before the agent acts."""
    time.sleep(seconds)
