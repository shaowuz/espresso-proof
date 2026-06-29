"""Proof Verification Bridge V2.

Connects the finite operation-table identity checker (verify_identity_on_table)
and an exhaustive Z/n algebraic-identity sanity checker into the proof-
verification artifact flow. Each earns a SCOPED instance_checked ONLY from a
trusted + confirming deterministic result; everything else fails closed to
unverified. There is no 'proved' status: a finite/computational check never
proves a universal claim.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.verification.finite_group_verifier import cyclic_group_table
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.verification.proof_verification import (
    append_verification_entries,
    verify_identity_table_in_run,
    verify_ring_identity_in_run,
)
from espresso_proof.verification.symbolic_identity import (
    SUPPORTED_RING_IDENTITIES,
    verify_ring_identity_mod,
)
from espresso_proof.kernel.validators import KernelValidationError, validate_run

FIXTURES = Path("tests/fixtures")
# a non-commutative finite operation table (left-projection: a*b = a).
LEFT_PROJ = [[0, 0], [1, 1]]


def _build(tmp_path: Path) -> Path:
    run_dir = run_mock_critique("pv", FIXTURES / "vague_claim_unclear" / "input.md", tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)
    return run_dir


def _claim_id(run_dir: Path) -> str:
    a = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return read_yaml(run_dir / a["locked_claim"])["id"]


def _grade(run_dir: Path) -> dict:
    a = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return read_yaml(run_dir / a["idea_grade"])


def _pv(run_dir: Path) -> dict:
    return read_yaml(run_dir / "proof_verification.yaml")


# --- symbolic checker correctness (unit) ------------------------------------
def test_ring_identity_checker_correctness() -> None:
    # square_of_sum holds over every ring, so for any modulus.
    for n in (1, 2, 6, 12):
        assert verify_ring_identity_mod("square_of_sum", n)["holds"] is True
    # freshman's dream holds over Z/n iff n is prime (Frobenius in char p).
    assert verify_ring_identity_mod("freshmans_dream", 5)["holds"] is True
    assert verify_ring_identity_mod("freshmans_dream", 7)["holds"] is True
    assert verify_ring_identity_mod("freshmans_dream", 6)["holds"] is False
    assert verify_ring_identity_mod("freshmans_dream", 9)["holds"] is False
    assert verify_ring_identity_mod("distributive", 5)["holds"] is True


def test_ring_identity_fails_closed() -> None:
    # unsupported identity / bad modulus / over-cap modulus never raise.
    assert verify_ring_identity_mod("nope", 5)["holds"] is False
    assert verify_ring_identity_mod("square_of_sum", 0)["holds"] is False
    assert verify_ring_identity_mod("square_of_sum", -3)["holds"] is False
    assert verify_ring_identity_mod("square_of_sum", True)["holds"] is False
    assert verify_ring_identity_mod("square_of_sum", "5")["holds"] is False
    over = verify_ring_identity_mod("square_of_sum", 100000)
    assert over["holds"] is False and "safety cap" in over["detail"]


# --- table identity wired into a run ----------------------------------------
def test_table_identity_earns_instance_checked(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    grade_before = _grade(run_dir)
    entry = verify_identity_table_in_run(
        run_dir, table=cyclic_group_table(4), identity="commutative", entry_id="t_comm"
    )
    validate_run(run_dir)
    assert entry["earned_status"] == "instance_checked"
    assert entry["verifier_type"] == "finite_identity_table"
    # the bridge never upgrades idea_grade proof status.
    assert _grade(run_dir)["proof_language_status"] == grade_before["proof_language_status"]


def test_table_identity_non_holding_fails_closed(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    entry = verify_identity_table_in_run(
        run_dir, table=LEFT_PROJ, identity="commutative", entry_id="t_bad"
    )
    validate_run(run_dir)  # unverified is the safe default
    assert entry["earned_status"] == "unverified"


def test_table_identity_malformed_fails_closed(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    entry = verify_identity_table_in_run(
        run_dir, table="not a table", identity="commutative", entry_id="t_junk"
    )
    validate_run(run_dir)
    assert entry["earned_status"] == "unverified" and "malformed" in entry["result"]


# --- ring identity wired into a run -----------------------------------------
def test_ring_identity_prime_vs_composite(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    prime = verify_ring_identity_in_run(run_dir, identity="freshmans_dream", modulus=5, entry_id="r_prime")
    composite = verify_ring_identity_in_run(run_dir, identity="freshmans_dream", modulus=6, entry_id="r_comp")
    validate_run(run_dir)
    assert prime["earned_status"] == "instance_checked"
    assert composite["earned_status"] == "unverified"
    # scope note must keep the result finite/computational, never universal.
    assert "not a proof over a general ring" in prime["scope_note"]


# --- the validator gate holds for the new trusted types ---------------------
def _seed(run_dir: Path) -> None:
    verify_identity_table_in_run(
        run_dir, table=cyclic_group_table(4), identity="commutative", entry_id="seed"
    )


def _overwrite_entries(run_dir: Path, entries: list[dict]) -> None:
    path = run_dir / "proof_verification.yaml"
    doc = read_yaml(path)
    doc["entries"] = entries
    write_yaml(path, doc)


def _entry(run_dir: Path, **over) -> dict:
    base = {
        "id": "x", "verifier_type": "finite_identity_table", "claim_id": _claim_id(run_dir),
        "target": "t", "input_summary": "s", "result": "r", "trusted": True,
        "confirmed": True, "earned_status": "instance_checked", "scope_note": "finite table only",
    }
    base.update(over)
    return base


def test_fabricated_identity_checked_without_confirmation_rejected(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    _seed(run_dir)
    _overwrite_entries(run_dir, [_entry(run_dir, confirmed=False)])
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)
    # also: a non-trusted entry cannot earn it.
    _overwrite_entries(run_dir, [_entry(run_dir, trusted=False)])
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_identity_type_cannot_earn_computation_checked(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    _seed(run_dir)
    _overwrite_entries(run_dir, [_entry(run_dir, earned_status="computation_checked")])
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_ring_identity_for_other_claim_rejected(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    _seed(run_dir)
    _overwrite_entries(run_dir, [_entry(run_dir, verifier_type="finite_ring_identity", claim_id="other")])
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


# --- append safety ----------------------------------------------------------
def test_append_rejects_duplicate_entry_id(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    verify_identity_table_in_run(run_dir, table=cyclic_group_table(4), identity="commutative", entry_id="dup")
    with pytest.raises(ValueError):
        verify_ring_identity_in_run(run_dir, identity="square_of_sum", modulus=5, entry_id="dup")


def test_append_binds_run_and_claim(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    verify_identity_table_in_run(run_dir, table=cyclic_group_table(4), identity="commutative", entry_id="b1")
    pv = _pv(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert pv["claim_id"] == _claim_id(run_dir)
    assert pv["run_id"] == manifest["run_id"]
    assert manifest["optional_artifacts"]["proof_verification"] == "proof_verification.yaml"


# --- memo wording -----------------------------------------------------------
def test_memo_renders_scoped_identity_never_proved(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    verify_identity_table_in_run(run_dir, table=cyclic_group_table(4), identity="commutative", entry_id="m_t")
    verify_ring_identity_in_run(run_dir, identity="freshmans_dream", modulus=5, entry_id="m_r")
    memo = build_concise_memo(run_dir)
    lower = memo.lower()
    assert "machine-verified instances (scoped" in lower
    assert "finite/computational" in lower
    assert "do not prove the universal claim" in lower
    for phrase in ("proof-close", "proof complete", "we proved the", "this establishes the theorem"):
        assert phrase not in lower


# --- CLI --------------------------------------------------------------------
def test_cli_verify_ring_identity_in_run(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    out = CliRunner().invoke(
        app,
        ["verify-ring-identity-in-run", "--run", str(run_dir), "--identity", "freshmans_dream",
         "--modulus", "5", "--id", "cli_r"],
    )
    assert out.exit_code == 0, out.output
    assert "instance_checked" in out.output
    assert "NOT a universal proof" in out.output
    assert "freshmans_dream" in " ".join(SUPPORTED_RING_IDENTITIES)
