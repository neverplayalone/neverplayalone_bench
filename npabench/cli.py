from __future__ import annotations

from pathlib import Path

import click

from npabench.agents.base import AgentSpec
from npabench.evaluation.evaluate import (
    AgentMode,
    evaluate_multiple_agents,
    evaluate_single_agent,
)
from npabench.evaluation.comparison import compare_agents
from npabench.recording.replay_exporter import export_mcpr


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
        if not batch_report.evidence_valid:
            click.echo(
                "INVALID COMPARISON EVIDENCE: "
                + ", ".join(batch_report.evidence_reasons),
                err=True,
            )
        click.echo(str(batch_report.output_dir))
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e


@main.command("compare")
@click.argument("baseline")
@click.argument("candidate")
@click.option(
    "--mission",
    "mission_id",
    default="resource_gathering",
    show_default=True,
)
@click.option("--seed", "seeds", type=int, multiple=True)
@click.option("--pairs-per-seed", type=int, default=1, show_default=True)
@click.option("--config", "config_path", type=click.Path(path_type=Path), default=None)
@click.option(
    "--output-dir",
    "output_dir",
    type=click.Path(path_type=Path),
    required=True,
)
@click.option("--record/--no-record", default=False, show_default=True)
@click.option("--max-parallel", type=click.IntRange(1, 2), default=2, show_default=True)
@click.option(
    "--sandbox/--no-sandbox",
    default=True,
    show_default=True,
)
def compare_cmd(
    baseline: str,
    candidate: str,
    mission_id: str,
    seeds: tuple[int, ...],
    pairs_per_seed: int,
    config_path: Path | None,
    output_dir: Path,
    record: bool,
    max_parallel: int,
    sandbox: bool,
) -> None:
    """Compare BASELINE and CANDIDATE in counterbalanced AB/BA pairs."""

    agent_mode = AgentMode.SANDBOXED if sandbox else AgentMode.HOST
    try:
        report = compare_agents(
            _parse_agent_assignment(baseline),
            _parse_agent_assignment(candidate),
            mission_id=mission_id,
            seeds=seeds or (0,),
            pairs_per_seed=pairs_per_seed,
            config_path=config_path,
            output_dir=output_dir,
            record=record,
            agent_mode=agent_mode,
            max_parallel=max_parallel,
        )
        delta = report.summary["candidate_minus_baseline"]
        order_sensitivity = report.summary["order_sensitivity"]
        click.echo(
            f"{report.state}: {report.summary['completed_pairs']}/"
            f"{report.summary['expected_pairs']} pairs; "
            f"candidate-baseline mean={delta['mean']:.6f} "
            f"median={delta['median']:.6f}; "
            f"W/T/L={delta['wins']}/{delta['ties']}/{delta['losses']}"
        )
        click.echo(
            "order sensitivity: "
            f"mean gap={order_sensitivity['mean_absolute_delta_gap']:.6f} "
            f"max gap={order_sensitivity['max_absolute_delta_gap']:.6f}; "
            f"direction disagreements={order_sensitivity['direction_disagreements']}"
        )
        promotion = report.summary["promotion_gate"]
        click.echo(
            "promotion evidence: "
            f"sufficient={str(promotion['sufficient_repeated_evidence']).lower()} "
            f"supports_candidate={str(promotion['supports_candidate']).lower()}; "
            f"evidence_reasons={','.join(promotion['evidence_reasons']) or 'none'}; "
            f"decision_reasons={','.join(promotion['decision_reasons']) or 'none'}"
        )
        click.echo(str(report.output_dir / "comparison.json"))
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e


@main.command("build-agent-image")
def build_agent_image_cmd() -> None:
    from npabench.agents import ensure_agent_image

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
