"""Proof Verification Bridge V0 (Six-Hour goal, Phase 4).

Only a trusted deterministic backend with a confirming result earns a checked
status, and it is SCOPED to what was actually checked. There is no ``proved``
status: a finite/instance check never proves a universal claim. Natural-language
prover/reviewer approval is not proof verification. These tests pin that.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.verification.finite_group_verifier import (
    cyclic_group_table,
    elementary_abelian_table,
    verify_group_abelian,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.kernel.models import ProofVerificationStatus
from espresso_proof.verification.proof_verification import verify_finite_groups
from espresso_proof.orchestration.run_dashboard import render_topbar_status_html, status_summary
from espresso_proof.kernel.validators import KernelValidationError, validate_run

FIXTURES = Path("tests/fixtures")


def _build(tmp_path: Path) -> Path:
    run_dir = run_mock_critique("pv", FIXTURES / "vague_claim_unclear" / "input.md", tmp_path)
    _mark_claim_clear(run_dir)
    return run_dir


def _mark_claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)


def _claim_id(run_dir: Path) -> str:
    a = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return read_yaml(run_dir / a["locked_claim"])["id"]


def _grade(run_dir: Path) -> dict:
    a = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return read_yaml(run_dir / a["idea_grade"])


def _entry(run_dir: Path, **over) -> dict:
    base = {
        "id": "pv_x",
        "verifier_type": "finite_group_cayley",
        "claim_id": _claim_id(run_dir),
        "target": "group C_4 of order 4",
        "input_summary": "table",
        "result": "verified abelian group",
        "trusted": True,
        "confirmed": True,
        "earned_status": "instance_checked",
        "scope_note": "order 4 only; not all primes p",
    }
    base.update(over)
    return base


def _write_pv(run_dir: Path, entries: list[dict]) -> None:
    verify_finite_groups(run_dir, primes=[2])  # creates + registers the artifact
    path = run_dir / "proof_verification.yaml"
    doc = read_yaml(path)
    doc["entries"] = entries
    write_yaml(path, doc)


# --- structural -------------------------------------------------------------
def test_no_proved_status_exists() -> None:
    values = {s.value for s in ProofVerificationStatus}
    assert "proved" not in values


def test_finite_group_verifier_is_correct() -> None:
    assert verify_group_abelian(cyclic_group_table(4))["is_abelian"] is True
    assert verify_group_abelian(elementary_abelian_table(3, 2))["is_abelian"] is True
    # a real non-abelian group of order 6 (S3): a verified group, not abelian
    s3 = [[0,1,2,3,4,5],[1,0,4,5,2,3],[2,5,0,4,3,1],[3,4,5,0,1,2],[4,3,1,2,5,0],[5,2,3,1,0,4]]
    r = verify_group_abelian(s3)
    assert r["is_group"] is True and r["is_abelian"] is False
    # a malformed table is not a group (fail closed)
    assert verify_group_abelian([[0, 1], [1, 1]])["is_group"] is False


# --- positive control -------------------------------------------------------
def test_finite_group_bridge_earns_instance_checked_not_proved(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    grade_before = _grade(run_dir)
    report = verify_finite_groups(run_dir, primes=[2, 3])
    validate_run(run_dir)
    assert all(e["earned_status"] == "instance_checked" for e in report["entries"])
    assert len(report["entries"]) == 4  # C4, C2xC2, C9, C3xC3
    # the bridge does NOT upgrade idea_grade proof status
    assert _grade(run_dir)["proof_language_status"] == grade_before["proof_language_status"]
    # the memo shows scoped machine checks but never asserts a proof
    memo = build_concise_memo(run_dir).lower()
    assert "machine-verified instances (scoped" in memo
    for phrase in ("proof-close", "proof complete", "we proved the", "this establishes the theorem"):
        assert phrase not in memo


# --- the 5 goal-required negative tests --------------------------------------
def test_prose_untrusted_cannot_earn_checked(tmp_path: Path) -> None:
    """Prover prose (a non-trusted 'verifier') cannot set a proof-checked status."""
    run_dir = _build(tmp_path)
    _write_pv(run_dir, [_entry(run_dir, trusted=False)])
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_unconfirmed_result_cannot_earn_checked(tmp_path: Path) -> None:
    """A reviewer PASS / non-confirming result cannot earn instance_checked."""
    run_dir = _build(tmp_path)
    _write_pv(run_dir, [_entry(run_dir, confirmed=False)])
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_wrong_verifier_type_cannot_earn_proof_status(tmp_path: Path) -> None:
    """lean_bridge cannot earn lean_checked in V0; type/status must match."""
    run_dir = _build(tmp_path)
    _write_pv(run_dir, [_entry(run_dir, verifier_type="lean_bridge", earned_status="lean_checked")])
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)
    # also: finite_group_cayley cannot claim computation_checked
    _write_pv(run_dir, [_entry(run_dir, earned_status="computation_checked")])
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_verifier_result_for_other_claim_rejected(tmp_path: Path) -> None:
    """A verifier result tagged with a different claim cannot support this run."""
    run_dir = _build(tmp_path)
    _write_pv(run_dir, [_entry(run_dir, claim_id="some_other_claim")])
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_failed_verifier_fails_closed_to_unverified(tmp_path: Path) -> None:
    """A failed/malformed verifier result fails closed (unverified), never proof."""
    run_dir = _build(tmp_path)
    # an unverified entry is acceptable and changes no proof status
    _write_pv(run_dir, [_entry(run_dir, confirmed=False, earned_status="unverified",
                               result="non-square table")])
    validate_run(run_dir)  # unverified is the safe default
    grade = _grade(run_dir)
    assert grade["proof_language_status"] != "proved"
    memo = build_concise_memo(run_dir).lower()
    for phrase in ("proof-close", "proof complete", "we proved the"):
        assert phrase not in memo


def test_lean_bridge_must_stay_unverified(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    _write_pv(run_dir, [_entry(run_dir, verifier_type="lean_bridge", trusted=False,
                               confirmed=False, earned_status="unverified")])
    validate_run(run_dir)  # registry-only lean entry is allowed when unverified
    _write_pv(run_dir, [_entry(run_dir, verifier_type="lean_bridge", earned_status="instance_checked")])
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


# ---------------------------------------------------------------------------
# Goal 23: a scoped computation cannot upgrade the grade to a global checked proof
# ---------------------------------------------------------------------------
def _set_grade_proof_status(run_dir: Path, value: str) -> None:
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    grade = read_yaml(run_dir / artifacts["idea_grade"])
    grade["proof_language_status"] = value
    write_yaml(run_dir / artifacts["idea_grade"], grade)


def test_grade_computation_checked_requires_confirming_machine_result(tmp_path: Path) -> None:
    # Forging proof_language_status=computation_checked with NO confirming machine
    # result is rejected (it would otherwise render "A proof has been checked by a
    # trusted verifier").
    run_dir = _build(tmp_path)
    _set_grade_proof_status(run_dir, "computation_checked")
    with pytest.raises(KernelValidationError, match="confirming trusted machine result"):
        validate_run(run_dir)


def test_grade_computation_checked_valid_with_confirming_entry(tmp_path: Path) -> None:
    # A scoped finite check that genuinely earned a trusted+confirmed entry DOES
    # back computation_checked (e.g. the claim is exactly the enumerated scope).
    # Use a fixture without failed reviewers so only the Goal-23 gate is exercised.
    run_dir = run_mock_critique("pv", FIXTURES / "promising_but_unproved" / "input.md", tmp_path)
    _mark_claim_clear(run_dir)
    verify_finite_groups(run_dir, primes=[2])  # trusted+confirmed instance_checked entries
    _set_grade_proof_status(run_dir, "computation_checked")
    validate_run(run_dir)


def test_grade_lean_checked_not_earnable_in_v0(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    verify_finite_groups(run_dir, primes=[2])  # even with a real scoped check...
    _set_grade_proof_status(run_dir, "lean_checked")  # ...lean_checked is still not earnable
    with pytest.raises(KernelValidationError, match="not earnable in V0"):
        validate_run(run_dir)


def test_grade_human_confirmed_not_earnable_in_v0(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    _set_grade_proof_status(run_dir, "human_confirmed")
    with pytest.raises(KernelValidationError, match="not earnable in V0"):
        validate_run(run_dir)


def test_computation_checked_grade_renders_scoped_not_universal_proof(tmp_path: Path) -> None:
    # A universal claim whose grade is computation_checked (backed by finite
    # p=2,3 checks) must NOT render the global "a proof has been checked by a
    # trusted verifier" sentence -- a finite computation is scoped, not a proof
    # of the universal claim.
    run_dir = run_mock_critique("pv", FIXTURES / "promising_but_unproved" / "input.md", tmp_path)
    _mark_claim_clear(run_dir)
    verify_finite_groups(run_dir, primes=[2, 3])
    _set_grade_proof_status(run_dir, "computation_checked")
    validate_run(run_dir)
    memo = build_concise_memo(run_dir)
    assert "A proof has been checked by a trusted verifier" not in memo
    verdict = next(line for line in memo.splitlines() if line.startswith("Verdict:"))
    assert "finite/computational scope" in verdict
    assert "not a proof of the full claim" in verdict
    topbar = render_topbar_status_html(run_dir)
    summary = status_summary(run_dir)
    assert "Proof status: Scoped check" in topbar
    assert "Proof status: Checked" not in topbar
    assert summary["proof_status"] == "Scoped check"
