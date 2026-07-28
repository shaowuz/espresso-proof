"""Math Failure Pattern Bank V1 (Six-Hour goal, Phase 2).

Three tiers, none of which earns/clears status:
- candidate -> warn only
- audited   -> ranking / time allocation only
- hard      -> points at an EXISTING kernel validator (no new blocking)

A pattern match never proves falsehood, never refutes the claim, and never
upgrades proof/source/evidence status. These tests pin that boundary, including
that a pattern can never be promoted to ``hard`` at run time.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.kernel.failure_pattern_bank import (
    ENFORCING_VALIDATORS,
    audit_failure_patterns,
    build_failure_pattern_audit,
    seed_failure_pattern_bank,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import FailurePatternAudit
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.validators import KernelValidationError, validate_run

FIXTURES = Path("tests/fixtures")


def _build(tmp_path: Path, fixture: str) -> Path:
    return run_mock_critique("fp", FIXTURES / fixture / "input.md", tmp_path)


def _artifact(run_dir: Path, key: str) -> dict:
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return read_yaml(run_dir / artifacts[key])


def test_seed_bank_has_at_least_15_patterns_and_valid_hard_links() -> None:
    bank = seed_failure_pattern_bank()
    assert len(bank.patterns) >= 15
    # very-general-to-all is present and hard, reusing the existing gate
    vg = next(p for p in bank.patterns if p.id == "very_general_to_all")
    assert vg.tier.value == "hard"
    assert vg.enforced_by == "_validate_theorem_applicability"
    # every hard pattern points at an existing validator; non-hard add no blocking
    for p in bank.patterns:
        if p.tier.value == "hard":
            assert p.enforced_by in ENFORCING_VALIDATORS
            assert p.action.value == "block_via_validator"
        else:
            assert p.enforced_by == ""
            assert p.action.value in {"warn", "rank_downgrade"}
    # at least one audited (ranking/time only) pattern exists
    assert any(p.tier.value == "audited" for p in bank.patterns)


def test_audit_model_rejects_status_fields() -> None:
    base = {
        "schema_version": 1,
        "claim_id": "claim_001",
        "run_id": "run_001",
        "generated_at": "2026-06-04T00:00:00+00:00",
        "matches": [],
        "notes": [],
    }
    FailurePatternAudit.model_validate(base)
    for bad in ("source_backed", "computation_checked", "proof_language_status", "refutes_claim"):
        with pytest.raises(ValidationError):
            FailurePatternAudit.model_validate({**base, bad: True})


def test_candidate_and_audited_matches_do_not_upgrade_status(tmp_path: Path) -> None:
    """A pattern audit changes no status: grade, source, computation untouched."""
    run_dir = _build(tmp_path, "source_misapplication_very_general_to_all")
    grade_before = _artifact(run_dir, "idea_grade")
    sg_before = _artifact(run_dir, "source_grounding")
    comp_before = _artifact(run_dir, "computation_checks")
    report = build_failure_pattern_audit(run_dir)
    tiers = {m["tier"] for m in report["matches"]}
    assert "candidate" in tiers or "audited" in tiers  # this fixture triggers both
    audit_failure_patterns(run_dir)
    validate_run(run_dir)
    # nothing the audit can touch changed
    assert _artifact(run_dir, "idea_grade")["status"] == grade_before["status"]
    assert _artifact(run_dir, "idea_grade")["proof_language_status"] == grade_before["proof_language_status"]
    assert [s.get("status") for s in _artifact(run_dir, "source_grounding").get("steps", [])] == [
        s.get("status") for s in sg_before.get("steps", [])
    ]
    assert [c.get("status") for c in _artifact(run_dir, "computation_checks").get("checks", [])] == [
        c.get("status") for c in comp_before.get("checks", [])
    ]


def test_hard_match_only_points_at_existing_validator(tmp_path: Path) -> None:
    """A hard match documents an existing validator; it adds no new blocking and
    the audit carries no blocking/refutation field of its own."""
    run_dir = _build(tmp_path, "dropped_hypothesis_not_refutation")
    report = build_failure_pattern_audit(run_dir)
    hard = [m for m in report["matches"] if m["tier"] == "hard"]
    assert hard, "expected at least one hard match for this fixture"
    for m in hard:
        assert m["enforced_by"] in ENFORCING_VALIDATORS
    # dropped_hypothesis_not_refutation is matched, yet the counterexample is NOT a
    # claim refutation (the existing validator already enforces this).
    assert any(m["pattern_id"] == "dropped_hypothesis_not_refutation" for m in hard)
    cx = _artifact(run_dir, "counterexamples")
    assert all(not t.get("refutes_claim") for t in cx.get("tests", []))


def test_pattern_match_is_not_a_counterexample(tmp_path: Path) -> None:
    """The audit has no refutation vocabulary: a match never refutes the claim."""
    run_dir = _build(tmp_path, "promising_but_unproved")
    report = build_failure_pattern_audit(run_dir)
    # the match shape has no 'refutes' / status field at all
    for m in report["matches"]:
        assert set(m.keys()) == {
            "pattern_id",
            "family",
            "tier",
            "action",
            "subject",
            "signal",
            "explanation",
            "enforced_by",
        }
    # building/auditing does not flip the grade to a refutation status
    audit_failure_patterns(run_dir)
    validate_run(run_dir)
    assert _artifact(run_dir, "idea_grade")["status"] != "refuted"


def test_run_cannot_promote_candidate_pattern_to_hard(tmp_path: Path) -> None:
    """Relabeling a pattern's tier/action/enforced_by is rejected by the validator
    (so memory / a human edit cannot promote candidate -> hard)."""
    run_dir = _build(tmp_path, "source_misapplication_very_general_to_all")
    audit_failure_patterns(run_dir)
    validate_run(run_dir)
    audit_path = run_dir / "failure_pattern_audit.yaml"
    doc = read_yaml(audit_path)
    candidate = next(m for m in doc["matches"] if m["tier"] == "candidate")
    candidate["tier"] = "hard"
    candidate["action"] = "block_via_validator"
    candidate["enforced_by"] = "_validate_theorem_applicability"
    write_yaml(audit_path, doc)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_unknown_pattern_id_and_id_mismatch_rejected(tmp_path: Path) -> None:
    run_dir = _build(tmp_path, "vague_claim_unclear")
    audit_failure_patterns(run_dir)
    validate_run(run_dir)
    audit_path = run_dir / "failure_pattern_audit.yaml"

    # unknown pattern id
    doc = read_yaml(audit_path)
    doc["matches"].append(
        {
            "pattern_id": "not_a_real_pattern",
            "family": "logic",
            "tier": "candidate",
            "action": "warn",
            "subject": "x",
            "signal": "x",
            "explanation": "x",
            "enforced_by": "",
        }
    )
    write_yaml(audit_path, doc)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)

    # claim_id mismatch
    doc = read_yaml(audit_path)
    doc["matches"] = []
    doc["claim_id"] = "other"
    write_yaml(audit_path, doc)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


# --- Additional named patterns + audited ranking/time-allocation hint ---

def test_new_named_seed_patterns_present_as_candidate() -> None:
    bank = seed_failure_pattern_bank()
    by_id = {p.id: p for p in bank.patterns}
    for pid in (
        "generic_to_special",
        "projective_to_proper",
        "rational_to_integral_coefficients",
        "dense_open_not_whole_space",
    ):
        assert pid in by_id, pid
        assert by_id[pid].tier.value == "candidate"
        assert by_id[pid].action.value == "warn"
        assert by_id[pid].enforced_by == ""


def test_new_candidate_patterns_match_claim_text(tmp_path: Path) -> None:
    run_dir = _build(tmp_path, "source_misapplication_very_general_to_all")
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    claim_path = run_dir / artifacts["locked_claim"]
    claim = read_yaml(claim_path)
    claim["normalized_statement"] = (
        "A generic projective variety with rational coefficients holds on a dense "
        "open subset of the moduli space."
    )
    write_yaml(claim_path, claim)
    matched = {m["pattern_id"] for m in build_failure_pattern_audit(run_dir)["matches"]}
    for pid in (
        "generic_to_special",
        "projective_to_proper",
        "rational_to_integral_coefficients",
        "dense_open_not_whole_space",
    ):
        assert pid in matched, pid


def test_allocation_hints_from_audited_match(tmp_path: Path) -> None:
    from espresso_proof.kernel.failure_pattern_bank import failure_pattern_allocation_hints

    run_dir = _build(tmp_path, "source_misapplication_very_general_to_all")
    audit = build_failure_pattern_audit(run_dir)
    assert any(m["action"] == "rank_downgrade" for m in audit["matches"])  # fixture triggers audited
    hints = failure_pattern_allocation_hints(audit)
    # An audited pattern produces a concrete ranking + time-allocation recommendation.
    assert hints["rank_downgrade"]
    assert hints["time_allocation"]
    # ...but the hint carries NO score/grade/proof/source/evidence status field.
    forbidden = {"score", "status", "source_backed", "proof_status", "idea_grade", "computation_checked"}
    assert not (set(hints) & forbidden)
    for h in hints["rank_downgrade"]:
        assert not (set(h) & forbidden)


def test_allocation_hints_empty_without_audited() -> None:
    from espresso_proof.kernel.failure_pattern_bank import failure_pattern_allocation_hints

    audit = {"matches": [{"pattern_id": "smooth_not_singular", "action": "warn", "tier": "candidate"}]}
    hints = failure_pattern_allocation_hints(audit)
    assert hints["rank_downgrade"] == []
    assert hints["time_allocation"] == []


def test_allocation_hints_is_read_only() -> None:
    from copy import deepcopy
    from espresso_proof.kernel.failure_pattern_bank import failure_pattern_allocation_hints

    audit = {"matches": [{"pattern_id": "finite_check_not_universal", "action": "rank_downgrade",
                          "tier": "audited", "subject": "computation_checks", "explanation": "x"}]}
    snapshot = deepcopy(audit)
    failure_pattern_allocation_hints(audit)
    assert audit == snapshot  # pure: does not mutate the audit
