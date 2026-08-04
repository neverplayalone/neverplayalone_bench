from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from npabench.cli import main
from npabench.recording.replay_exporter import export_mcpr


def test_cli_help_smoke() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "run" in result.output
    assert "replay" in result.output


def test_cli_run_help_smoke() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--help"])

    assert result.exit_code == 0
    assert "--mission" in result.output
    assert "--no-sandbox" in result.output


def test_cli_compare_help_smoke() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["compare", "--help"])

    assert result.exit_code == 0
    assert "--pairs-per-seed" in result.output
    assert "--output-dir" in result.output
    assert "BASELINE" in result.output
    assert "CANDIDATE" in result.output


def test_replay_exporter_missing_packet_log_error(tmp_path: Path) -> None:
    missing_log = tmp_path / "missing.jsonl.gz"

    try:
        export_mcpr(missing_log)
    except RuntimeError as exc:
        assert "packet log does not exist" in str(exc)
    else:
        raise AssertionError("expected export_mcpr to fail for a missing packet log")
