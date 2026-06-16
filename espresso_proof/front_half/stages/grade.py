"""Front-half stage: grade (Phase 04 physical split)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ...hypothesis_normalization import normalize_ambient_object_hypotheses
from ...io import read_yaml, write_yaml
from ...llm_agents import LLMClient, load_agent_context, safe_list, safe_text
from ...memo import build_concise_memo
from ...memory import build_grade_memory_entry, load_memory_index, write_run_memory
from ...models import (
    Claim,
    ComputationChecks,
    CounterexampleReport,
    ExampleSelection,
    FrontHalfRunReport,
    HypothesisLedger,
    IdeaGrade,
    LiteratureReviewReport,
    MemoryEntry,
    Review,
    SourceGroundingReport,
    StrategyBrief,
    StrategySynthesis,
    TestReport,
)
from ...strategy_rules import (
    HIGH_SCORE_THRESHOLD,
    STANDARD_SCORE_LIFT_LIMIT,
    collect_strategy_rule_violations,
)
from ...validators import validate_run
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
    from ...surface_warnings import refresh_final_report_disclosures

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

    # Signals (read-only; never upgrade any status).
    source_backed_steps = any(
        s.get("status") == "source_backed" for s in source.get("steps", [])
    )
    source_gap = (not source_backed_steps) or bool(current.get("source_gaps"))
    missing_hyps = bool(current.get("missing_hypotheses")) or bool(
        ledger.get("missing_or_unclear")
    )
    counterexample_pressure = len(counterexamples) > 0
    # A refutation requires an admissible all-hypotheses counterexample that is
    # actually checked/refuting — the front half never produces one.
    admissible_refutation = any(
        c.get("satisfies_all_claim_hypotheses") is True and c.get("refutes_claim") is True
        for c in counterexamples
    )
    theorem_applicability_failure = False  # no theorem-applicability artifact in the front half

    review_note = None
    if precision != "precise":
        # Vague/ambiguous claim: keep it at claim-locking.
        status = "unclear"
    elif admissible_refutation:
        status = "refuted"
    else:
        serious = sum(
            [bool(missing_hyps), counterexample_pressure, theorem_applicability_failure]
        )
        status = "likely_false" if serious >= 2 else "fragile"
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
        "fragile": "Recomputed from live artifacts: the claim is precise, but the route has source gaps / missing hypotheses / counterexample pressure and no checked proof.",
        "likely_false": "Recomputed from live artifacts: the claim is precise but faces multiple serious pressures; no checked counterexample has been verified.",
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

    # SAFETY/CONSISTENCY: recomputing the grade can invalidate a seeded
    # strategy_synthesis/brief whose routes were scored against the OLD grade
    # (e.g. the hodge_atoms mock seeds high-scoring non-lock routes). The
    # post-stage validate_run would then trip strategy_rules before the strategy
    # stage rewrites them. Reconcile to the single safe claim-lock route, which is
    # valid under the strategy rules for ANY grade; the strategy stage (next)
    # regenerates the real routes. This never invents support or upgrades status.
    run_id = context["manifest"]["run_id"]
    lock_route = _safe_lock_route(grade)
    synthesis = {
        "claim_id": claim_id,
        "synthesized_from": [
            run_id,
            "literature_review.yaml",
            "tests.yaml",
            "counterexamples.yaml",
            "source_grounding.yaml",
            "review.yaml",
            "idea_grade.yaml",
            "loop_control.yaml",
        ],
        "routes": [lock_route],
        "discarded_routes": [],
        "synthesis_note": "Strategy routes reset to claim-locking pending the strategy stage after regrade.",
    }
    brief = {
        "claim_id": claim_id,
        "generated_after_runs": [run_id],
        "top_strategies": [lock_route],
        "also_considered": [],
        "question_for_mathematician": "Which route should receive time, if any?",
        "allowed_human_choices": [
            {"strategy_id": lock_route["id"], "effort_options": [lock_route["estimated_effort"]]},
            {"strategy_id": "none", "effort_options": ["quick_test"]},
        ],
    }
    StrategySynthesis.model_validate(synthesis)
    StrategyBrief.model_validate(brief)
    write_yaml(run_dir / artifacts["strategy_synthesis"], synthesis)
    write_yaml(run_dir / artifacts["strategy_brief"], brief)

    notes = [f"regrade: precision={precision} -> status={status} (score {score})"]
    if review_note:
        notes.append(review_note)
    return {"unsafe_language": [], "notes": notes}
