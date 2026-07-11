from __future__ import annotations

import subprocess
from pathlib import Path

from npabench.recording import recorder


def test_recorder_probe_strips_pm2_node_ipc_env(monkeypatch, tmp_path: Path) -> None:
    recorder_dir = tmp_path / "recorder"
    (recorder_dir / "node_modules").mkdir(parents=True)
    captured: dict[str, dict[str, str]] = {}

    def fake_run(*args, **kwargs):
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(recorder, "RECORDER_DIR", recorder_dir)
    monkeypatch.setattr(recorder, "_node_bin", lambda: "/usr/bin/node")
    monkeypatch.setattr(recorder.subprocess, "run", fake_run)
    monkeypatch.setenv("NODE_CHANNEL_FD", "3")
    monkeypatch.setenv("NODE_CHANNEL_SERIALIZATION_MODE", "json")
    monkeypatch.setenv("NODE_UNIQUE_ID", "pm2-worker")

    available, reason = recorder.is_available()

    assert available is True
    assert reason is None
    assert "NODE_CHANNEL_FD" not in captured["env"]
    assert "NODE_CHANNEL_SERIALIZATION_MODE" not in captured["env"]
    assert "NODE_UNIQUE_ID" not in captured["env"]


def test_recorder_start_strips_pm2_node_ipc_env(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, dict[str, str]] = {}

    class FakeProcess:
        stderr = ["recorder started\n"]

        def poll(self):
            return 0

    def fake_popen(*args, **kwargs):
        captured["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(recorder, "RECORDER_DIR", tmp_path / "recorder")
    monkeypatch.setattr(recorder, "_node_bin", lambda: "/usr/bin/node")
    monkeypatch.setattr(recorder.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("NODE_CHANNEL_FD", "3")
    monkeypatch.setenv("NODE_CHANNEL_SERIALIZATION_MODE", "json")
    monkeypatch.setenv("NODE_UNIQUE_ID", "pm2-worker")

    instance = recorder.Recorder(
        recorder.RecordingOptions(
            target_username="agent",
            packet_output=tmp_path / "packets.jsonl.gz",
        )
    )
    instance.start()

    assert "NODE_CHANNEL_FD" not in captured["env"]
    assert "NODE_CHANNEL_SERIALIZATION_MODE" not in captured["env"]
    assert "NODE_UNIQUE_ID" not in captured["env"]
    assert captured["env"]["NPABENCH_RECORDER_TARGET"] == "agent"
