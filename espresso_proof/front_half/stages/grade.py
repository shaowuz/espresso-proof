"""Front-half stage: grade (Phase 04 physical split)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.claim.hypothesis_normalization import normalize_ambient_object_hypotheses
from espresso_proof.claim.task_intent import infer_task_intent, is_witness_intent
from espresso_proof.kernel.io import read_yaml, write_text, write_yaml
from espresso_proof.agents.llm_agents import LLMClient, load_agent_context, safe_list, safe_text
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.memory.memory import build_grade_memory_entry, load_memory_index, write_run_memory
from espresso_proof.kernel.models import (
    Claim,
    ComputationChecks,
    CounterexampleReport,
    ExampleSelection,
    FrontHalfRunReport,
    HypothesisLedger,
    IdeaGrade,
    LiteratureReviewReport,
    LoopControl,
    MemoryEntry,
    Review,
    SourceGroundingReport,
    StrategyBrief,
    StrategySynthesis,
    TestReport,
)
from espresso_proof.kernel.strategy_rules import (
    HIGH_SCORE_THRESHOLD,
    STANDARD_SCORE_LIFT_LIMIT,
    collect_strategy_rule_violations,
    score_lift_limit,
)
from espresso_proof.kernel.validators import validate_run
from ..registry import register_stage
from ..safety import (
    _CANDIDATE_SOURCE_STATUSES,
    _EXPLORATION_MODES,
    _HYPOTHESIS_STATUSES,
    _IDEA_STATUSES,
    _LITERATURE_RELEVANCE,
    _LOCATOR_KINDS,
    _NONTRIVIALITY,
    _coerce_satisfies,
    _default_investigation_route,
    _is_vagueness_text,
    _neutralize_review_if_failed,
    _normalize_locator,
    _safe_lock_route,
)


_GRADE_SYSTEM = (
    "You are the Espresso Proof Grade agent. Summarize the run's evidence "
    "HONESTLY for the mathematician: reasons, source gaps, missing hypotheses, "
    "repair options, and the next cheapest test. You may NOT upgrade proof "
    "status, declare anything proved, or count unverified examples as evidence. "
    "The kernel keeps the status/score; you provide the qualitative reasoning. "
    "Respond with ONLY a JSON object."
)

_GRADE_OUTPUT_SCHEMA = {
    "reasons": ["string"],
    "missing_hypotheses": ["string"],
    "source_gaps": ["string"],
    "repair_options": ["string"],
    "next_cheapest_test": "string",
}


@register_stage("grade_memory_memo")
def _grade_memory_memo(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    claim = context["claim"]
    claim_id = claim["id"]
    current = dict(context.get("idea_grade") or {})
    counterexamples = (context.get("counterexamples") or {}).get("tests", [])

    payload = {
        "task": "summarize_grade_reasoning",
        "claim": {"normalized_statement": claim.get("normalized_statement", "")},
        "current_status": current.get("status", "unclear"),
        "required_json_schema": _GRADE_OUTPUT_SCHEMA,
        "rules": [
            "do not upgrade proof status or declare anything proved",
            "unverified examples are not evidence",
        ],
    }
    raw = client.complete_json(
        system=_GRADE_SYSTEM, prompt=json.dumps(payload, indent=2)
    ) or {}

    # SAFETY: the kernel-controlled fields (status, score, confidence,
    # proof_language_status) are PRESERVED from the existing grade — they are
    # already consistent with the review/final_report/strategy and must not be
    # upgraded by agent prose. Only the qualitative reasoning is refreshed, and
    # evidence lists are forced empty (the front half verifies nothing).
    grade = {
        "claim_id": claim_id,
        "status": current.get("status", "unclear"),
        "score": int(current.get("score", 0) or 0),
        "confidence": current.get("confidence", "low"),
        "reasons": safe_list(raw.get("reasons")) or list(current.get("reasons") or ["candidate run; not verified"]),
        "survived_tests": [],  # SAFETY: no checked evidence in the front half
        "failed_tests": [],    # SAFETY: no checked evidence in the front half
        "counterexample_candidates": [c["id"] for c in counterexamples],
        "missing_hypotheses": safe_list(raw.get("missing_hypotheses"))
        or list(current.get("missing_hypotheses") or []),
        "source_gaps": safe_list(raw.get("source_gaps")) or list(current.get("source_gaps") or []),
        "repair_options": safe_list(raw.get("repair_options"))
        or list(current.get("repair_options") or []),
        "next_cheapest_test": safe_text(raw.get("next_cheapest_test", ""))
        or current.get("next_cheapest_test", "Lock or pressure-test the claim."),
        "human_review_required": True,
        # SAFETY: never upgrade the proof-language status from the front half.
        "proof_language_status": current.get("proof_language_status", "idea_sketch"),
    }
    IdeaGrade.model_validate(grade)
    project_dir = run_dir.parents[1]
    run_id = context["manifest"]["run_id"]
    MemoryEntry.model_validate(
        build_grade_memory_entry(
            grade,
            read_yaml(run_dir / artifacts["strategy_brief"]),
            project_id=project_dir.name,
            run_id=run_id,
        )
    )
    load_memory_index(project_dir)
    write_yaml(run_dir / artifacts["idea_grade"], grade)

    # 21 Memory: refresh advisory memory from the (now live) artifacts. Every
    # entry is advisory-only (MemoryEntry has no status field).
    write_run_memory(
        project_dir,
        run_dir,
        project_id=project_dir.name,
        run_id=run_id,
    )
    from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures

    refresh_final_report_disclosures(run_dir)

    # 22 Concise Answer: ensure the memo renders from the live content (read-only;
    # raises here if the memo cannot be built, surfacing real breakage).
    build_concise_memo(run_dir)

    return {
        "unsafe_language": [],
        "notes": [
            "grade refreshed reasons/source_gaps from live content (status/score "
            "preserved, no upgrade); advisory memory written; memo renders"
        ],
    }


# ---------------------------------------------------------------------------
# Evidence-aware regrade (runs before strategy; activates precise claims)
# ---------------------------------------------------------------------------

# Conservative scores (all strictly < HIGH_SCORE_THRESHOLD; high score still
# requires real checked/source-backed support, which the front half never has).
_REGRADE_SCORES = {
    "unclear": 10,
    "fragile": 40,
    "likely_false": 25,
    "refuted": 5,
}


@register_stage("regrade")
def _regrade(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    claim = context["claim"]
    claim_id = claim["id"]
    ledger = context.get("hypothesis_ledger") or {}
    current = dict(context.get("idea_grade") or {})
    counterexamples = (context.get("counterexamples") or {}).get("tests", [])
    source = context.get("source_grounding") or {}
    review = context.get("review") or {}

    precision = ledger.get("claim_precision", "unclear")
    task_intent = infer_task_intent(claim)
    witness_intent = is_witness_intent(task_intent)

    # Signals (read-only; never upgrade any status).
    source_backed_steps = any(
        s.get("status") == "source_backed" for s in source.get("steps", [])
    )
    source_gap = (not source_backed_steps) or bool(current.get("source_gaps"))
    counterexample_pressure = len(counterexamples) > 0
    # A refutation requires an admissible all-hypotheses counterexample that is
    # actually checked/refuting — the front half never produces one.
    admissible_refutation = any(
        c.get("satisfies_all_claim_hypotheses") is True and c.get("refutes_claim") is True
        for c in counterexamples
    )
    theorem_applicability_failure = _has_theorem_applicability_failure(run_dir, context)

    review_note = None
    if precision != "precise":
        # Vague/ambiguous claim: keep it at claim-locking.
        status = "unclear"
    elif admissible_refutation:
        status = "refuted"
    else:
        # likely_false requires real negative pressure, not merely missing
        # support. A confirmed machine check vetoes likely_false, but never
        # upgrades this front-half grade beyond fragile.
        has_confirmed_machine_check = _has_confirmed_machine_check(run_dir, context)
        negative_evidence = (
            (counterexample_pressure and not witness_intent)
            or theorem_applicability_failure
        )
        status = (
            "likely_false"
            if negative_evidence and not has_confirmed_machine_check
            else "fragile"
        )
        # Precise claim: relax the placeholder failed review so meaningful routes
        # can activate (real reviewers still run later), and scrub stale
        # "too vague to test" placeholder text that no longer applies.
        new_review = _neutralize_review_if_failed(review, precise_claim=True)
        if new_review is not None:
            write_yaml(run_dir / artifacts["review"], new_review)
            review_note = "neutralized placeholder failed review (front-half pending)"

    score = _REGRADE_SCORES.get(status, 10)
    reason = {
        "unclear": "Recomputed from live artifacts: the claim is not precise enough to study safely.",
        "fragile": (
            "Recomputed from live artifacts: candidate witness/counterexample "
            "constructions are present for this task, but they remain unchecked "
            "and cannot count as proof dependencies."
            if witness_intent and counterexample_pressure
            else "Recomputed from live artifacts: the claim is precise, but the route has source gaps / missing hypotheses / counterexample pressure and no checked proof."
        ),
        "likely_false": "Recomputed from live artifacts: the claim is precise but faces unchecked counterexample pressure or theorem misuse; no confirming machine check or checked counterexample has been verified.",
        "refuted": "Recomputed from live artifacts: an admissible, checked counterexample violates the conclusion.",
    }[status]

    grade = {
        "claim_id": claim_id,
        "status": status,
        "score": score,
        "confidence": current.get("confidence", "low"),
        "reasons": [reason] + list(current.get("reasons") or []),
        "survived_tests": [],  # SAFETY: no checked evidence in the front half
        "failed_tests": [],
        "counterexample_candidates": [c["id"] for c in counterexamples],
        "missing_hypotheses": list(current.get("missing_hypotheses") or [])
        or list(ledger.get("missing_or_unclear") or []),
        "source_gaps": list(current.get("source_gaps") or [])
        or (["No step is reference-supported yet."] if source_gap else []),
        "repair_options": list(current.get("repair_options") or []),
        "next_cheapest_test": current.get("next_cheapest_test", "")
        or "Test the leading route on small/canonical cases and pin its key lemma.",
        "human_review_required": True,
        # SAFETY: never upgrade the proof-language status from the front half.
        "proof_language_status": current.get("proof_language_status", "idea_sketch"),
    }
    IdeaGrade.model_validate(grade)
    write_yaml(run_dir / artifacts["idea_grade"], grade)

    reconciled = _reconcile_strategy_after_regrade(run_dir, artifacts, grade)
    scaffold_reconciled = 0
    if precision == "precise":
        scaffold_reconciled = _reconcile_precise_claim_scaffold_artifacts(
            run_dir, artifacts
        )

    notes = [
        f"regrade: precision={precision}, task_intent={task_intent} -> "
        f"status={status} (score {score})"
    ]
    if reconciled:
        notes.append(f"reconciled {reconciled} scaffold strategy artifact(s)")
    if scaffold_reconciled:
        notes.append(f"reconciled {scaffold_reconciled} stale scaffold artifact(s)")
    if review_note:
        notes.append(review_note)
    return {"unsafe_language": [], "notes": notes}


_GENERIC_MISSING_HYPOTHESES = {"objects", "hypotheses", "conclusion"}


def _is_stale_claim_vagueness_text(text: object) -> bool:
    lowered = str(text or "").lower()
    return _is_vagueness_text(lowered) or any(
        marker in lowered
        for marker in (
            "claim is unclear",
            "needs restatement",
            "too vague for test selection",
            "too vague for productive looping",
        )
    )


def _only_generic_missing_hypotheses(items: object) -> bool:
    values = {str(item).strip().lower() for item in (items or []) if str(item).strip()}
    return bool(values) and values.issubset(_GENERIC_MISSING_HYPOTHESES)


def _reconcile_precise_claim_scaffold_artifacts(run_dir: Path, artifacts: dict) -> int:
    """Clear stale generic scaffold text after a live claim-clear decision.

    The generic scaffolder starts arbitrary claims in a safe "unclear" posture.
    Once the front half has locked a precise claim, those stale placeholders must
    not keep telling the UI/tests/memo that the same claim is too vague. This is
    display/control hygiene only: it does not create checked tests, source
    support, proof status, or a reviewer pass.
    """

    changed = 0

    tests_path = run_dir / artifacts["tests"]
    if tests_path.exists():
        tests = read_yaml(tests_path) or {}
        if _is_stale_claim_vagueness_text(tests.get("summary")) and not tests.get("tests"):
            tests["target"] = "claim"
            tests["summary"] = (
                "No generated tests yet for this clear claim; later evidence/prover "
                "stages should pressure-test the candidate routes."
            )
            TestReport.model_validate(tests)
            write_yaml(tests_path, tests)
            changed += 1

    loop_path = run_dir / artifacts["loop_control"]
    if loop_path.exists():
        loop = read_yaml(loop_path) or {}
        if loop.get("should_stop") is True and any(
            _is_stale_claim_vagueness_text(reason)
            for reason in loop.get("stop_reasons", [])
        ):
            loop["should_stop"] = False
            loop["stop_reasons"] = []
            loop["next_action"] = "run_strategy_brief"
            LoopControl.model_validate(loop)
            write_yaml(loop_path, loop)
            changed += 1

    review_path = run_dir / artifacts["review"]
    if review_path.exists():
        review = read_yaml(review_path) or {}
        if _only_generic_missing_hypotheses(review.get("missing_hypotheses")):
            review["missing_hypotheses"] = []
            Review.model_validate(review)
            write_yaml(review_path, review)
            changed += 1

    final_path = run_dir / artifacts["final_report"]
    if final_path.exists():
        text = final_path.read_text(encoding="utf-8")
        base = text.split("\n\n## Current Disclosure", 1)[0]
        if _is_stale_claim_vagueness_text(base):
            write_text(
                final_path,
                "Front-half accepted the claim as clear and recorded candidate "
                "planning artifacts. Trust labels remain with the validated "
                "artifacts below.\n",
            )
            from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures

            refresh_final_report_disclosures(run_dir)
            changed += 1

    return changed


def _has_theorem_applicability_failure(run_dir: Path, context: dict) -> bool:
    theorem_applicability = context.get("theorem_applicability")
    if theorem_applicability is None:
        optional = (context.get("manifest") or {}).get("optional_artifacts", {})
        rel_path = optional.get("theorem_applicability")
        if rel_path:
            theorem_applicability = read_yaml(run_dir / rel_path)
    if not isinstance(theorem_applicability, dict):
        return False
    if theorem_applicability.get("overall_status") in {"blocked", "misapplied"}:
        return True
    usages = list(theorem_applicability.get("usages") or []) + list(
        theorem_applicability.get("ledger_usages") or []
    )
    return any(
        isinstance(usage, dict)
        and usage.get("applicability_status") in {"blocked", "misapplied"}
        for usage in usages
    )


def _has_confirmed_machine_check(run_dir: Path, context: dict) -> bool:
    current_claim_id = (context.get("claim") or {}).get("id")
    if not current_claim_id:
        return False
    checks = (context.get("computation_checks") or {}).get("checks", [])
    checked_specs = {
        check.get("id"): (check.get("machine_check") or {})
        for check in checks
        if check.get("id")
        and check.get("machine_check") is not None
        and check.get("status") == "checked"
        and check.get("claim_id") == current_claim_id
    }
    if not checked_specs:
        return False

    verification = context.get("computation_verification")
    if verification is None:
        optional = (context.get("manifest") or {}).get("optional_artifacts", {})
        rel_path = optional.get("computation_verification")
        if rel_path:
            verification = read_yaml(run_dir / rel_path)
    if not isinstance(verification, dict):
        return False

    for result in verification.get("results", []):
        if not isinstance(result, dict):
            continue
        check_id = result.get("check_id")
        spec = checked_specs.get(check_id)
        if (
            spec is not None
            and result.get("verdict") == "confirmed"
            and result.get("executed") is True
            and result.get("claim_id") == current_claim_id
            and result.get("kind") == spec.get("kind")
            and result.get("expression") == spec.get("expression")
            and result.get("expected") == spec.get("expected")
        ):
            return True
    return False


_STRATEGY_ROUTE_ARTIFACTS = (
    ("strategy_synthesis", "routes"),
    ("strategy_brief", "top_strategies"),
)
_STRATEGY_MODEL_BY_ARTIFACT = {
    "strategy_synthesis": StrategySynthesis,
    "strategy_brief": StrategyBrief,
}


def _reconcile_strategy_after_regrade(run_dir: Path, artifacts: dict, grade: dict) -> int:
    """Keep scaffold strategy artifacts valid after the grade is recomputed."""
    status = grade.get("status", "unclear")
    idea_score = int(grade.get("score", 0) or 0)
    changed_artifacts = 0

    if status == "unclear":
        lock_route = _safe_lock_route(grade)
        allowed = [
            {"strategy_id": lock_route["id"], "effort_options": [lock_route["estimated_effort"]]},
            {"strategy_id": "none", "effort_options": ["quick_test"]},
        ]
        for artifact_key, routes_key in _STRATEGY_ROUTE_ARTIFACTS:
            path = run_dir / artifacts[artifact_key]
            if not path.exists():
                continue
            doc = read_yaml(path) or {}
            doc[routes_key] = [dict(lock_route)]
            if artifact_key == "strategy_synthesis":
                doc["discarded_routes"] = []
                doc["synthesis_note"] = (
                    "Strategy routes reset to claim-locking pending the strategy stage after regrade."
                )
            else:
                doc["also_considered"] = []
                doc["allowed_human_choices"] = allowed
            _STRATEGY_MODEL_BY_ARTIFACT[artifact_key].model_validate(doc)
            write_yaml(path, doc)
            changed_artifacts += 1
        return changed_artifacts

    for artifact_key, routes_key in _STRATEGY_ROUTE_ARTIFACTS:
        path = run_dir / artifacts[artifact_key]
        if not path.exists():
            continue
        doc = read_yaml(path) or {}
        changed = False
        for route in doc.get(routes_key, []):
            if not isinstance(route, dict):
                continue
            cap = idea_score + score_lift_limit(route)
            if int(route.get("score", 0) or 0) > cap:
                route["score"] = max(0, cap)
                changed = True
        if changed:
            _STRATEGY_MODEL_BY_ARTIFACT[artifact_key].model_validate(doc)
            write_yaml(path, doc)
            changed_artifacts += 1
    return changed_artifacts
