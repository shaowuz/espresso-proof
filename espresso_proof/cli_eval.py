"""Evaluation commands: benchmark / calibration / scorecard / judge (non-product).

Registered on the shared Typer ``app`` from ``espresso_proof.cli_app``
(flat command names, identical behavior). Imported for its side effects at
the bottom of cli.py."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from espresso_proof.kernel.io import read_yaml
from espresso_proof.orchestration.orchestrator import orchestrate_run
# NOTE: literature_retrieval.retrieve_and_ground (corpus_dir/query, older
# retrieve-sources --ground path) is a DIFFERENT function from
# Retrieval_grounding.retrieve_and_ground (candidate/local_corpus). Alias
# This one so it does not shadow the import above (which the
# `retrieve-and-ground` command depends on).
from espresso_proof.verification.mathematical_judgment import judge_run
from espresso_proof.evaluation.benchmark import run_benchmark
from espresso_proof.evaluation.calibration import run_calibration
from espresso_proof.evaluation.real_math_benchmark import (
    build_real_math_capability_report,
    case_from_run as real_math_case_from_run,
)
from espresso_proof.evaluation.scorecard import render_scorecard_markdown, run_scorecard
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.validators import KernelValidationError, validate_run

from espresso_proof.cli_app import app
from espresso_proof.cli_shared import (
    _assert_fixture_expectations,
    _resolve_front_half_mode,
    _validate_seed_files,
)


@app.command("judge")
def judge_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Record conservative mathematical risk judgments for a run (advisory only).

    Writes mathematical_judgment.yaml and re-validates. The layer never upgrades
    proof/source/evidence status: it can only warn, request more evidence,
    downgrade confidence, or ask for human review. The kernel validators remain
    the only gate that earns any status.
    """
    report = judge_run(run)
    validate_run(run)
    kinds = ", ".join(sorted({item["kind"] for item in report["findings"]})) or "none"
    typer.echo(f"Recorded {len(report['findings'])} judgment finding(s): {kinds}")
    typer.echo(f"-> {run / 'mathematical_judgment.yaml'}")


@app.command("benchmark")
def benchmark_command(
    output_root: Path = typer.Option(Path(".benchmark_runs"), "--out-root", help="Where benchmark runs are written."),
    json_out: Path | None = typer.Option(
        None, "--json-out", help="Also write the full report as machine-readable JSON."
    ),
) -> None:
    """Run the regression benchmark (curated conservative-behaviour cases over the
    existing mock fixtures) and print a stable report. Exits non-zero if any case
    regresses. Never asserts a claim is proved.
    """
    report = run_benchmark(output_root)
    passed = sum(1 for row in report if row["passed"])
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(
            json.dumps({"cases": report, "passed": passed, "total": len(report)},
                       indent=2, sort_keys=True),
            encoding="utf-8",
        )
    for row in report:
        mark = "PASS" if row["passed"] else "FAIL"
        typer.echo(f"[{mark}] {row['category']:24s} {row['fixture']:42s} status={row['idea_status']}")
        for problem in row["problems"]:
            typer.echo(f"        - {problem}")
    typer.echo(f"benchmark: {passed}/{len(report)} case(s) passed")
    if passed != len(report):
        raise typer.Exit(code=1)


@app.command("real-math-capability")
def real_math_capability_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    case_id: str | None = typer.Option(None, "--case-id"),
    provider: str | None = typer.Option(
        None, "--provider", help="Override provider; otherwise infer from llm_agent_run.yaml."
    ),
    model_fingerprint: str | None = typer.Option(
        None,
        "--model-fingerprint",
        help="Override model fingerprint; otherwise infer from llm_agent_run.yaml.",
    ),
    prompt_version: str = typer.Option(..., "--prompt-version"),
    repeat_count: int = typer.Option(1, "--repeat-count", min=1),
    json_out: Path | None = typer.Option(
        None, "--json-out", help="Write the capability report as machine-readable JSON."
    ),
) -> None:
    """Evaluate one existing run against the real-math capability ledger.

    Read-only: this command validates/inspects run artifacts and reports whether
    the run was valid, used a ready real provider without adapter fallback, found
    useful routes, and earned any scoped/source/formal/human capability labels.
    It never upgrades proof/source/evidence status.
    """

    case = real_math_case_from_run(
        run,
        case_id=case_id,
        provider=provider,
        model_fingerprint=model_fingerprint,
        prompt_version=prompt_version,
        repeat_count=repeat_count,
    )
    report = build_real_math_capability_report([case])
    row = report["cases"][0]
    metrics = report["metrics"]
    typer.echo(
        "real-math capability: "
        f"valid_run={row['valid_run']} "
        f"real_agent_valid_run={row['real_agent_valid_run']} "
        f"useful_route={row['useful_route_found']} "
        f"source_supported={row['source_supported_argument']} "
        f"checked_scoped={row['checked_scoped_fact']} "
        f"formal={row['formal_solution']} "
        f"human_attested={row['human_attested_solution']} "
        f"overclaim_count={metrics['overclaim_count']}"
    )
    typer.echo(
        "provider: "
        f"{row['provider']} model={row['model_fingerprint']} "
        f"ready={row['provider_ready']} adapter_fallback={row['adapter_fallback_used']}"
    )
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        typer.echo(f"Wrote real-math capability report -> {json_out}")
    if metrics["overclaim_count"]:
        raise typer.Exit(code=1)


