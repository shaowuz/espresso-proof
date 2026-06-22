"""Real-math capability benchmark ledger.

This module is intentionally read-only. It inspects already-written run
artifacts, runs the normal kernel validator, and reports separate capability
labels. It never earns, upgrades, or repairs mathematical status.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from espresso_proof.claim.task_intent import infer_task_intent, is_witness_intent
from espresso_proof.kernel.io import read_text, read_yaml
from espresso_proof.kernel.proof_language import contains_proof_language
from espresso_proof.kernel.validators import validate_run

REAL_MATH_BENCHMARK_SCHEMA_VERSION = 1
REQUIRED_METADATA = ("provider", "model_fingerprint", "prompt_version", "repeat_count")
USEFUL_ROUTE_MIN_SCORE = 50

_USEFUL_ROUTE_STATUSES = {"promising", "fragile", "unclear"}
_SCOPED_CHECKED_STATUSES = {"instance_checked", "computation_checked"}
_FORMAL_STATUSES = {"lean_checked"}
_HUMAN_ATTESTED_STATUSES = {"human_attested", "human_confirmed"}


def build_real_math_capability_report(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a conservative capability report over existing run directories.

    Each case must provide:

    - ``case_id``: stable benchmark case identifier
    - ``run_dir``: path to an Espresso run directory
    - ``provider``, ``model_fingerprint``, ``prompt_version``, ``repeat_count``:
      provenance for the model/prompt attempt being measured

    The report deliberately separates "valid run" from mathematical progress:
    a kernel-valid run may still have no useful route, no checked scoped fact, no
    source-supported argument, and no formal/human-attested solution.
    """

    rows = [_case_row(case) for case in cases]
    total = len(rows)
    metrics = {
        "cases_total": total,
        "valid_run_rate": _rate(rows, "valid_run"),
        "useful_route_rate": _rate(rows, "useful_route_found"),
        "checked_scoped_fact_rate": _rate(rows, "checked_scoped_fact"),
        "source_supported_argument_rate": _rate(rows, "source_supported_argument"),
        "formal_solution_rate": _rate(rows, "formal_solution"),
        "human_attested_solution_rate": _rate(rows, "human_attested_solution"),
        "agent_solved_rate": _rate(rows, "agent_solved"),
        "real_agent_valid_run_rate": _rate(rows, "real_agent_valid_run"),
        "overclaim_count": sum(1 for row in rows if row["overclaim"]),
    }
    return {
        "schema_version": REAL_MATH_BENCHMARK_SCHEMA_VERSION,
        "metrics": metrics,
        "cases": rows,
    }


def case_from_run(
    run_dir: Path,
    *,
    case_id: str | None = None,
    provider: str | None = None,
    model_fingerprint: str | None = None,
    prompt_version: str,
    repeat_count: int,
) -> dict[str, Any]:
    """Build benchmark metadata for an existing run directory.

    Provider/model are inferred from ``llm_agent_run.yaml`` when present. The
    caller still supplies ``prompt_version`` and ``repeat_count`` explicitly so
    capability reports never hide provenance.
    """

    manifest = _read_yaml_if_exists(run_dir / "run_manifest.yaml") or {}
    llm_agent_run = _read_optional_yaml(
        run_dir, manifest.get("optional_artifacts") or {}, "llm_agent_run"
    ) or {}
    return {
        "case_id": case_id or run_dir.name,
        "run_dir": run_dir,
        "provider": provider or llm_agent_run.get("provider") or "",
        "model_fingerprint": model_fingerprint or llm_agent_run.get("model") or "",
        "prompt_version": prompt_version,
        "repeat_count": repeat_count,
    }


