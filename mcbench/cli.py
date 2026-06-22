from __future__ import annotations

from pathlib import Path

import click

from mcbench.agents.base import AgentSpec
from mcbench.evaluation.evaluate import (
    AgentMode,
    evaluate_multiple_agents,
    evaluate_single_agent,
)
from mcbench.recording.replay_exporter import export_mcpr


@click.group()
def main() -> None:
    pass


@main.group()
def replay() -> None:
    pass


@replay.command("export-mcpr")
@click.argument("packet_log", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="Output .mcpr path (default: recording.mcpr next to the packet log)",
)
def replay_export_mcpr(packet_log: Path, output: Path | None) -> None:
    try:
        mcpr = export_mcpr(packet_log, output=output)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"ReplayMod file written: {mcpr}")


@main.command("run")
@click.argument("agents", nargs=-1, required=True)
@click.option(
    "--mission",
    "mission_id",
    default="resource_gathering",
    show_default=True,
)
@click.option("--seed", type=int, default=0, show_default=True)
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None)
@click.option("--output-dir", "output_dir", type=click.Path(path_type=Path), default=None)
@click.option("--record/--no-record", default=True, show_default=True)
@click.option("--max-parallel", type=int, default=1, show_default=True)
@click.option(
    "--sandbox/--no-sandbox",
    default=True,
    show_default=True,
)
def run_cmd(
    agents: tuple[str, ...],
    mission_id: str,
    seed: int,
    config_path: Path | None,
    output_dir: Path | None,
    record: bool,
    max_parallel: int,
    sandbox: bool,
) -> None:
    agent_mode = AgentMode.SANDBOXED if sandbox else AgentMode.HOST
    try:
        parsed_agents = [_parse_agent_assignment(raw) for raw in agents]
        if len(parsed_agents) == 1 and max_parallel <= 1:
            report = evaluate_single_agent(
                parsed_agents[0],
                mission_id=mission_id,
                seed=seed,
                config_path=config_path,
                output_dir=output_dir,
                record=record,
                agent_mode=agent_mode,
            )
            click.echo(
                f"{report.agent_name}: {report.score:.1f}/{report.max_score:.1f} "
                f"({report.status})"
            )
            click.echo(str(report.output_dir))
            return

        batch_report = evaluate_multiple_agents(
            parsed_agents,
            mission_id=mission_id,
            seed=seed,
            config_path=config_path,
            output_dir=output_dir,
            record=record,
            agent_mode=agent_mode,
            max_parallel=max_parallel,
        )
        for agent_name, report in batch_report.agents.items():
            click.echo(
                f"{agent_name}: {report.score:.1f}/{report.max_score:.1f} ({report.status})"
            )
        click.echo(str(batch_report.output_dir))
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e


@main.command("build-agent-image")
def build_agent_image_cmd() -> None:
    from mcbench.agents import ensure_agent_image

    try:
        tag = ensure_agent_image()
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"Agent runtime image ready: {tag}")


def _parse_agent_assignment(raw: str) -> AgentSpec:
    if "=" in raw:
        name, path_raw = raw.split("=", 1)
        if not name:
            raise ValueError(f"invalid agent assignment {raw!r}: missing name")
    else:
        path_raw = raw
        name = Path(path_raw).name
    path = Path(path_raw).resolve()
    if not path.exists():
        raise ValueError(f"agent path does not exist: {path}")
    return AgentSpec(name=name, path=path)


if __name__ == "__main__":
    main()
