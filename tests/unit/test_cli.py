from __future__ import annotations

from click.testing import CliRunner

from mcbench.cli import main


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