def _case_row(case: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in REQUIRED_METADATA if case.get(key) in (None, "")]
    if missing:
        raise ValueError(f"benchmark case missing metadata: {', '.join(missing)}")
    repeat_count = case["repeat_count"]
    if not isinstance(repeat_count, int) or repeat_count < 1:
        raise ValueError("benchmark case repeat_count must be a positive integer")

    run_dir = Path(case["run_dir"])
    manifest = _read_yaml_if_exists(run_dir / "run_manifest.yaml") or {}
    artifacts = manifest.get("artifacts") or {}
    optional = manifest.get("optional_artifacts") or {}

    validation_error = ""
    try:
        validate_run(run_dir)
        valid_run = True
    except Exception as exc:  # evaluation should classify invalid runs, not crash
        valid_run = False
        validation_error = f"{type(exc).__name__}: {exc}"

    final_report = _read_artifact_text(run_dir, artifacts, "final_report")
    proof_phrase = contains_proof_language(final_report)

    claim = _read_artifact_yaml(run_dir, artifacts, "locked_claim") or {}
    grade = _read_artifact_yaml(run_dir, artifacts, "idea_grade") or {}
    source_grounding = _read_artifact_yaml(run_dir, artifacts, "source_grounding") or {}
    strategy_brief = _read_artifact_yaml(run_dir, artifacts, "strategy_brief") or {}
    theorem_applicability = _read_optional_yaml(run_dir, optional, "theorem_applicability") or {}
    proof_verification = _read_optional_yaml(run_dir, optional, "proof_verification") or {}
    computation_verification = (
        _read_optional_yaml(run_dir, optional, "computation_verification") or {}
    )
    llm_agent_run = _read_optional_yaml(run_dir, optional, "llm_agent_run") or {}
    provider_diagnostic = llm_agent_run.get("provider_diagnostic") or {}
    provider_ready = _provider_ready(provider_diagnostic)
    provider_adapter_fallback_used = _adapter_fallback_used(provider_diagnostic)
    agent_call_fallback_used = _agent_call_fallback_used(llm_agent_run)
    adapter_fallback_used = provider_adapter_fallback_used or agent_call_fallback_used
    verified_agent_calls = _verified_agent_calls(llm_agent_run, str(case["provider"]))

    formal_solution = valid_run and _has_formal_solution(grade, proof_verification)
    human_attested = valid_run and _has_human_attestation(grade, proof_verification)
    overclaim = bool(proof_phrase) and not (formal_solution or human_attested)
    capability_valid = valid_run and not overclaim

    checked_scoped = capability_valid and _has_checked_scoped_fact(
        proof_verification, computation_verification
    )
    source_supported = capability_valid and _has_source_supported_argument(
        source_grounding, theorem_applicability
    )
    useful_route = capability_valid and _has_useful_route(strategy_brief, claim)
    real_agent_valid_run = (
        valid_run
        and str(case["provider"]) != "stub"
        and provider_ready
        and not adapter_fallback_used
        and verified_agent_calls
    )
    agent_solved = formal_solution

    return {
        "case_id": str(case.get("case_id") or run_dir.name),
        "run_dir": str(run_dir),
        "provider": str(case["provider"]),
        "model_fingerprint": str(case["model_fingerprint"]),
        "prompt_version": str(case["prompt_version"]),
        "repeat_count": repeat_count,
        "valid_run": valid_run,
        "validation_error": validation_error,
        "provider_ready": provider_ready,
        "adapter_fallback_used": adapter_fallback_used,
        "provider_adapter_fallback_used": provider_adapter_fallback_used,
        "agent_call_fallback_used": agent_call_fallback_used,
        "agent_call_verified_real_provider": verified_agent_calls,
        "agent_call_diagnostics_count": len(
            llm_agent_run.get("agent_call_diagnostics") or []
        )
        if isinstance(llm_agent_run, dict)
        else 0,
        "real_agent_valid_run": real_agent_valid_run,
        "useful_route_found": useful_route,
        "checked_scoped_fact": checked_scoped,
        "source_supported_argument": source_supported,
        "formal_solution": formal_solution,
        "human_attested_solution": human_attested,
        "agent_solved": agent_solved,
        "solved_full_claim": formal_solution or human_attested,
        "overclaim": overclaim,
        "overclaim_phrase": proof_phrase or "",
        "proof_language_status": str(grade.get("proof_language_status") or ""),
    }


def _rate(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if row[key]) / len(rows)


def _read_yaml_if_exists(path: Path) -> Any:
    if not path.exists():
        return None
    return read_yaml(path)


def _read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return read_text(path)


def _artifact_path(run_dir: Path, manifest_section: dict[str, Any], key: str) -> Path | None:
    rel = manifest_section.get(key)
    if not rel:
        return None
    return run_dir / str(rel)


def _read_artifact_yaml(
    run_dir: Path, artifacts: dict[str, Any], key: str
) -> dict[str, Any] | None:
    path = _artifact_path(run_dir, artifacts, key)
    return _read_yaml_if_exists(path) if path is not None else None


def _read_optional_yaml(
    run_dir: Path, optional: dict[str, Any], key: str
) -> dict[str, Any] | None:
    path = _artifact_path(run_dir, optional, key)
    return _read_yaml_if_exists(path) if path is not None else None


def _read_artifact_text(run_dir: Path, artifacts: dict[str, Any], key: str) -> str:
    path = _artifact_path(run_dir, artifacts, key)
    return _read_text_if_exists(path) if path is not None else ""


def _provider_ready(provider_diagnostic: dict[str, Any]) -> bool:
    return (
        provider_diagnostic.get("status") == "ready"
        and provider_diagnostic.get("network_check") == "passed"
    )


def _adapter_fallback_used(provider_diagnostic: dict[str, Any]) -> bool:
    structured = provider_diagnostic.get("structured_output") or {}
    if not isinstance(structured, dict):
        return False
    adapter = structured.get("adapter_diagnostic") or {}
    return isinstance(adapter, dict) and adapter.get("fallback_used") is True


