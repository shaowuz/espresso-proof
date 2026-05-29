from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer

from .contracts import DEFAULT_AGENT_FLOW, default_agent_contracts
from .io import read_text, read_yaml, write_text, write_yaml
from .literature_discovery import discover_literature as run_literature_discovery
from .memory import summarize_memory
from .mock_pipeline import run_mock_critique
from .pdf_ingestion import ingest_source_for_theorems
from .source_verification import verify_source_grounding_from_theorems
from .models import (
    AdmissionCheck,
    AgentContracts,
    ExampleBank,
    ExampleFeedbackSummary,
    HumanExampleFeedback,
    LiteratureReviewReport,
    TheoremExtractionReport,
    WorkflowSpec,
)
from .validators import KernelValidationError, validate_run
from .workflow_generator import generate_workflow_spec

app = typer.Typer(help="Espresso Proof local kernel.")


@app.command()
def init(project: str = "hodge_atoms") -> None:
    """Initialize the local project directories used by the P0 kernel."""
    project_dir = Path("projects") / project
    (project_dir / "runs").mkdir(parents=True, exist_ok=True)
    (project_dir / "memory").mkdir(parents=True, exist_ok=True)
    contracts = default_agent_contracts(project)
    AgentContracts.model_validate(contracts)
    contract_path = project_dir / "agent_contracts.yaml"
    if not contract_path.exists():
        write_yaml(contract_path, contracts)
    typer.echo(f"Initialized {project_dir}")


@app.command("critique-sketch")
def critique_sketch(
    input: Path = typer.Option(..., "--input", "-i", exists=True, dir_okay=False),
    project: str = typer.Option("hodge_atoms", "--project", "-p"),
    mode: str = typer.Option("mock", "--mode"),
) -> None:
    """Run a schema-checked mock critique pass."""
    if mode != "mock":
        raise typer.BadParameter("P0 only supports --mode mock")
    run_dir = run_mock_critique(project, input, Path("."))
    typer.echo(f"Wrote run artifacts to {run_dir}")


@app.command()
def inspect(run: Path = typer.Option(..., "--run", exists=True, file_okay=False)) -> None:
    """Validate and print a human-readable run summary."""
    validate_run(run)
    typer.echo(f"Run is valid: {run}")
    claim = read_yaml(run / "locked_claim.yaml")
    grade = read_yaml(run / "idea_grade.yaml")
    review = read_yaml(run / "review.yaml")
    strategy_brief = read_yaml(run / "strategy_brief.yaml")
    source_grounding = read_yaml(run / "source_grounding.yaml")
    human_choice = read_yaml(run / "human_choice.yaml")

    failed_reviewers = [
        reviewer["reviewer_id"]
        for reviewer in review.get("reviewers", [])
        if reviewer.get("verdict") == "fail"
    ]
    source_gaps = grade.get("source_gaps", [])
    source_statuses = sorted(
        {
            ref.get("status")
            for ref in source_grounding.get("source_refs", [])
            if ref.get("status")
        }
        | {
            step.get("status")
            for step in source_grounding.get("steps", [])
            if step.get("status")
        }
    )

    typer.echo("")
    typer.echo("Claim")
    typer.echo(f"- {claim.get('normalized_statement', '').strip()}")
    typer.echo("")
    typer.echo("Grade")
    typer.echo(
        f"- {grade.get('status')} / {grade.get('score')} "
        f"({grade.get('proof_language_status')})"
    )
    typer.echo("")
    typer.echo("Strongest Objection")
    typer.echo(f"- {review.get('strongest_objection')}")
    typer.echo("")
    typer.echo("Failed Reviewers")
    typer.echo(f"- {', '.join(failed_reviewers) if failed_reviewers else 'none'}")
    typer.echo("")
    typer.echo("Source Gaps")
    typer.echo(f"- {', '.join(source_gaps) if source_gaps else 'none'}")
    typer.echo(f"- source status: {', '.join(source_statuses) if source_statuses else 'none'}")
    typer.echo("")
    typer.echo("Top Strategies")
    for index, strategy in enumerate(strategy_brief.get("top_strategies", [])[:3], start=1):
        typer.echo(
            f"{index}. {strategy.get('title')} "
            f"[score {strategy.get('score')}, effort {strategy.get('estimated_effort')}]"
        )
        typer.echo(f"   risk: {strategy.get('main_risk')}")
    typer.echo("")
    typer.echo("Human Choice")
    typer.echo(
        f"- {human_choice.get('status')} / {human_choice.get('selected_strategy') or 'none'}"
    )
    memory_summary = summarize_memory(run.parents[1], claim_text=claim.get("normalized_statement", ""))
    if memory_summary:
        typer.echo("")
        typer.echo("Memory")
        for line in memory_summary.splitlines()[:10]:
            typer.echo(line)
    typer.echo("")
    typer.echo(f"Final report: {run / 'final_report.md'}")


