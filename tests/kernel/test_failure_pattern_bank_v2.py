"""Failure Pattern Bank V2.

Adds two advisory (candidate/warn) assumption-swap patterns: general
finite-vs-infinite, and dropping a compactness/properness/completeness
assumption. Advisory only: a match never proves/refutes a claim and never
upgrades proof/source/evidence status. The generic/very-general->all gate stays
hard (existing validator).
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.failure_pattern_bank import (
    audit_failure_patterns,
    build_failure_pattern_audit,
    seed_failure_pattern_bank,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")
_NEW_PATTERNS = ("finite_not_infinite", "compact_proper_complete_assumption")


def _run_with_claim_text(tmp_path: Path, text: str) -> Path:
    run_dir = run_mock_critique(
        "linear_algebra", FIXTURES / "all_matrices_diagonalizable" / "input.md", tmp_path
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_path = run_dir / manifest["artifacts"]["locked_claim"]
    claim = read_yaml(claim_path)
    claim["normalized_statement"] = text
    write_yaml(claim_path, claim)
    return run_dir


def _match_ids(report: dict) -> set[str]:
    return {m["pattern_id"] for m in report["matches"]}


# ---------------------------------------------------------------------------
# 1) Each new pattern can be matched on a small fixture.
# ---------------------------------------------------------------------------


def test_finite_not_infinite_matches(tmp_path: Path) -> None:
    run_dir = _run_with_claim_text(tmp_path, "Every infinite group with this property is trivial.")
    report = build_failure_pattern_audit(run_dir)
    assert "finite_not_infinite" in _match_ids(report)


def test_compact_proper_complete_matches(tmp_path: Path) -> None:
    run_dir = _run_with_claim_text(
        tmp_path, "For a compact complex manifold admitting a proper morphism, the result holds."
    )
    report = build_failure_pattern_audit(run_dir)
    assert "compact_proper_complete_assumption" in _match_ids(report)


# ---------------------------------------------------------------------------
# 2) Hard / advisory distinction is preserved.
# ---------------------------------------------------------------------------


def test_new_patterns_are_candidate_advisory_not_hard() -> None:
    bank = seed_failure_pattern_bank()
    by_id = {p.id: p for p in bank.patterns}
    for pid in _NEW_PATTERNS:
        assert pid in by_id, pid
        assert by_id[pid].tier.value == "candidate"
        assert by_id[pid].action.value == "warn"
        assert by_id[pid].enforced_by == ""  # candidate adds no blocking


def test_new_pattern_matches_are_warn_only_in_audit(tmp_path: Path) -> None:
    run_dir = _run_with_claim_text(tmp_path, "Every infinite group here is compact and complete.")
    report = build_failure_pattern_audit(run_dir)
    for m in report["matches"]:
        if m["pattern_id"] in _NEW_PATTERNS:
            assert m["tier"] == "candidate"
            assert m["action"] == "warn"
            assert m["enforced_by"] == ""


# ---------------------------------------------------------------------------
# 3) Pattern match does not upgrade proof/evidence/source status.
# ---------------------------------------------------------------------------


def test_new_pattern_match_does_not_upgrade_status(tmp_path: Path) -> None:
    run_dir = _run_with_claim_text(tmp_path, "Every infinite group with a compact quotient is trivial.")
    grade_before = read_yaml(run_dir / "idea_grade.yaml")
    sg_before = read_yaml(run_dir / "source_grounding.yaml")
    report = build_failure_pattern_audit(run_dir)
    assert _NEW_PATTERNS[0] in _match_ids(report) or _NEW_PATTERNS[1] in _match_ids(report)
    # the audit report carries no status/refutation vocabulary.
    blob = str(report)
    for forbidden in ("refutes_claim", "source_backed", "proof_language_status"):
        assert forbidden not in blob
    assert read_yaml(run_dir / "idea_grade.yaml") == grade_before
    assert read_yaml(run_dir / "source_grounding.yaml") == sg_before


# ---------------------------------------------------------------------------
# 4) `generic` / `very general` cannot support `all` (stays hard).
# ---------------------------------------------------------------------------


def test_generic_very_general_to_all_remains_hard() -> None:
    bank = seed_failure_pattern_bank()
    vg = next(p for p in bank.patterns if p.id == "very_general_to_all")
    assert vg.tier.value == "hard"
    assert vg.action.value == "block_via_validator"
    assert vg.enforced_by == "_validate_theorem_applicability"
    # the new finite/compact patterns did NOT become hard or gain a validator.
    for pid in _NEW_PATTERNS:
        p = next(x for x in bank.patterns if x.id == pid)
        assert p.tier.value != "hard"


# ---------------------------------------------------------------------------
# 5) Missing compactness/properness assumption stays candidate/advisory.
# ---------------------------------------------------------------------------


def test_compactness_pattern_is_advisory_and_audit_stays_valid(tmp_path: Path) -> None:
    run_dir = _run_with_claim_text(
        tmp_path, "Assume a proper morphism with completeness; the conclusion follows."
    )
    report = build_failure_pattern_audit(run_dir)
    # matched only as a warning (candidate); never blocks or refutes.
    matched = [m for m in report["matches"] if m["pattern_id"] == "compact_proper_complete_assumption"]
    assert matched and all(m["action"] == "warn" for m in matched)
    # writing the audit + full validate_run still passes (advisory artifact only).
    audit_failure_patterns(run_dir)
    validate_run(run_dir)