def _agent_call_fallback_used(llm_agent_run: dict[str, Any]) -> bool:
    diagnostics = llm_agent_run.get("agent_call_diagnostics") or []
    if not isinstance(diagnostics, list):
        return True
    return any(
        isinstance(item, dict) and item.get("fallback_used") is True
        for item in diagnostics
    )


def _verified_agent_calls(llm_agent_run: dict[str, Any], provider: str) -> bool:
    if provider == "stub":
        return False
    diagnostics = llm_agent_run.get("agent_call_diagnostics") or []
    if provider == "openai":
        # Older OpenAI artifacts have no per-call adapter diagnostic, but a
        # successful call plus provider diagnostic is already a direct API path.
        if not diagnostics:
            return True
        return all(
            isinstance(item, dict)
            and item.get("adapter") == "openai_responses"
            and item.get("fallback_used") is not True
            and item.get("verified_real_provider") is True
            for item in diagnostics
        )
    if provider != "external" or not diagnostics:
        return False
    return all(
        isinstance(item, dict)
        and item.get("adapter") == "espresso_codex_agent"
        and item.get("parse_status") == "ok"
        and item.get("fallback_used") is not True
        and item.get("verified_real_provider") is True
        for item in diagnostics
    )


def _has_useful_route(
    strategy_brief: dict[str, Any], claim: dict[str, Any] | None = None
) -> bool:
    task_intent = infer_task_intent(claim)
    witness_task = is_witness_intent(task_intent)
    for route in strategy_brief.get("top_strategies") or []:
        if not isinstance(route, dict):
            continue
        if route.get("status") not in _USEFUL_ROUTE_STATUSES:
            continue
        score = route.get("score")
        if isinstance(score, int) and score >= USEFUL_ROUTE_MIN_SCORE:
            return True
        if (
            witness_task
            and isinstance(score, int)
            and score >= 40
            and _route_has_witness_markers(route)
        ):
            return True
    return False


def _route_has_witness_markers(route: dict[str, Any]) -> bool:
    text = " ".join(
        str(route.get(key) or "")
        for key in (
            "title",
            "why_this_route",
            "main_risk",
            "next_loop_plan",
            "notes",
        )
    ).lower()
    return any(
        marker in text
        for marker in (
            "counterexample",
            "witness",
            "tensor",
            "short exact",
            "multiplication by 2",
            "z/2",
            "mod 2",
            "non-injective",
            "noninjective",
        )
    )


def _has_checked_scoped_fact(
    proof_verification: dict[str, Any], computation_verification: dict[str, Any]
) -> bool:
    for entry in proof_verification.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("trusted") is True
            and entry.get("confirmed") is True
            and entry.get("earned_status") in _SCOPED_CHECKED_STATUSES
        ):
            return True
    for result in computation_verification.get("results") or []:
        if not isinstance(result, dict):
            continue
        if result.get("executed") is True and result.get("verdict") == "confirmed":
            return True
    return False


def _has_source_supported_argument(
    source_grounding: dict[str, Any], theorem_applicability: dict[str, Any]
) -> bool:
    if theorem_applicability.get("overall_status") != "all_applicable":
        return False

    backed_steps = [
        step
        for step in source_grounding.get("steps") or []
        if isinstance(step, dict) and step.get("status") == "source_backed"
    ]
    if not backed_steps:
        return False

    backed_step_ids = {str(step.get("proof_step_id") or "") for step in backed_steps}
    backed_step_ids.discard("")
    if not backed_step_ids:
        return False

    applicable_steps = set()
    for usage in theorem_applicability.get("usages") or []:
        if not isinstance(usage, dict):
            continue
        if (
            usage.get("proof_step_id") in backed_step_ids
            and usage.get("source_status") == "source_backed"
            and usage.get("applicability_status") == "applicable"
            and usage.get("blocks_source_backed") is False
        ):
            applicable_steps.add(usage.get("proof_step_id"))
    return bool(applicable_steps)


def _has_formal_solution(
    grade: dict[str, Any], proof_verification: dict[str, Any]
) -> bool:
    if grade.get("proof_language_status") in _FORMAL_STATUSES:
        return True
    return any(
        isinstance(entry, dict) and entry.get("earned_status") in _FORMAL_STATUSES
        for entry in proof_verification.get("entries") or []
    )


def _has_human_attestation(
    grade: dict[str, Any], proof_verification: dict[str, Any]
) -> bool:
    if grade.get("proof_language_status") in _HUMAN_ATTESTED_STATUSES:
        return True
    return any(
        isinstance(entry, dict) and entry.get("earned_status") in _HUMAN_ATTESTED_STATUSES
        for entry in proof_verification.get("entries") or []
    )