@app.command("discover-literature")
def discover_literature(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    mode: str = typer.Option("local", "--mode"),
    query: str | None = typer.Option(None, "--query"),
    max_references: int = typer.Option(8, "--max-references", min=1, max=25),
    provider: list[str] | None = typer.Option(None, "--provider"),
    ads_token_env: str = typer.Option("ADS_API_TOKEN", "--ads-token-env"),
) -> None:
    """Run conservative Literature Discovery for an existing run."""
    if mode not in {"local", "live"}:
        raise typer.BadParameter("Literature Discovery supports --mode local or --mode live")
    validate_run(run)
    manifest_path = run / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    artifacts = manifest["artifacts"]
    project_dir = run.parents[1]
    report = run_literature_discovery(
        project_dir=project_dir,
        locked_claim_path=run / artifacts["locked_claim"],
        hypothesis_ledger_path=run / artifacts["hypothesis_ledger"],
        max_references=max_references,
        query=query,
        mode=mode,
        providers=provider,
        ads_token_env=ads_token_env,
    )
    LiteratureReviewReport.model_validate(report)
    write_yaml(run / artifacts["literature_review"], report)
    validate_run(run)
    typer.echo(f"Wrote {mode} literature discovery to {run / artifacts['literature_review']}")
    typer.echo("Reference status remains candidate/unverified/not_found; Referencing owns source_backed.")


@app.command("ingest-pdf")
def ingest_pdf(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    pdf: str = typer.Option(..., "--pdf", help="Local PDF/text path or HTTPS URL."),
    source_id: str = typer.Option("source_001", "--source-id"),
    title: str = typer.Option("Ingested source", "--title"),
    author: list[str] | None = typer.Option(None, "--author"),
    source_type: str = typer.Option("paper", "--source-type"),
) -> None:
    """Ingest a PDF/text source and write theorem_extraction.yaml."""
    validate_run(run)
    manifest_path = run / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    claim = read_yaml(run / manifest["artifacts"]["locked_claim"])
    report = ingest_source_for_theorems(
        claim_id=claim["id"],
        source=pdf,
        source_id=source_id,
        title=title,
        authors=author or [],
        source_type=source_type,
    )
    TheoremExtractionReport.model_validate(report)
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = "theorem_extraction.yaml"
    write_yaml(run / "theorem_extraction.yaml", report)
    write_yaml(manifest_path, manifest)
    validate_run(run)
    typer.echo(f"Wrote theorem extraction to {run / 'theorem_extraction.yaml'}")


@app.command("verify-sources")
def verify_sources(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    theorem_extraction: Path | None = typer.Option(None, "--theorem-extraction", exists=True, dir_okay=False),
) -> None:
    """Run conservative Referencing verification from extracted theorem statements."""
    validate_run(run)
    manifest = read_yaml(run / "run_manifest.yaml")
    extraction_path = theorem_extraction
    if extraction_path is None:
        extraction_name = manifest.get("optional_artifacts", {}).get("theorem_extraction")
        if not extraction_name:
            raise typer.BadParameter("run has no theorem_extraction optional artifact")
        extraction_path = run / extraction_name
    verify_source_grounding_from_theorems(
        run_dir=run,
        theorem_extraction_path=extraction_path,
    )
    validate_run(run)
    typer.echo(f"Wrote source verification to {run / 'source_grounding.yaml'}")


