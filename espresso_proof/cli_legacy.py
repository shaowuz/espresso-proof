"""Legacy compatibility commands.

Registered on the shared Typer ``app`` from ``espresso_proof.cli_app``
(flat command names, identical behavior). Imported for its side effects at
the bottom of cli.py."""
from __future__ import annotations

from pathlib import Path

import typer

from espresso_proof.orchestration.orchestrator import orchestrate_run

from espresso_proof.cli_app import app
from espresso_proof.cli_shared import (
    _guard_windows_run_path_length,
    _resolve_front_half_mode,
)


@app.command("run-flow")
def run_flow(
    input: Path = typer.Option(..., "--input", "-i", exists=True, dir_okay=False),
    project: str = typer.Option("demo", "--project", "-p"),
    literature_mode: str = typer.Option("local", "--literature-mode"),
    literature_provider: list[str] | None = typer.Option(None, "--literature-provider"),
    max_references: int = typer.Option(8, "--max-references", min=1, max=25),
    provider: str = typer.Option("stub", "--provider"),
    front_half: str = typer.Option("auto", "--front-half"),
    model: str = typer.Option("gpt-4.1", "--model"),
    reviewer_set: str = typer.Option("espresso", "--reviewer-set"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND",
        "--external-agent-command-env",
    ),
    agent_network_preflight: bool = typer.Option(
        False,
        "--agent-network-preflight/--no-agent-network-preflight",
    ),
    output_root: Path = typer.Option(Path("."), "--output-root", file_okay=False),
) -> None:
    """Compatibility command: run the end-to-end critique flow to the human gate.

    ``run-flow`` is a thin compatibility wrapper. ``orchestrate`` is the canonical
    runtime path; this delegates to the SAME ``orchestrate_run`` lifecycle so the
    two entrypoints can never drift in stage sequencing or safety behavior. It
    cannot upgrade proof/source/evidence status or create human_confirmed=True.
    """
    if literature_mode not in {"local", "live"}:
        raise typer.BadParameter("literature-mode must be local or live")
    if provider not in {"stub", "openai", "external"}:
        raise typer.BadParameter("provider must be stub, openai, or external")
    if front_half not in {"auto", "mock", "stub", "openai", "external"}:
        raise typer.BadParameter("front-half must be auto, mock, stub, openai, or external")
    if reviewer_set not in {"generic", "espresso"}:
        raise typer.BadParameter("reviewer-set must be generic or espresso")
    front_half = _resolve_front_half_mode(
        provider=provider, front_half=front_half, command_name="run-flow"
    )

    _guard_windows_run_path_length(output_root, project)
    result = orchestrate_run(
        project=project,
        input_path=input,
        output_root=output_root,
        provider=provider,
        front_half=front_half,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
        reviewer_set=reviewer_set,
        literature_mode=literature_mode,
        literature_providers=literature_provider,
        max_references=max_references,
        agent_network_preflight=agent_network_preflight,
    )

    typer.echo(f"Wrote run artifacts to {result['run_dir']}")
    typer.echo(f"Literature Discovery: {literature_mode}")
    typer.echo(f"Front-half agents: mode={front_half}")
    typer.echo(f"LLM agents: provider={provider}, reviewer_set={reviewer_set}")
    typer.echo(f"Human gate: {result['human_gate'].get('status', 'unknown')}")
