"""`mcbench` CLI entry point."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from mcbench.recording.replay import export_mcpr

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
    task_id: str,
    config_path: Path | None,
    agent_assignments: tuple[str, ...],
    seed: int,
    instance_id: str | None,
    base_game_port: int,
    base_rcon_port: int,
    out_dir: Path | None,
    record: bool,
    keep_slots: bool,
    normal: bool,
) -> None:
    from mcbench.core import create_evaluation_batch, parse_agent_assignment, run_evaluation_batch
    from mcbench.registry import get_task

    try:
        task = get_task(task_id)
        cfg_path = config_path or task.default_config_path()
        if not Path(cfg_path).exists():
            raise ValueError(f"config file does not exist: {cfg_path}")

        agents = [parse_agent_assignment(raw) for raw in agent_assignments]
        cfg = task.load_config(cfg_path)
        batch = create_evaluation_batch(
            task=task,
            base_cfg=cfg,
            agents=agents,
            seed=seed,
            instance_id=instance_id,
            output_dir=out_dir,
            base_game_port=base_game_port,
            base_rcon_port=base_rcon_port,
        )
        report = run_evaluation_batch(
            batch,
            record=record,
            keep_slots=keep_slots,
            # Docker sandbox by default; --normal opts back into host subprocess.
            agent_mode="subprocess" if normal else "docker",
        )
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e

    console.log(f"[bold green]Batch complete:[/] {batch.output_dir}")
    for result in report["results"]:
        if "error" in result:
            console.log(
                f"[red]{result['agent']}[/] slot {result['slot']} failed: {result['error']}"
            )
        else:
            console.log(
                f"{result['agent']} slot {result['slot']}: "
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
            help="Run config (default: the task's bundled default.yaml).",
        ),
        click.option(
            "--seed",
            type=int,
            default=0,
            show_default=True,
            help="Deterministic instance seed.",
        ),
        click.option("--instance-id", default=None, help="Optional explicit instance id."),
        click.option("--base-game-port", type=int, default=25665),
        click.option("--base-rcon-port", type=int, default=25675),
        click.option("--out", "out_dir", type=click.Path(path_type=Path), default=None),
        click.option(
            "--record/--no-record",
            default=True,
            show_default=True,
            help="Record every agent slot (use --no-record to disable).",
        ),
        click.option(
            "--keep-slots/--no-keep-slots",
            default=False,
            help="Keep per-slot world copies after the batch (default: delete; "
            "scores, traces, recordings, and world_template are kept).",
        ),
        click.option(
            "--normal",
            is_flag=True,
            default=False,
            help="Run agents as host subprocesses (no sandbox). Default: each "
            "agent runs in an isolated Docker container.",
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return func


@main.command("run")
@click.argument("agents", nargs=-1, required=True)
@click.option(
    "--task",
    "task_id",
    default="resource_gathering_v1",
    show_default=True,
    help="Task id to run (see the registry).",
)
@_batch_options
def run_cmd(task_id: str, agents: tuple[str, ...], **kwargs) -> None:
    """Run one generated instance for a task across one or more agents.

    AGENTS are agent paths (or NAME=PATH); each runs in its own slot. Agents run
    sandboxed in Docker by default; pass --normal to run them as host subprocesses.
    """
    _run_batch(task_id=task_id, agent_assignments=agents, **kwargs)


@main.command("build-agent-image")
def build_agent_image_cmd() -> None:
    """Build the sandbox runtime image used by Docker agent mode (the default)."""
    from mcbench.agents import ensure_agent_image

    try:
        tag = ensure_agent_image()
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    console.log(f"[bold green]Agent runtime image ready:[/] {tag}")


if __name__ == "__main__":
    main()
