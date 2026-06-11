"""`mcbench` CLI entry point."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from .recording.replay import export_mcpr

console = Console()


@click.group()
def main() -> None:
    """Benchmark mineflayer-style Minecraft agents."""


@main.group()
def replay() -> None:
    """Manage visual replay artifacts."""


@replay.command("export-mcpr")
@click.argument("packet_log", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Output .mcpr path (default: recording.mcpr next to the packet log)",
)
def replay_export_mcpr(packet_log: Path, output: Path | None) -> None:
    """Export a packet log to a ReplayMod .mcpr file."""
    try:
        mcpr = export_mcpr(packet_log, output=output)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    console.log(f"[green]ReplayMod file written:[/] {mcpr}")


def _run_batch(
    *,
    competition_id: str,
    config_path: Path | None,
    agent_assignments: tuple[str, ...],
    seed: int,
    challenge_id: str | None,
    base_game_port: int,
    base_rcon_port: int,
    out_dir: Path | None,
    record: bool,
    keep_slots: bool,
) -> None:
    from .core import create_evaluation_batch, parse_agent_assignment, run_evaluation_batch
    from .registry import get_competition

    try:
        competition = get_competition(competition_id)
        cfg_path = config_path or competition.default_config_path()
        if not Path(cfg_path).exists():
            raise ValueError(f"config file does not exist: {cfg_path}")

        agents = [parse_agent_assignment(raw) for raw in agent_assignments]
        cfg = competition.load_config(cfg_path)
        batch = create_evaluation_batch(
            competition=competition,
            base_cfg=cfg,
            agents=agents,
            seed=seed,
            challenge_id=challenge_id,
            output_dir=out_dir,
            base_game_port=base_game_port,
            base_rcon_port=base_rcon_port,
        )
        report = run_evaluation_batch(batch, record=record, keep_slots=keep_slots)
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e

    console.log(f"[bold green]Batch complete:[/] {batch.output_dir}")
    for result in report["results"]:
        if "error" in result:
            console.log(
                f"[red]{result['miner']}[/] slot {result['slot']} failed: {result['error']}"
            )
        else:
            console.log(
                f"{result['miner']} slot {result['slot']}: "
                f"{result['score']:.1f} / {result['max_score']:.1f}"
            )


def _batch_options(func):
    """Shared options for the batch-running commands."""
    options = [
        click.option(
            "--config",
            "config_path",
            type=click.Path(path_type=Path),
            default=None,
            help="Run config (default: the competition's bundled config.yaml).",
        ),
        click.option(
            "--agent",
            "agent_assignments",
            multiple=True,
            required=True,
            help="Miner assignment as NAME=PATH or PATH. Repeat once per miner.",
        ),
        click.option("--seed", type=int, required=True, help="Deterministic challenge seed."),
        click.option("--challenge-id", default=None, help="Optional explicit challenge id."),
        click.option("--base-game-port", type=int, default=25665),
        click.option("--base-rcon-port", type=int, default=25675),
        click.option("--out", "out_dir", type=click.Path(path_type=Path), default=None),
        click.option(
            "--record/--no-record",
            default=True,
            show_default=True,
            help="Record every miner slot (use --no-record to disable).",
        ),
        click.option(
            "--keep-slots/--no-keep-slots",
            default=False,
            help="Keep per-slot world copies after the batch (default: delete; "
            "scores, traces, recordings, and world_template are kept).",
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return func


@main.command("run")
@click.option(
    "--competition",
    "competition_id",
    default="resource_gathering_v1",
    show_default=True,
    help="Competition id to run (see the registry).",
)
@_batch_options
def run_cmd(competition_id: str, **kwargs) -> None:
    """Run one generated challenge for the chosen competition across miner slots."""
    _run_batch(competition_id=competition_id, **kwargs)


@main.command("resource-gather")
@_batch_options
def resource_gather_cmd(**kwargs) -> None:
    """Alias for `run --competition resource_gathering_v1`."""
    _run_batch(competition_id="resource_gathering_v1", **kwargs)


if __name__ == "__main__":
    main()