@app.command("real-agent-smoke")
def real_agent_smoke_command(
    input: Path = typer.Option(
        Path("tests/fixtures/promising_but_unproved/input.md"),
        "--input",
        "-i",
        exists=True,
        dir_okay=False,
    ),
    project: str = typer.Option("real_codex_smoke", "--project", "-p"),
    literature_mode: str = typer.Option("local", "--literature-mode"),
    literature_provider: list[str] | None = typer.Option(None, "--literature-provider"),
    max_references: int = typer.Option(8, "--max-references", min=1, max=25),
    provider: str = typer.Option("external", "--provider"),
    front_half: str = typer.Option("auto", "--front-half"),
    model: str = typer.Option("codex-cli", "--model"),
    reviewer_set: str = typer.Option("espresso", "--reviewer-set"),
    api_key_env: str = typer.Option("OPENAI_API_KEY", "--api-key-env"),
    external_agent_command_env: str = typer.Option(
        "ESPRESSO_EXTERNAL_AGENT_COMMAND",
        "--external-agent-command-env",
    ),
    output_root: Path = typer.Option(
        Path(".real_agent_smoke_runs"), "--output-root", file_okay=False
    ),
    prompt_version: str = typer.Option("real-agent-smoke-v1", "--prompt-version"),
    repeat_count: int = typer.Option(1, "--repeat-count", min=1),
    json_out: Path | None = typer.Option(
        None,
        "--json-out",
        help="Write the capability report here; default: <run>/real_math_capability.json.",
    ),
) -> None:
    """Run the real-agent smoke gate and write a capability report.

    This is a product-readiness smoke, not a solver claim. It requires a
    non-stub provider, runs the normal orchestrator path with network preflight,
    validates the run, and then writes the same read-only capability report as
    ``real-math-capability``. Passing means "real agent produced a valid run
    without adapter fallback or proof-language overclaim."
    """

    if literature_mode not in {"local", "live"}:
        raise typer.BadParameter("literature-mode must be local or live")
    if provider not in {"openai", "external"}:
        raise typer.BadParameter("real-agent-smoke requires provider=openai or provider=external")
    if front_half not in {"auto", "mock", "stub", "openai", "external"}:
        raise typer.BadParameter("front-half must be auto, mock, stub, openai, or external")
    if reviewer_set not in {"generic", "espresso"}:
        raise typer.BadParameter("reviewer-set must be generic or espresso")
    front_half = _resolve_front_half_mode(
        provider=provider, front_half=front_half, command_name="real-agent-smoke"
    )

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
        agent_network_preflight=True,
    )
    run_dir = Path(result["run_dir"])
    report_path = json_out or (run_dir / "real_math_capability.json")
    case = real_math_case_from_run(
        run_dir,
        case_id=run_dir.name,
        prompt_version=prompt_version,
        repeat_count=repeat_count,
    )
    report = build_real_math_capability_report([case])
    row = report["cases"][0]
    metrics = report["metrics"]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    typer.echo(f"Wrote run artifacts to {run_dir}")
    typer.echo(f"Wrote real-math capability report -> {report_path}")
    typer.echo(
        "real-agent smoke: "
        f"valid_run={row['valid_run']} "
        f"real_agent_valid_run={row['real_agent_valid_run']} "
        f"provider_ready={row['provider_ready']} "
        f"adapter_fallback={row['adapter_fallback_used']} "
        f"agent_solved={row['agent_solved']} "
        f"overclaim_count={metrics['overclaim_count']}"
    )
    if not row["real_agent_valid_run"] or metrics["overclaim_count"]:
        raise typer.Exit(code=1)


