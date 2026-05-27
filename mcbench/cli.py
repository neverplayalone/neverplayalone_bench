"""`mcbench` CLI entry point."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from pathlib import Path as _Path

from . import server as server_mod
from .agents import AgentSpec, SubprocessAgent
from .config import load_task
from .recorder import RecordOptions
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


@main.command("run")
@click.option("--task", "task_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--agent", "agent_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--agent-name", default=None, help="Display name for the agent")
@click.option("--record/--no-record", default=False, help="Record an MP4 of the agent's POV via prismarine-viewer")
@click.option("--record-width", default=640, show_default=True, type=int)
@click.option("--record-height", default=480, show_default=True, type=int)
@click.option("--record-fps", default=20, show_default=True, type=int)
@click.option(
    "--record-pov",
    type=click.Choice(["first", "third"]),
    default="first",
    show_default=True,
    help="Camera perspective for the recording",
)
def run_cmd(
    task_path: Path,
    agent_path: Path,
    agent_name: str | None,
    record: bool,
    record_width: int,
    record_height: int,
    record_fps: int,
    record_pov: str,
) -> None:
    """Run AGENT against TASK and grade the result."""
    task = load_task(task_path)
    spec = AgentSpec(name=agent_name or agent_path.name, path=str(agent_path))
    agent = SubprocessAgent(spec)
    rec_opts: RecordOptions | None = None
    if record:
        rec_opts = RecordOptions(
            output=_Path("recording.mp4"),  # runner overrides with run-id dir
            target_username="BenchmarkBot",
            width=record_width,
            height=record_height,
            fps=record_fps,
            pov=record_pov,
        )
    try:
        run_task(task, agent, record=rec_opts)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e


if __name__ == "__main__":
    main()
