"""Tests for the advisory "AI review passed" signal (espresso_proof.kernel.ai_review).

The signal is ADVISORY ONLY: it can never render green, never changes idea_grade
or any other status, and ``no_blocking_objection`` is earned only with >=2
independent reviewers, zero unresolved blocking objections, no admissible checked
refutation, and idea_grade.status != refuted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.kernel.ai_review import DISCLAIMER, assess_ai_review, build_ai_review
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.kernel.models import AIReviewAssessment, AIReviewVerdict
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.orchestration.orchestrator import orchestrate_run
from espresso_proof.kernel.validators import validate_run


# ---------------------------------------------------------------------------
# run fixtures + helpers
# ---------------------------------------------------------------------------
def _claim(tmp_path: Path) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(
        "Let p be a prime. Every group G of order p^2 is abelian.\n",
        encoding="utf-8",
    )
    return path


def _gate_run(tmp_path: Path) -> Path:
    result = orchestrate_run(
        project="ai_review_demo",
        input_path=_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
    )
    return Path(result["run_dir"])


def _manifest(run_dir: Path) -> dict:
    return read_yaml(run_dir / "run_manifest.yaml")


def _set_reviews(run_dir: Path, reviews: list[dict]) -> None:
    """Overwrite the verifier_reviews the signal reads with controlled reviews.

    The signal reads via ``_scoped_objection_reviews`` = the gate-time
    llm_agent_run PLUS every ``prover_rounds/round_*.yaml`` file, deduped by
    verifier_id. To control the scenario exactly we overwrite BOTH. (Runs touched
    this way are no longer internally consistent for the full ``validate_run``
    cross-checks, so these scenarios assert on ``assess_ai_review`` only.)"""
    optional = _manifest(run_dir)["optional_artifacts"]
    path = run_dir / optional["llm_agent_run"]
    lar = read_yaml(path)
    lar["verifier_reviews"] = reviews
    write_yaml(path, lar)
    rounds = run_dir / "prover_rounds"
    if rounds.is_dir():
        for round_path in sorted(rounds.glob("round_*.yaml")):
            data = read_yaml(round_path)
            if isinstance(data, dict) and "verifier_reviews" in data:
                data["verifier_reviews"] = reviews
                write_yaml(round_path, data)
    _sync_reviewer_pointers(run_dir, reviews)


def _clean_review(review: dict) -> dict:
    clean = dict(review)
    clean["verdict"] = "plausible_direction"
    clean["objections"] = []
    clean["missing_hypotheses"] = []
    clean["source_requests"] = []
    clean["scoped_objections"] = []
    clean["recommended_human_action"] = "continue"
    clean["status"] = "candidate"
    return clean


def _sync_reviewer_pointers(run_dir: Path, reviews: list[dict]) -> None:
    """Keep prover-loop reviewer summaries aligned with the supplied reviews."""

    optional = _manifest(run_dir)["optional_artifacts"]
    pl_rel = optional.get("prover_loop")
    if not pl_rel:
        return
    path = run_dir / pl_rel
    pl = read_yaml(path)
    by_id = {str(review.get("verifier_id")): review for review in reviews}
    for attempt in pl.get("attempts") or []:
        for pointer in attempt.get("reviewer_objections") or []:
            review = by_id.get(str(pointer.get("verifier_id")))
            if not review:
                continue
            pointer["verdict"] = review.get("verdict", pointer.get("verdict"))
            pointer["recommended_human_action"] = review.get(
                "recommended_human_action",
                pointer.get("recommended_human_action"),
            )
            pointer["objection_count"] = len(review.get("objections") or [])
    write_yaml(path, pl)


def _current_reviews(run_dir: Path) -> list[dict]:
    optional = _manifest(run_dir)["optional_artifacts"]
    path = run_dir / optional["llm_agent_run"]
    return list(read_yaml(path).get("verifier_reviews") or [])


def _clear_prover_blockers(run_dir: Path) -> None:
    """Empty every prover-loop attempt's blockers so no prover blocker counts as
    an unresolved objection (the base stub run records reviewer source-requests)."""
    optional = _manifest(run_dir)["optional_artifacts"]
    pl_rel = optional.get("prover_loop")
    if not pl_rel:
        return
    path = run_dir / pl_rel
    pl = read_yaml(path)
    for attempt in pl.get("attempts") or []:
        attempt["blockers"] = []
        attempt["blocker_details"] = []
    write_yaml(path, pl)
    # Keep the round artifacts consistent with the loop (validate_run cross-checks
    # report.reviewer_blockers == attempt.blockers).
    rounds = run_dir / "prover_rounds"
    if rounds.is_dir():
        for round_path in sorted(rounds.glob("round_*.yaml")):
            data = read_yaml(round_path)
            if isinstance(data, dict) and "reviewer_blockers" in data:
                data["reviewer_blockers"] = []
                write_yaml(round_path, data)


def _review(verifier_id: str, *, scoped: list[dict] | None = None) -> dict:
    return {
        "verifier_id": verifier_id,
        "provider": "stub",
        "model": "stub-model",
        "created_at": "2026-06-14T00:00:00+00:00",
        "verdict": "plausible_direction",
        "objections": [],
        "missing_hypotheses": [],
        "source_requests": [],
        "unsafe_language": [],
        "scoped_objections": scoped or [],
        "recommended_human_action": "continue",
        "status": "candidate",
    }


def _scoped_objection(severity: str) -> dict:
    return {
        "id": f"so_{severity.lower()}",
        "text": "The cited theorem's hypotheses are not checked against the claim.",
        "scope": "route",
        "scope_source": "explicit",
        "scope_inferred": False,
        "kind": "missing_source",
        "severity": severity,
    }


def _clean_default_run(run_dir: Path) -> None:
    """Keep the run's default 3 reviewers (so the full validate_run cross-checks
    still pass) and just clear the prover blockers + reviewer source-requests so
    nothing blocking remains -> a clean ``no_blocking_objection``."""
    _set_reviews(run_dir, [_clean_review(review) for review in _current_reviews(run_dir)])
    _clear_prover_blockers(run_dir)


# ---------------------------------------------------------------------------
# earn rule
# ---------------------------------------------------------------------------
def test_no_blocking_objection_earned_with_exactly_two_clean_reviewers(tmp_path: Path) -> None:
    # Exactly two distinct reviewers, zero objections -> earned (assess-only,
    # since shrinking reviewers breaks the unrelated >=3-role prover cross-check).
    run_dir = _gate_run(tmp_path)
    _set_reviews(
        run_dir,
        [_review("strategy_reviewer_001"), _review("evidence_reviewer_002")],
    )
    _clear_prover_blockers(run_dir)
    assessment = assess_ai_review(run_dir)
    assert assessment["verdict"] == AIReviewVerdict.no_blocking_objection.value
    assert assessment["reviewer_count"] == 2
    assert assessment["unresolved_objection_count"] == 0


def test_no_blocking_objection_earned_on_clean_default_run(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    _clean_default_run(run_dir)
    assessment = build_ai_review(run_dir)
    assert assessment["verdict"] == AIReviewVerdict.no_blocking_objection.value
    assert assessment["reviewer_count"] >= 2
    assert assessment["unresolved_objection_count"] == 0
    # advisory disclaimer is pinned and clearly not a verification
    assert assessment["disclaimer"] == DISCLAIMER
    assert "NOT a verification" in assessment["disclaimer"]


def test_blocking_scoped_objection_blocks_pass(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    _set_reviews(
        run_dir,
        [
            _review("strategy_reviewer_001", scoped=[_scoped_objection("P0")]),
            _review("evidence_reviewer_002"),
        ],
    )
    _clear_prover_blockers(run_dir)
    assessment = build_ai_review(run_dir)
    assert assessment["verdict"] == AIReviewVerdict.objections_found.value
    assert assessment["unresolved_objection_count"] >= 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("objections", ["Line-level proof gap."]),
        ("missing_hypotheses", ["Exact theorem applicability condition."]),
        ("source_requests", ["Need exact theorem locator."]),
    ],
)
def test_canonical_reviewer_blocking_lists_block_pass(
    tmp_path: Path, field: str, value: list[str]
) -> None:
    run_dir = _gate_run(tmp_path)
    first = _review("strategy_reviewer_001")
    first[field] = value
    _set_reviews(run_dir, [first, _review("evidence_reviewer_002")])
    _clear_prover_blockers(run_dir)
    assessment = assess_ai_review(run_dir)
    assert assessment["verdict"] == AIReviewVerdict.objections_found.value
    assert assessment["unresolved_objection_count"] >= 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verdict", "blocked"),
        ("verdict", "needs_revision"),
        ("verdict", "needs_sources"),
        ("status", "blocked"),
        ("status", "needs_human"),
        ("recommended_human_action", "stop"),
        ("recommended_human_action", "revise"),
    ],
)
def test_canonical_reviewer_blocking_status_fields_block_pass(
    tmp_path: Path, field: str, value: str
) -> None:
    run_dir = _gate_run(tmp_path)
    first = _review("strategy_reviewer_001")
    first[field] = value
    _set_reviews(run_dir, [first, _review("evidence_reviewer_002")])
    _clear_prover_blockers(run_dir)
    assessment = assess_ai_review(run_dir)
    assert assessment["verdict"] == AIReviewVerdict.objections_found.value
    assert assessment["unresolved_objection_count"] >= 1


def test_prover_blocker_blocks_pass(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    _set_reviews(
        run_dir,
        [_review("strategy_reviewer_001"), _review("evidence_reviewer_002")],
    )
    # Do NOT clear prover blockers: the base stub run records a reviewer
    # source-request blocker, which must keep the pass from being earned.
    assessment = build_ai_review(run_dir)
    assert assessment["unresolved_objection_count"] >= 1
    assert assessment["verdict"] == AIReviewVerdict.objections_found.value


def test_single_reviewer_is_insufficient(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    _set_reviews(run_dir, [_review("strategy_reviewer_001")])
    _clear_prover_blockers(run_dir)
    assessment = build_ai_review(run_dir)
    assert assessment["verdict"] == AIReviewVerdict.insufficient_review.value
    assert assessment["reviewer_count"] == 1


def test_refuted_grade_never_passes(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    _clean_default_run(run_dir)
    grade_path = run_dir / "idea_grade.yaml"
    grade = read_yaml(grade_path)
    grade["status"] = "refuted"
    write_yaml(grade_path, grade)
    assessment = assess_ai_review(run_dir)
    assert assessment["verdict"] != AIReviewVerdict.no_blocking_objection.value
    assert assessment["verdict"] == AIReviewVerdict.objections_found.value


def test_malformed_counterexamples_artifact_fails_closed(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    _clean_default_run(run_dir)
    counterexamples = run_dir / _manifest(run_dir)["artifacts"]["counterexamples"]
    counterexamples.write_text("not: [valid", encoding="utf-8")
    with pytest.raises(Exception, match="counterexamples artifact is malformed"):
        assess_ai_review(run_dir)


# ---------------------------------------------------------------------------
# model is status-free + fails closed on smuggled fields
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("extra", ["source_backed", "status", "checked", "idea_grade", "trust"])
def test_extra_status_field_rejected(extra: str) -> None:
    base = {
        "schema_version": 1,
        "claim_id": "claim_001",
        "run_id": "run_x",
        "generated_at": "2026-06-14T00:00:00+00:00",
        "verdict": "no_blocking_objection",
        "reviewer_count": 2,
        "dimensions_reviewed": [],
        "unresolved_objection_count": 0,
        "disclaimer": DISCLAIMER,
        "notes": [],
    }
    # valid baseline parses
    AIReviewAssessment.model_validate(base)
    with pytest.raises(ValidationError):
        AIReviewAssessment.model_validate({**base, extra: True})


# ---------------------------------------------------------------------------
# build does not mutate idea_grade
# ---------------------------------------------------------------------------
def test_build_does_not_change_idea_grade(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    _clean_default_run(run_dir)
    grade_before = (run_dir / "idea_grade.yaml").read_bytes()
    build_ai_review(run_dir)
    grade_after = (run_dir / "idea_grade.yaml").read_bytes()
    assert grade_after == grade_before


# ---------------------------------------------------------------------------
# memo render: human text + "not a verification", no raw enum
# ---------------------------------------------------------------------------
def test_memo_renders_human_text_no_raw_enum(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    _clean_default_run(run_dir)
    build_ai_review(run_dir)
    validate_run(run_dir)
    memo = build_concise_memo(run_dir)
    assert "independent reviewers found no blocking objection" in memo
    assert "advisory — not a verification" in memo
    assert "not a verification" in memo.lower()
    # the raw verdict enum value is never printed
    assert "no_blocking_objection" not in memo


def test_memo_renders_insufficient(tmp_path: Path) -> None:
    # No LLM reviewer artifacts -> insufficient, while the run remains internally
    # valid because there is no prover-loop cross-check to satisfy.
    run_dir = run_mock_critique("ai_review_demo", _claim(tmp_path), tmp_path)
    build_ai_review(run_dir)
    memo = build_concise_memo(run_dir)
    assert "not enough independent reviewers to assess" in memo
    assert "insufficient_review" not in memo


# ---------------------------------------------------------------------------
# idempotent + validate_run passes
# ---------------------------------------------------------------------------
def test_build_is_idempotent(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    _clean_default_run(run_dir)
    build_ai_review(run_dir)
    first = (run_dir / "ai_review.yaml").read_bytes()
    build_ai_review(run_dir)
    second = (run_dir / "ai_review.yaml").read_bytes()
    assert first == second


def test_validate_run_passes_after_build(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    _clean_default_run(run_dir)
    build_ai_review(run_dir)
    validate_run(run_dir)  # must not raise


def test_validate_run_fails_closed_on_malformed_assessment(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    _clean_default_run(run_dir)
    build_ai_review(run_dir)
    # smuggle a forbidden status field directly into the persisted artifact
    path = run_dir / "ai_review.yaml"
    doc = read_yaml(path)
    doc["status"] = "source_backed"
    write_yaml(path, doc)
    with pytest.raises(Exception):
        validate_run(run_dir)


def test_validate_run_fails_closed_on_stale_assessment_after_blocker_added(
    tmp_path: Path,
) -> None:
    run_dir = _gate_run(tmp_path)
    _clean_default_run(run_dir)
    build_ai_review(run_dir)
    validate_run(run_dir)
    optional = _manifest(run_dir)["optional_artifacts"]
    path = run_dir / optional["llm_agent_run"]
    lar = read_yaml(path)
    lar["verifier_reviews"][0]["source_requests"] = ["Need exact theorem locator."]
    write_yaml(path, lar)
    with pytest.raises(Exception, match="ai_review does not match current"):
        validate_run(run_dir)


def test_validate_run_fails_closed_on_stale_assessment_after_refuted_grade(
    tmp_path: Path,
) -> None:
    run_dir = _gate_run(tmp_path)
    _clean_default_run(run_dir)
    build_ai_review(run_dir)
    validate_run(run_dir)
    grade_path = run_dir / "idea_grade.yaml"
    grade = read_yaml(grade_path)
    grade["status"] = "refuted"
    write_yaml(grade_path, grade)
    with pytest.raises(Exception, match="ai_review does not match current"):
        validate_run(run_dir)
