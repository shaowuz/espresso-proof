from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import typer

from espresso_proof.agents.llm_agents import external_agent_diagnostic, openai_agent_diagnostic
from espresso_proof.claim.claim_clear import (
    ClaimClearBlockedError,
    ensure_claim_clear_for_downstream,
)
from espresso_proof.kernel.contracts import default_agent_contracts
from espresso_proof.kernel.io import read_text, read_yaml
from espresso_proof.kernel.models import AgentContracts, ContinueDecision, WorkflowSpec
from espresso_proof.kernel.validators import KernelValidationError, validate_run
from espresso_proof.orchestration.run_dashboard import write_run_dashboard
from espresso_proof.orchestration.workflow_generator import generate_workflow_spec


_WINDOWS_RUN_PATH_LIMIT = 240


def _guard_windows_run_path_length(output_root: Path, project: str) -> None:
    """Fail early when a fresh run path is likely to exceed legacy MAX_PATH."""

    if os.name != "nt":
        return
    sample_run_id = f"{project}-YYYYMMDD-HHMMSS-xxxxxxxx"
    try:
        root = str(output_root.resolve(strict=False))
    except Exception:  # noqa: BLE001 - pathlib can reject WindowsPath on non-Windows tests
        root = str(output_root)
    sample = os.path.join(
        root,
        "projects",
        project,
        "runs",
        sample_run_id,
        "prover_rounds",
        "round_001_initial_llm_prover_review_attempt.yaml",
    )
    if len(sample) >= _WINDOWS_RUN_PATH_LIMIT:
        raise typer.BadParameter(
            "Windows run path would likely exceed MAX_PATH. Use a shorter "
            "--output-root such as C:\\tmp\\espresso_runs or a shorter --project."
        )


def _resolve_front_half_mode(*, provider: str, front_half: str, command_name: str) -> str:
    """Resolve the user-facing front-half setting.

    ``auto`` keeps deterministic stub runs on the developer path, while real
    providers use a real claim-understanding agent by default. ``run-flow`` keeps
    its legacy stub fixture default (mock) so old diagnostic tests stay stable.
    Explicitly pairing a real provider with mock/stub front-half remains
    available for fixture/debug parity and is surfaced to the user.
    """

    if front_half == "auto":
        if provider == "external":
            return "external"
        if provider == "openai":
            return "openai"
        return "mock" if command_name == "run-flow" else "stub"
    if provider in {"openai", "external"} and front_half in {"mock", "stub"}:
        typer.echo(
            f"{command_name}: using --front-half {front_half} with provider={provider}. "
            f"This run used {front_half} front-half; arbitrary math claims may stop at "
            "clarification. Omit --front-half to use the real claim-understanding "
            "agent, or pass --front-half external for the external adapter."
        )
    return front_half


def _refresh_process_status(run: Path) -> None:
    """Refresh AGENT_FLOW process projection after manual CLI artifact writes."""

    from espresso_proof.orchestration.agent_flow_status import (
        write_stage_status_projection,
    )

    write_stage_status_projection(run, include_artifact_hashes=False)
    validate_run(run)


def _mark_failure_patterns_done_if_board_exists(run: Path) -> None:
    """Keep manual failure-audit commands aligned with the visible process board."""

    from espresso_proof.kernel.agent_run_status import has_status, set_agent_status

    if not has_status(run):
        return
    now = datetime.now(UTC).isoformat()
    set_agent_status(
        run,
        "failure_patterns",
        "done",
        started_at=now,
        finished_at=now,
        increment_attempt=True,
    )


def _require_claim_clear_for_cli(run: Path) -> None:
    try:
        ensure_claim_clear_for_downstream(run, require_recorded_clear_gate=True)
    except ClaimClearBlockedError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _preflight_agent_provider(
    *,
    provider: str,
    model: str,
    api_key_env: str,
    external_agent_command_env: str,
    network_check: bool,
) -> dict | None:
    if provider == "stub":
        return None
    if provider == "openai":
        report = openai_agent_diagnostic(
            model=model,
            api_key_env=api_key_env,
            network_check=network_check,
        )
    elif provider == "external":
        report = external_agent_diagnostic(
            model=model,
            command_env=external_agent_command_env,
            network_check=network_check,
        )
    else:
        raise RuntimeError(f"unsupported provider for preflight: {provider}")
    if report["status"] not in {"configured", "ready"}:
        message = report.get("error") or report.get("message") or f"{provider} provider is not ready."
        raise RuntimeError(str(message))
    return report


def _refresh_derived_views(run: Path) -> None:
    """Rebuild the proof-search graph + dashboard after a command appends scoped
    evidence, so the memo, graph, and dashboard stay coherent for one run.

    Only refreshes views that ALREADY exist (never fabricates new artifacts), and
    fails closed on the graph: if the rebuilt graph cannot pass validation, drop it
    and keep the run valid. This never upgrades any status -- the graph re-validates
    through the same kernel gate and only reflects evidence the kernel already
    earned.
    """
    from espresso_proof.kernel.subgoal_graph import build_subgoal_graph, remove_subgoal_graph
    from espresso_proof.verification.theorem_condition_ledger import build_theorem_condition_ledger

    if (run / "proof_goal_graph.yaml").exists():
        try:
            # Refresh the advisory ledger before the graph reads it.
            build_theorem_condition_ledger(run)
            build_subgoal_graph(run)
            validate_run(run)
        except Exception:  # noqa: BLE001 - advisory view must fail closed, not block
            remove_subgoal_graph(run)
            validate_run(run)
    if (run / "research_dashboard.html").exists():
        write_run_dashboard(run)
    _refresh_process_status(run)


def _optional_artifact_path(run_dir: Path, optional_artifacts: dict, key: str) -> Path | None:
    rel_path = optional_artifacts.get(key)
    if not rel_path:
        return None
    path = run_dir / rel_path
    return path if path.exists() else None


def _focused_prover_loop_attempt(prover_loop: dict) -> dict:
    attempts = prover_loop.get("attempts", [])
    if not attempts:
        return {}
    selected_strategy_id = prover_loop.get("selected_strategy_id")
    candidates = [
        attempt
        for attempt in attempts
        if selected_strategy_id is None or attempt.get("strategy_id") == selected_strategy_id
    ]
    if not candidates:
        candidates = attempts
    return sorted(
        candidates,
        key=lambda attempt: (attempt.get("round_index", 0), attempt.get("attempt_id", "")),
    )[-1]


def _validate_seed_files() -> None:
    WorkflowSpec.model_validate(
        generate_workflow_spec("Hodge atom mirror symmetry", project_name="generated_hodge")
    )
    WorkflowSpec.model_validate(
        generate_workflow_spec("flat coherent base change", project_name="generated_base_change")
    )
    AgentContracts.model_validate(default_agent_contracts("scratch"))


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


def _state_for_human_decision(decision: ContinueDecision) -> str:
    if decision == ContinueDecision.continue_run:
        return "HUMAN_CHOICE_CONTINUE_SELECTED"
    if decision == ContinueDecision.revise:
        return "HUMAN_CHOICE_REVISE_SELECTED"
    return "HUMAN_CHOICE_STOP_SELECTED"


def _strategy_title(strategy_brief: dict, strategy_id: str) -> str:
    if strategy_id == "none":
        return "none"
    for strategy in strategy_brief.get("top_strategies", []):
        if strategy.get("id") == strategy_id:
            return strategy.get("title") or strategy_id
    return strategy_id


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
