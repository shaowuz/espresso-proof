"""Phase 6 Slice A: kernel gate for the agent_consensus_mock_checked proof status.

The status is amber/advisory but it OCCUPIES a proof_language_status slot, so it gets
the same fail-closed provenance treatment as source_checked_argument: a UNANIMOUS
adversarial consensus by the three actual reviewers, hash-bound to the current
claim/source/review/prover_loop (revoked on any change), and never compatible with a
failed reviewer, a refuted claim, or a perfect score.

These are direct unit tests of the kernel gate functions + the model validator (no
fragile full-run fixture surgery): the model validator and the two gate functions are
where the trust boundary lives.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from espresso_proof.kernel.models import (
    AgentConsensusEntry,
    AgentConsensusReview,
    AgentConsensusVerdict,
    IdeaGrade,
    IdeaStatus,
    ProofLanguageStatus,
    Review,
    ReviewerReport,
    ReviewerVerdict,
    SourceGroundingReport,
)
from espresso_proof.kernel.pressure_status import artifact_revision_hash
from espresso_proof.kernel.validators import (
    KernelValidationError,
    _prover_status_smuggling_field,
    _validate_agent_consensus_review,
    _validate_grade_10_requires_global_proof_status,
    _validate_grade_proof_language_backed,
)

_ROLES = [
    ("strategy_reviewer", "proof_strategy"),
    ("evidence_reviewer", "examples_counterexamples"),
    ("line_proof_reviewer", "line_by_line_proof"),
]


def _entries(verdicts: dict | None = None) -> list[AgentConsensusEntry]:
    verdicts = verdicts or {}
    return [
        AgentConsensusEntry(
            reviewer_id=rid,
            verdict=verdicts.get(rid, AgentConsensusVerdict.no_fatal_flaw_found),
            looked_for="logical gaps / false steps / misapplied theorems",
            finding="no fatal flaw found",
        )
        for rid, _ in _ROLES
    ]


def _consensus(*, consensus: bool = True, verdicts=None, hashes=None) -> AgentConsensusReview:
    h = hashes or {}
    return AgentConsensusReview(
        claim_id="c1",
        run_id="r1",
        generated_at="t",
        bound_claim_hash=h.get("claim", ""),
        bound_source_grounding_hash=h.get("sg", ""),
        bound_review_hash=h.get("rev", ""),
        bound_prover_loop_hash=h.get("pl", ""),
        entries=_entries(verdicts),
        consensus=consensus,
        disclaimer="agent consensus, NOT machine-verified",
    )


def _review(verdicts=None) -> Review:
    verdicts = verdicts or {}
    reviewers = [
        ReviewerReport(
            reviewer_id=rid,
            scope=scope,
            verdict=verdicts.get(rid, ReviewerVerdict.passed),
            summary="ok",
            severity="low",
        )
        for rid, scope in _ROLES
    ]
    any_fail = any(r.verdict == ReviewerVerdict.failed for r in reviewers)
    any_unclear = any(r.verdict == ReviewerVerdict.unclear for r in reviewers)
    attempt = "failed" if any_fail else ("unclear" if any_unclear else "passed")
    return Review(
        claim_id="c1",
        attempt_status=attempt,
        verdict="continue",
        reviewers=reviewers,
        strongest_objection="none",
    )


def _grade(
    *,
    status: IdeaStatus = IdeaStatus.promising,
    score: int = 80,
    pls: ProofLanguageStatus = ProofLanguageStatus.agent_consensus_mock_checked,
) -> IdeaGrade:
    return IdeaGrade(
        claim_id="c1",
        status=status,
        score=score,
        confidence="medium",
        reasons=["r"],
        next_cheapest_test="t",
        human_review_required=False,
        proof_language_status=pls,
    )


def _seal(tmp_path: Path):
    (tmp_path / "locked_claim.yaml").write_text("claim")
    (tmp_path / "source_grounding.yaml").write_text("sg")
    (tmp_path / "review.yaml").write_text("rev")
    (tmp_path / "prover_loop.yaml").write_text("pl")
    manifest = SimpleNamespace(
        artifacts={
            "locked_claim": "locked_claim.yaml",
            "source_grounding": "source_grounding.yaml",
            "review": "review.yaml",
        },
        optional_artifacts={"prover_loop": "prover_loop.yaml"},
    )
    hashes = {
        "claim": artifact_revision_hash(tmp_path, "locked_claim.yaml"),
        "sg": artifact_revision_hash(tmp_path, "source_grounding.yaml"),
        "rev": artifact_revision_hash(tmp_path, "review.yaml"),
        "pl": artifact_revision_hash(tmp_path, "prover_loop.yaml"),
    }
    return manifest, hashes


# --- model validator: unanimous-consensus + three-distinct-reviewers -----------


def test_model_rejects_consensus_true_with_a_fatal_flaw() -> None:
    with pytest.raises(ValidationError):
        _consensus(consensus=True, verdicts={"line_proof_reviewer": AgentConsensusVerdict.fatal_flaw_found})


def test_model_rejects_consensus_true_with_an_unclear() -> None:
    with pytest.raises(ValidationError):
        _consensus(consensus=True, verdicts={"evidence_reviewer": AgentConsensusVerdict.unclear})


def test_model_requires_three_distinct_reviewers() -> None:
    with pytest.raises(ValidationError):  # only two
        AgentConsensusReview(
            claim_id="c1", run_id="r1", generated_at="t",
            bound_claim_hash="", bound_source_grounding_hash="", bound_review_hash="",
            entries=_entries()[:2], consensus=False, disclaimer="d",
        )
    with pytest.raises(ValidationError):  # duplicate role, not three distinct
        dup = _entries()
        dup[1] = AgentConsensusEntry(reviewer_id="strategy_reviewer", verdict=AgentConsensusVerdict.no_fatal_flaw_found, looked_for="x", finding="y")
        AgentConsensusReview(
            claim_id="c1", run_id="r1", generated_at="t",
            bound_claim_hash="", bound_source_grounding_hash="", bound_review_hash="",
            entries=dup, consensus=True, disclaimer="d",
        )


# --- _validate_grade_proof_language_backed: status needs a unanimous artifact ---


def test_status_requires_a_consensus_artifact() -> None:
    with pytest.raises(KernelValidationError):
        _validate_grade_proof_language_backed(_grade(), SourceGroundingReport(claim_id="c1", steps=[]), None, None, None)


def test_status_requires_unanimous_consensus() -> None:
    nonunanimous = _consensus(consensus=False, verdicts={"line_proof_reviewer": AgentConsensusVerdict.fatal_flaw_found})
    with pytest.raises(KernelValidationError):
        _validate_grade_proof_language_backed(_grade(), SourceGroundingReport(claim_id="c1", steps=[]), None, None, nonunanimous)


def test_status_accepts_a_valid_unanimous_consensus() -> None:
    _validate_grade_proof_language_backed(_grade(), SourceGroundingReport(claim_id="c1", steps=[]), None, None, _consensus())


# --- _validate_agent_consensus_review: provenance gate ------------------------


def test_consensus_happy_path(tmp_path: Path) -> None:
    manifest, h = _seal(tmp_path)
    _validate_agent_consensus_review(_consensus(hashes=h), _review(), _grade(), manifest, tmp_path)


def test_consensus_revoked_on_artifact_change(tmp_path: Path) -> None:
    manifest, h = _seal(tmp_path)
    consensus = _consensus(hashes=h)
    (tmp_path / "locked_claim.yaml").write_text("claim MUTATED")  # proof changed after sealing
    with pytest.raises(KernelValidationError):
        _validate_agent_consensus_review(consensus, _review(), _grade(), manifest, tmp_path)


def test_consensus_blocked_by_a_failed_reviewer(tmp_path: Path) -> None:
    manifest, h = _seal(tmp_path)
    failed_review = _review({"strategy_reviewer": ReviewerVerdict.failed})
    with pytest.raises(KernelValidationError):
        _validate_agent_consensus_review(_consensus(hashes=h), failed_review, _grade(), manifest, tmp_path)


def test_consensus_incompatible_with_refuted_claim(tmp_path: Path) -> None:
    manifest, h = _seal(tmp_path)
    with pytest.raises(KernelValidationError):
        _validate_agent_consensus_review(_consensus(hashes=h), _review(), _grade(status=IdeaStatus.refuted), manifest, tmp_path)


def test_status_set_but_no_artifact_is_rejected(tmp_path: Path) -> None:
    manifest, _ = _seal(tmp_path)
    with pytest.raises(KernelValidationError):
        _validate_agent_consensus_review(None, _review(), _grade(), manifest, tmp_path)


def test_advisory_record_with_weak_status_still_hash_checked(tmp_path: Path) -> None:
    # the status is weak (proof_candidate) but the consensus record exists -> it must
    # still bind to the current artifacts (no stale advisory record allowed).
    manifest, _ = _seal(tmp_path)
    stale = _consensus(hashes={"claim": "WRONG", "sg": "WRONG", "rev": "WRONG", "pl": "WRONG"})
    with pytest.raises(KernelValidationError):
        _validate_agent_consensus_review(stale, _review(), _grade(pls=ProofLanguageStatus.proof_candidate), manifest, tmp_path)


def test_no_artifact_no_status_is_fine(tmp_path: Path) -> None:
    manifest, _ = _seal(tmp_path)
    _validate_agent_consensus_review(None, _review(), _grade(pls=ProofLanguageStatus.proof_candidate), manifest, tmp_path)


# --- other guardrails ---------------------------------------------------------


def test_mock_check_cannot_imply_grade_10() -> None:
    with pytest.raises(KernelValidationError):
        _validate_grade_10_requires_global_proof_status(_grade(score=100))


def test_mock_check_phrase_is_a_smuggling_field() -> None:
    assert _prover_status_smuggling_field("the agent_consensus_mock_checked: true now") is not None
    assert _prover_status_smuggling_field("mock_checked = true") is not None


# --- end-to-end wiring through validate_run (loader + gate actually invoked) ----


def _register_consensus(
    run_dir: Path, *, verdicts: dict, consensus: bool, bad_hash: bool = False, findings: dict | None = None
) -> None:
    from espresso_proof.kernel.io import read_yaml, write_yaml

    findings = findings or {}
    man = read_yaml(run_dir / "run_manifest.yaml")
    a = man["artifacts"]
    pl_rel = man["optional_artifacts"].get("prover_loop")
    rec = {
        "schema_version": 1,
        "claim_id": read_yaml(run_dir / a["locked_claim"])["id"],
        "run_id": man["run_id"],
        "generated_at": "t",
        "bound_claim_hash": "STALE" if bad_hash else artifact_revision_hash(run_dir, a["locked_claim"]),
        "bound_source_grounding_hash": artifact_revision_hash(run_dir, a["source_grounding"]),
        "bound_review_hash": artifact_revision_hash(run_dir, a["review"]),
        "bound_prover_loop_hash": artifact_revision_hash(run_dir, pl_rel) if pl_rel else "",
        "entries": [
            {"reviewer_id": rid, "verdict": verdicts[rid], "looked_for": "x", "finding": findings.get(rid, "y"), "residual_objections": []}
            for rid in ("strategy_reviewer", "evidence_reviewer", "line_proof_reviewer")
        ],
        "consensus": consensus,
        "disclaimer": "agent consensus, NOT machine-verified",
    }
    write_yaml(run_dir / "agent_consensus_review.yaml", rec)
    man["optional_artifacts"]["agent_consensus_review"] = "agent_consensus_review.yaml"
    write_yaml(run_dir / "run_manifest.yaml", man)


def test_validate_run_accepts_a_valid_advisory_consensus_record(tmp_path: Path) -> None:
    # the baseline fixture has a failed strategy_reviewer; a consistent advisory
    # consensus record (failed reviewer marked fatal, non-unanimous, hash-bound,
    # status left weak) is accepted end-to-end -> proves the loader + gate are wired.
    from espresso_proof.kernel.validators import validate_run
    from tests.test_proof_verification_bridge_v2 import _build

    run_dir = _build(tmp_path)
    _register_consensus(
        run_dir,
        verdicts={
            "strategy_reviewer": "fatal_flaw_found",
            "evidence_reviewer": "unclear",
            "line_proof_reviewer": "unclear",
        },
        consensus=False,
    )
    validate_run(run_dir)  # must not raise


def test_validate_run_rejects_a_stale_consensus_record(tmp_path: Path) -> None:
    from espresso_proof.kernel.validators import KernelValidationError, validate_run
    from tests.test_proof_verification_bridge_v2 import _build

    run_dir = _build(tmp_path)
    _register_consensus(
        run_dir,
        verdicts={
            "strategy_reviewer": "fatal_flaw_found",
            "evidence_reviewer": "unclear",
            "line_proof_reviewer": "unclear",
        },
        consensus=False,
        bad_hash=True,
    )
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_validate_run_rejects_status_smuggle_in_consensus_text(tmp_path: Path) -> None:
    # B2: free text inside the consensus record cannot smuggle a status field, even
    # though StrictBaseModel only blocks unknown field NAMES.
    from espresso_proof.kernel.validators import KernelValidationError, validate_run
    from tests.test_proof_verification_bridge_v2 import _build

    run_dir = _build(tmp_path)
    _register_consensus(
        run_dir,
        verdicts={
            "strategy_reviewer": "fatal_flaw_found",
            "evidence_reviewer": "unclear",
            "line_proof_reviewer": "unclear",
        },
        consensus=False,
        findings={"evidence_reviewer": "the step is source_backed: true so it holds"},
    )
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


# --- B1: a mock-checked final report may never print forbidden proof language ----


def _call_final_report_language(text: str, pls: ProofLanguageStatus) -> None:
    from espresso_proof.kernel.models import (
        CounterexampleReport,
        ExampleSelection,
        TestReport,
    )
    from espresso_proof.kernel.validators import _validate_final_report_language

    import tempfile

    d = Path(tempfile.mkdtemp())
    fr = d / "final_report.md"
    fr.write_text(text)
    _validate_final_report_language(
        fr,
        SimpleNamespace(artifacts={}, optional_artifacts={}),
        _grade(pls=pls),
        ExampleSelection(claim_id="c1", examples=[]),
        CounterexampleReport(claim_id="c1", tests=[]),
        TestReport(claim_id="c1", target="claim", tests=[], summary="x"),
        SourceGroundingReport(claim_id="c1", steps=[]),
        _review(),
        None,
        None,
    )


def test_mock_check_final_report_rejects_forbidden_proof_language() -> None:
    # agent_consensus_mock_checked is amber, so its final report is held to the same
    # no-"proof-close"/"proved"/"verified" bar as the other weak statuses.
    with pytest.raises(KernelValidationError, match="forbidden proof language"):
        _call_final_report_language(
            "The theorem is proved. proof-close.",
            ProofLanguageStatus.agent_consensus_mock_checked,
        )