@app.command("feedback-example")
def feedback_example(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    target: str = typer.Option(..., "--target", help="Example id from examples.yaml."),
    label: list[str] | None = typer.Option(None, "--label", "-l", help="Repeatable taste label."),
    reason: str = typer.Option(..., "--reason"),
    scope: str = typer.Option("project", "--scope"),
    action: list[str] | None = typer.Option(None, "--action", "-a", help="Optional repeatable action."),
) -> None:
    """Record confirmed human taste feedback about an example."""
    validate_run(run)
    labels = label or []
    if not labels:
        raise typer.BadParameter("provide at least one --label")
    examples = read_yaml(run / "examples.yaml")
    example_ids = {example["id"] for example in examples.get("examples", [])}
    if target not in example_ids:
        raise typer.BadParameter(f"unknown example target {target!r}")

    manifest_path = run / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    optional = manifest.setdefault("optional_artifacts", {})
    feedback_path = run / optional.get("human_example_feedback", "human_example_feedback.yaml")
    existing = read_yaml(feedback_path) if feedback_path.exists() else None
    if existing is None:
        feedback_data = {
            "schema_version": 1,
            "project_id": manifest["project_id"],
            "claim_id": examples["claim_id"],
            "run_id": manifest["run_id"],
            "created_at": datetime.now(UTC).isoformat(),
            "feedback": [],
        }
    else:
        feedback_data = existing
    next_id = _next_feedback_id(feedback_data.get("feedback", []))
    feedback_data["feedback"].append(
        {
            "feedback_id": next_id,
            "target_type": "example",
            "target_artifact": "examples.yaml",
            "target_id": target,
            "labels": labels,
            "reason": reason,
            "scope": scope,
            "requested_actions": action or _default_feedback_actions(labels),
            "human_confirmed": True,
            "supersedes_feedback_id": None,
        }
    )
    HumanExampleFeedback.model_validate(feedback_data)
    optional["human_example_feedback"] = feedback_path.name
    write_yaml(feedback_path, feedback_data)
    write_yaml(manifest_path, manifest)
    validate_run(run)
    typer.echo(f"Wrote feedback {next_id} to {feedback_path}")