@app.command()
def calibration(
    output_root: Path = typer.Option(Path(".benchmark_runs"), "--out-root", help="Where calibration runs are written."),
    json_out: Path | None = typer.Option(
        None, "--json-out", help="Also write the full report as machine-readable JSON."
    ),
) -> None:
    """Aggregate the product's core SAFETY metrics over the mock fixtures.

    Reports per-fixture validation, source-support false positives, proof-language
    violations, strategy high-score false positives, and memo honesty. All safety
    metrics must be 0; the command exits non-zero on any regression. Earns no
    status and never asserts a claim is proved.
    """
    report = run_calibration(output_root)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    m = report["metrics"]
    for row in report["rows"]:
        mark = "ok " if row["memo_honest"] and not row["source_support_false_positives"] \
            and not row["strategy_high_score_false_positives"] else "BAD"
        typer.echo(
            f"[{mark}] {row['fixture']:44s} validated={row['validated']} "
            f"src_fp={row['source_support_false_positives']} "
            f"hiscore_fp={row['strategy_high_score_false_positives']} "
            f"proof_lang={len(row['proof_language'])}"
        )
    typer.echo(
        f"calibration: {m['fixtures_validated']}/{m['fixtures_total']} validated; "
        f"source-support false positives={m['source_support_false_positives']}; "
        f"proof-language violations={m['proof_language_violations']}; "
        f"high-score false positives={m['strategy_high_score_false_positives']}; "
        f"memo honest={m['memo_honest']}/{m['fixtures_total']}"
    )
    if not m["clean"]:
        raise typer.Exit(code=1)


@app.command()
def scorecard(
    output_root: Path = typer.Option(Path(".benchmark_runs"), "--out-root", help="Where scorecard probe runs are written."),
    json_out: Path | None = typer.Option(
        None, "--json-out", help="Write the full scorecard as machine-readable JSON."
    ),
    markdown_out: Path | None = typer.Option(
        None, "--markdown-out", help="Write the trend-friendly Markdown summary."
    ),
) -> None:
    """Autoprover Behavior Scorecard: deterministic safety AND
    capability metrics over the mock fixtures — baseline regressions, earning-
    pipeline wiring, seeded-gold-flaw reviewer recall, and a sanitized dogfood
    replay. Exits non-zero on any regression. Metrics never earn or upgrade any
    mathematical status; no claim is ever 'proved' by a scorecard.
    """
    report = run_scorecard(output_root)
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown = render_scorecard_markdown(report)
    if markdown_out is not None:
        markdown_out.parent.mkdir(parents=True, exist_ok=True)
        markdown_out.write_text(markdown, encoding="utf-8")
    typer.echo(markdown)
    if not report["clean"]:
        raise typer.Exit(code=1)


@app.command()
def eval(
    mode: str = typer.Option("mock", "--mode"),
    schemas_only: bool = typer.Option(False, "--schemas-only"),
    fixture: Path | None = typer.Option(None, "--fixture", exists=True, file_okay=False),
) -> None:
    """Run deterministic P0 evals."""
    if schemas_only:
        _validate_seed_files()
        typer.echo("Schema-only validation passed")
        return
    if mode != "mock":
        raise typer.BadParameter("P0 eval only supports --mode mock")
    fixture_dirs = [fixture] if fixture else sorted(Path("tests/fixtures").iterdir())
    passed = 0
    for fixture_dir in fixture_dirs:
        if not fixture_dir.is_dir():
            continue
        input_path = fixture_dir / "input.md"
        expected_path = fixture_dir / "expected.yaml"
        rubric_path = fixture_dir / "rubric.md"
        missing = [
            path.name
            for path in (input_path, expected_path, rubric_path)
            if not path.exists()
        ]
        if missing:
            raise KernelValidationError(
                f"{fixture_dir}: fixture is missing required files {missing}"
            )
        run_dir = run_mock_critique("hodge_atoms", input_path, Path(".eval_runs") / fixture_dir.name)
        validate_run(run_dir)
        _assert_fixture_expectations(run_dir, read_yaml(expected_path))
        passed += 1
        typer.echo(f"PASS {fixture_dir.name}")
    typer.echo(f"{passed} fixture(s) passed")
