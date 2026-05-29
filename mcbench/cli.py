"""`mcbench` CLI entry point."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from . import server as server_mod
from .agents import AgentSpec, SubprocessAgent
from .config import load_task
from .recorder import RecordOptions
from .replay_tool import export_mcpr
from .runner import run_task

console = Console()


@click.group()
def main() -> None:
    """Benchmark mineflayer-style Minecraft agents."""


@main.group()
def server() -> None:
    """Manage the ephemeral Paper server."""


@server.command("up")
def server_up() -> None:
    console.log("Bringing server up…")
    try:
        server_mod.up(wait=True)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    console.log("[green]Server is ready.[/]")


@server.command("down")
def server_down() -> None:
    console.log("Tearing server down…")
    try:
        server_mod.down()
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e


@server.command("reset")
def server_reset() -> None:
    console.log("Resetting world (server will restart)…")
    try:
        server_mod.reset_world()
        server_mod.up(wait=True)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    console.log("[green]Fresh world ready.[/]")


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


@main.command("run")
@click.option("--task", "task_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--agent", "agent_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--agent-name", default=None, help="Display name for the agent")
@click.option("--record/--no-record", default=False, help="Record a ReplayMod-compatible packet replay")
@click.option(
    "--reset/--no-reset",
    default=True,
    help="Clean the spawn area before the run (prevents terrain/entities leaking across runs). "
    "Use --no-reset to reuse the current world as-is.",
)
def run_cmd(
    task_path: Path,
    agent_path: Path,
    agent_name: str | None,
    record: bool,
    reset: bool,
) -> None:
    """Run AGENT against TASK and grade the result."""
    task = load_task(task_path)
    spec = AgentSpec(name=agent_name or agent_path.name, path=str(agent_path))
    agent = SubprocessAgent(spec)
    rec_opts: RecordOptions | None = None
    if record:
        rec_opts = RecordOptions(
            target_username="BenchmarkBot",
        )
    try:
        run_task(task, agent, record=rec_opts, reset=reset)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e


@main.command("taskgen")
@click.option("--style", default=None, help="Style id to generate (omit with --list to see all)")
@click.option("--list", "list_styles", is_flag=True, help="List available styles and exit")
@click.option("--seed", type=int, default=0, help="Base seed (instance i uses seed+i)")
@click.option("--n", type=int, default=1, help="Number of seeded instances to generate")
@click.option(
    "--difficulty", type=click.Choice(["simple", "hard"]), default="simple"
)
@click.option(
    "--out",
    type=click.Path(path_type=Path),
    default=Path("tasks/generated"),
    help="Output directory (a <difficulty>/ subfolder is created)",
)
def taskgen_cmd(
    style: str | None,
    list_styles: bool,
    seed: int,
    n: int,
    difficulty: str,
    out: Path,
) -> None:
    """Generate seeded task YAMLs from a parameterized style."""
    from .taskgen import STYLES, generate, write_task

    if list_styles or not style:
        for sid, spec in sorted(STYLES.items()):
            console.print(f"  [bold]{sid}[/]  ({spec.category})")
        if not style:
            console.print("\nPass --style <id> to generate.")
        return
    if style not in STYLES:
        raise click.ClickException(f"unknown style {style!r}; run with --list")
    for i in range(n):
        try:
            cfg = generate(style, seed + i, difficulty)
        except ValueError as e:  # bounds invariant violated
            raise click.ClickException(f"generation rejected: {e}") from e
        path = write_task(cfg, out / difficulty)
        console.log(f"wrote {path}  — {cfg.goal}")


@main.command("bench")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=Path("bench.yaml"),
    help="Benchmark suite config (default: ./bench.yaml)",
)
@click.option(
    "--agent",
    "agent_override",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="Override the agent in the config (the field you swap most often)",
)
@click.option("--out", "out_override", type=click.Path(path_type=Path), default=None)
@click.option(
    "--valid",
    "valid_mode",
    is_flag=True,
    default=False,
    help="Validation mode: solve each task with the built-in oracle (records each) to "
    "confirm the generated tasks are solvable. Ignores --agent.",
)
def bench_cmd(
    config_path: Path,
    agent_override: Path | None,
    out_override: Path | None,
    valid_mode: bool,
) -> None:
    """Run a config-defined benchmark suite and print an aggregate report."""
    from .bench import load_bench_config, run_bench

    if not config_path.exists():
        raise click.ClickException(f"config not found: {config_path} (pass --config)")
    cfg = load_bench_config(config_path)
    try:
        run_bench(
            cfg,
            agent_path=str(agent_override) if agent_override else None,
            out_dir=out_override,
            valid_mode=valid_mode,
        )
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e


if __name__ == "__main__":
    main()