@app.command("summarize-feedback")
def summarize_feedback(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Summarize confirmed human feedback into ranking-only preferences."""
    validate_run(run)
    manifest_path = run / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    feedback_name = manifest.get("optional_artifacts", {}).get("human_example_feedback")
    if not feedback_name:
        raise typer.BadParameter("run has no human_example_feedback optional artifact")
    feedback_path = run / feedback_name
    feedback_data = read_yaml(feedback_path)
    feedback = HumanExampleFeedback.model_validate(feedback_data)
    adjustments = []
    project_preferences = []
    for item in feedback.feedback:
        label_values = {label.value for label in item.labels}
        direction = "down" if label_values & {"misleading", "too_easy", "irrelevant"} else "up"
        weight = "high" if label_values & {"dangerous", "canonical", "favorite"} else "medium"
        adjustments.append(
            {
                "target_id": item.target_id,
                "target_type": item.target_type.value,
                "target_artifact": item.target_artifact,
                "feedback_ids": [item.feedback_id],
                "direction": direction,
                "weight": weight,
                "reason": item.reason,
            }
        )
        project_preferences.append(
            f"{item.target_type.value}:{item.target_id} labeled "
            f"{', '.join(label.value for label in item.labels)}"
        )
    summary_data = {
        "schema_version": 1,
        "claim_id": feedback.claim_id,
        "run_id": feedback.run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "generated_by": "summarize-feedback",
        "feedback_artifact_path": feedback_name,
        "summarized_from": [item.feedback_id for item in feedback.feedback],
        "input_artifact_digests": {},
        "ranking_adjustments": adjustments,
        "project_preferences": project_preferences,
        "playbook_tuning_candidates": [],
        "memory_entries": [],
    }
    ExampleFeedbackSummary.model_validate(summary_data)
    manifest.setdefault("optional_artifacts", {})["example_feedback_summary"] = "example_feedback_summary.yaml"
    write_yaml(run / "example_feedback_summary.yaml", summary_data)
    write_yaml(manifest_path, manifest)
    validate_run(run)
    typer.echo(f"Wrote feedback summary to {run / 'example_feedback_summary.yaml'}")


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


@app.command("generate-agent")
def generate_agent(
    prompt: Path = typer.Option(..., "--prompt", "-i", exists=True, dir_okay=False),
    project: str = typer.Option("generated_agent", "--project", "-p"),
    mode: str = typer.Option("v0", "--mode"),
) -> None:
    """Create a schema-checked draft agent workflow from a research prompt."""
    if mode not in {"v0", "stub"}:
        raise typer.BadParameter("P0 only supports --mode v0")
    text = read_text(prompt)
    project_dir = Path("projects") / project
    project_dir.mkdir(parents=True, exist_ok=True)
    spec = generate_workflow_spec(text, project_name=project)
    WorkflowSpec.model_validate(spec)
    contracts = default_agent_contracts(project)
    AgentContracts.model_validate(contracts)
    trial_dir = project_dir / "trial_fixture"
    trial_dir.mkdir(parents=True, exist_ok=True)
    trial_input = trial_dir / "input.md"
    if not trial_input.exists():
        write_text(
            trial_input,
            "Draft trial fixture. Replace with a representative proof sketch before admission.\n",
        )
    trial_expected = trial_dir / "expected.yaml"
    if not trial_expected.exists():
        write_yaml(
            trial_expected,
            {
                "fixture": "draft_trial_fixture",
                "required": {
                    "idea_status": ["fragile", "likely_false", "unclear"],
                    "forbidden_final_report_phrases": ["verified", "proof complete"],
                },
            },
        )
    admission = {
        "project_id": project,
        "workflow_spec_valid": True,
        "agent_contracts_exist": True,
        "required_artifacts_declared": True,
        "trial_fixture_exists": trial_input.exists() and trial_expected.exists(),
        "human_confirmed": False,
        "admitted": False,
        "blockers": ["human confirmation required before this workflow is reusable"],
    }
    AdmissionCheck.model_validate(admission)
    write_yaml(project_dir / "workflow_spec.yaml", spec)
    write_yaml(project_dir / "agent_contracts.yaml", contracts)
    write_yaml(project_dir / "admission_check.yaml", admission)
    write_text(
        project_dir / "playbook.md",
        "# Generated Agent Draft\n\n"
        "This is a deterministic P0 workflow draft. It is schema-valid but must be reviewed by the mathematician before use.\n\n"
        f"- Domain: {spec.get('domain')}\n"
        f"- Domain key: {spec.get('domain_key', 'unknown')}\n"
        f"- Uses live LLM: {spec.get('source', {}).get('uses_live_llm', False)}\n"
        f"- Uses network: {spec.get('source', {}).get('uses_network', False)}\n\n"
        "## Research Prompt\n\n"
        f"{text}\n",
    )
    typer.echo(f"Wrote draft agent to {project_dir}")
    typer.echo("Admission status: not admitted; human confirmation required")


@app.command("improve-workflow")
def improve_workflow(
    project: str = typer.Option(..., "--project", "-p"),
    note: str | None = typer.Option(None, "--note"),
    note_file: Path | None = typer.Option(None, "--note-file", exists=True, dir_okay=False),
) -> None:
    """Record a workflow improvement request without changing the checked spec automatically."""
    project_dir = Path("projects") / project
    spec_path = project_dir / "workflow_spec.yaml"
    if not spec_path.exists():
        raise typer.BadParameter(f"missing workflow spec: {spec_path}")
    WorkflowSpec.model_validate(read_yaml(spec_path))
    if note_file is not None:
        update = read_text(note_file)
    elif note is not None:
        update = note
    else:
        raise typer.BadParameter("provide --note or --note-file")
    update_path = project_dir / "workflow_updates.md"
    existing = read_text(update_path) if update_path.exists() else "# Workflow Updates\n\n"
    write_text(update_path, f"{existing.rstrip()}\n\n## Pending Update\n\n{update.strip()}\n")
    typer.echo(f"Recorded workflow update in {update_path}")


def _validate_seed_files() -> None:
    WorkflowSpec.model_validate(read_yaml(Path("projects/hodge_atoms/workflow_spec.yaml")))
    ExampleBank.model_validate(read_yaml(Path("projects/hodge_atoms/example_bank.yaml")))
    contract_path = Path("projects/hodge_atoms/agent_contracts.yaml")
    if contract_path.exists():
        AgentContracts.model_validate(read_yaml(contract_path))
    coherent_dir = Path("projects/coherent_base_change")
    if coherent_dir.exists():
        WorkflowSpec.model_validate(read_yaml(coherent_dir / "workflow_spec.yaml"))
        ExampleBank.model_validate(read_yaml(coherent_dir / "example_bank.yaml"))
        AgentContracts.model_validate(read_yaml(coherent_dir / "agent_contracts.yaml"))


def _next_feedback_id(existing: list[dict]) -> str:
    return f"feedback_{len(existing) + 1:03d}"


def _default_feedback_actions(labels: list[str]) -> list[str]:
    actions: set[str] = set()
    for label in labels:
        if label == "dangerous":
            actions.add("prioritize_counterexample_testing")
        elif label == "needs_source":
            actions.add("run_referencing")
        elif label == "needs_computation":
            actions.add("run_evidence_auditor")
        elif label in {"misleading", "too_easy", "irrelevant"}:
            actions.add("demote_ranking")
        elif label in {"interesting", "canonical", "favorite"}:
            actions.add("promote_ranking")
        if label == "favorite":
            actions.add("save_to_memory")
    return sorted(actions)


def _assert_fixture_expectations(run_dir: Path, expected: dict) -> None:
    required = expected.get("required", {})
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    idea_grade = read_yaml(run_dir / "idea_grade.yaml")
    counterexamples = read_yaml(run_dir / "counterexamples.yaml")
    computations = read_yaml(run_dir / "computation_checks.yaml")
    source_grounding = read_yaml(run_dir / "source_grounding.yaml")
    strategy_brief = read_yaml(run_dir / "strategy_brief.yaml")
    strategy_synthesis = read_yaml(run_dir / "strategy_synthesis.yaml")
    tests = read_yaml(run_dir / "tests.yaml")
    final_report = read_text(run_dir / "final_report.md").lower()

    if "idea_status" in required and idea_grade["status"] not in required["idea_status"]:
        raise KernelValidationError(
            f"idea status {idea_grade['status']!r} not in {required['idea_status']!r}"
        )

    for phrase in required.get("forbidden_final_report_phrases", []):
        if phrase in final_report:
            raise KernelValidationError(f"final report contains forbidden phrase {phrase!r}")

    for candidate in required.get("required_counterexample_candidates", []):
        if candidate not in idea_grade.get("counterexample_candidates", []):
            raise KernelValidationError(f"missing counterexample candidate {candidate!r}")

    for test_id in required.get("required_survived_tests", []):
        if test_id not in idea_grade.get("survived_tests", []):
            raise KernelValidationError(f"missing survived evidence test {test_id!r}")

    for test_id in required.get("required_failed_tests", []):
        if test_id not in idea_grade.get("failed_tests", []):
            raise KernelValidationError(f"missing failed evidence test {test_id!r}")

    if "meaningful_checked_support" in required:
        support = required["meaningful_checked_support"]
        allowed_statuses = set(support.get("statuses", ["checked", "source_checked"]))
        excluded = set(support.get("exclude_check_ids", []))
        required_check_ids = set(support.get("required_check_ids", []))
        eligible_checks = [
            check
            for check in computations.get("checks", [])
            if check.get("id") not in excluded and check.get("status") in allowed_statuses
        ]
        eligible_ids = {check.get("id") for check in eligible_checks}
        if len(eligible_checks) < support.get("min_count", 1):
            raise KernelValidationError("missing meaningful checked/source_checked support")
        missing_check_ids = required_check_ids.difference(eligible_ids)
        if missing_check_ids:
            raise KernelValidationError(
                f"missing required support checks {sorted(missing_check_ids)}"
            )

    if "counterexample_test" in required:
        expected_test = required["counterexample_test"]
        actual = _find_by_id(counterexamples.get("tests", []), expected_test["id"])
        for key, value in expected_test.items():
            if actual.get(key) != value:
                raise KernelValidationError(
                    f"counterexample test {expected_test['id']} expected {key}={value!r}, got {actual.get(key)!r}"
                )

    statuses = {
        ref.get("status")
        for ref in source_grounding.get("source_refs", [])
    } | {step.get("status") for step in source_grounding.get("steps", [])}
    for status in required.get("required_source_status", []):
        if status not in statuses:
            raise KernelValidationError(f"missing source status {status!r}")
    if required.get("source_backed_allowed") is False and "source_backed" in statuses:
        raise KernelValidationError("source_backed was not allowed for this fixture")

    if "human_feedback" in required:
        _assert_human_feedback_expectations(
            run_dir,
            manifest,
            required["human_feedback"],
            idea_grade,
            source_grounding,
        )

    computation_statuses = {check.get("status") for check in computations.get("checks", [])}
    expected_computation_statuses = set(required.get("computation_status", []))
    if expected_computation_statuses and not computation_statuses.intersection(expected_computation_statuses):
        raise KernelValidationError(
            f"missing expected computation statuses {sorted(expected_computation_statuses)}"
        )

    if required.get("no_survived_or_failed_from_unchecked_examples"):
        if idea_grade.get("survived_tests") or idea_grade.get("failed_tests"):
            raise KernelValidationError("unchecked examples entered survived/failed idea evidence")
        for test in tests.get("tests", []):
            if test.get("evidence_status") in {"human_needed", "inconclusive"} and test.get("result") in {
                "survived",
                "failed",
            }:
                raise KernelValidationError("unchecked examples entered survived/failed test evidence")

    if not strategy_brief.get("top_strategies"):
        raise KernelValidationError("strategy_brief must contain at least one top strategy")
    synthesis_ids = {route.get("id") for route in strategy_synthesis.get("routes", [])}
    brief_ids = {route.get("id") for route in strategy_brief.get("top_strategies", [])}
    if not brief_ids.issubset(synthesis_ids):
        raise KernelValidationError("strategy_brief includes a strategy not produced by synthesis")

    for strategy_id in required.get("required_strategy_ids", []):
        if strategy_id not in brief_ids:
            raise KernelValidationError(f"missing strategy brief route {strategy_id!r}")

    if required.get("strategy_risk_required"):
        for route in strategy_brief.get("top_strategies", []):
            if not str(route.get("main_risk", "")).strip():
                raise KernelValidationError(f"strategy {route.get('id')!r} is missing main_risk")

    for status in required.get("required_strategy_status", []):
        if status not in {route.get("status") for route in strategy_brief.get("top_strategies", [])}:
            raise KernelValidationError(f"missing strategy status {status!r}")

    for phrase in required.get("forbidden_strategy_phrases", []):
        lowered = phrase.lower()
        for route in strategy_brief.get("top_strategies", []):
            route_text = " ".join(str(value) for value in route.values()).lower()
            if lowered in route_text:
                raise KernelValidationError(f"strategy brief contains forbidden phrase {phrase!r}")


def _find_by_id(items: list[dict], item_id: str) -> dict:
    for item in items:
        if item.get("id") == item_id:
            return item
    raise KernelValidationError(f"missing item id {item_id!r}")


def _assert_human_feedback_expectations(
    run_dir: Path,
    manifest: dict,
    required: dict,
    idea_grade: dict,
    source_grounding: dict,
) -> None:
    optional = manifest.get("optional_artifacts", {})
    feedback_name = optional.get("human_example_feedback")
    if not feedback_name:
        raise KernelValidationError("expected human_example_feedback optional artifact")
    feedback = read_yaml(run_dir / feedback_name)
    items = feedback.get("feedback", [])
    if not items:
        raise KernelValidationError("expected at least one human feedback item")
    target_id = required.get("target_id")
    target_type = required.get("target_type")
    labels = set(required.get("labels", []))
    matching = [
        item
        for item in items
        if (target_id is None or item.get("target_id") == target_id)
        and (target_type is None or item.get("target_type") == target_type)
    ]
    if not matching:
        raise KernelValidationError("missing expected human feedback target")
    if labels and not any(labels.issubset(set(item.get("labels", []))) for item in matching):
        raise KernelValidationError(f"missing expected human feedback labels {sorted(labels)}")
    if required.get("summary_required"):
        summary_name = optional.get("example_feedback_summary")
        if not summary_name:
            raise KernelValidationError("expected example_feedback_summary optional artifact")
        summary = read_yaml(run_dir / summary_name)
        summarized = set(summary.get("summarized_from", []))
        feedback_ids = {item.get("feedback_id") for item in matching}
        if not feedback_ids.intersection(summarized):
            raise KernelValidationError("feedback summary does not cite expected feedback")
    if required.get("no_status_upgrade"):
        if idea_grade.get("status") == "promising":
            raise KernelValidationError("human feedback upgraded idea status to promising")
        if idea_grade.get("survived_tests") or idea_grade.get("failed_tests"):
            raise KernelValidationError("human feedback changed survived/failed evidence")
        source_statuses = {
            ref.get("status")
            for ref in source_grounding.get("source_refs", [])
        } | {step.get("status") for step in source_grounding.get("steps", [])}
        if "source_backed" in source_statuses:
            raise KernelValidationError("human feedback upgraded source status")
