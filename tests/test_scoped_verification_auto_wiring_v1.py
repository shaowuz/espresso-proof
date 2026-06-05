"""Scoped Verification Auto-Wiring V1.

The strategy/prover stage can emit EXPLICIT, structured scoped-verification
requests (scoped_verification_requests.yaml); auto-wiring runs the concrete, safe
ones through the existing trusted backend and appends scoped proof-verification
entries. A confirming result earns instance_checked; everything else fails closed
to unverified or is skipped. Natural-language prose alone can NEVER earn
instance_checked, and nothing here upgrades proof/theorem status to proved.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.finite_group_verifier import cyclic_group_table
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.models import ScopedVerificationRequest
from espresso_proof.proof_verification import (
    AUTO_MAX_MODULUS,
    auto_wire_scoped_verifications,
    verify_identity_table_in_run,
)
from espresso_proof.validators import KernelValidationError, validate_run

FIXTURES = Path("tests/fixtures")
LEFT_PROJ = [[0, 0], [1, 1]]  # non-commutative finite table


def _build(tmp_path: Path) -> Path:
    return run_mock_critique_pv(tmp_path)


def run_mock_critique_pv(tmp_path: Path) -> Path:
    from espresso_proof.mock_pipeline import run_mock_critique

    return run_mock_critique("aw", FIXTURES / "vague_claim_unclear" / "input.md", tmp_path)


def _claim_id(run_dir: Path) -> str:
    a = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return read_yaml(run_dir / a["locked_claim"])["id"]


def _grade(run_dir: Path) -> dict:
    a = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return read_yaml(run_dir / a["idea_grade"])


def _write_requests(run_dir: Path, requests: list[dict], *, claim_id: str | None = None) -> None:
    doc = {"claim_id": claim_id if claim_id is not None else _claim_id(run_dir), "requests": requests}
    write_yaml(run_dir / "scoped_verification_requests.yaml", doc)


def _pv_entries(run_dir: Path) -> list[dict]:
    path = run_dir / "proof_verification.yaml"
    return read_yaml(path)["entries"] if path.exists() else []


# --- auto-wiring happy path -------------------------------------------------
def test_auto_wire_runs_concrete_table_and_ring(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    before = _grade(run_dir)["proof_language_status"]
    _write_requests(run_dir, [
        {"id": "t", "kind": "finite_identity_table", "identity": "commutative", "table": cyclic_group_table(4)},
        {"id": "rp", "kind": "finite_ring_identity", "identity": "freshmans_dream", "modulus": 5},
        {"id": "rc", "kind": "finite_ring_identity", "identity": "freshmans_dream", "modulus": 6},
    ])
    summary = auto_wire_scoped_verifications(run_dir)
    validate_run(run_dir)
    earned = {e["id"]: e["earned_status"] for e in _pv_entries(run_dir)}
    assert earned["auto_t"] == "instance_checked"
    assert earned["auto_rp"] == "instance_checked"
    assert earned["auto_rc"] == "unverified"  # composite modulus fails closed
    assert {a["id"] for a in summary["appended"]} == {"auto_t", "auto_rp", "auto_rc"}
    # never upgrades proof/theorem status.
    assert _grade(run_dir)["proof_language_status"] == before


def test_auto_wire_is_idempotent(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    _write_requests(run_dir, [
        {"id": "t", "kind": "finite_identity_table", "identity": "commutative", "table": cyclic_group_table(4)},
    ])
    auto_wire_scoped_verifications(run_dir)
    second = auto_wire_scoped_verifications(run_dir)
    assert second["appended"] == []
    assert any(s["reason"] == "already wired" for s in second["skipped"])
    assert len([e for e in _pv_entries(run_dir) if e["id"] == "auto_t"]) == 1


# --- fail-closed: missing / malformed / ambiguous / too expensive -----------
def test_missing_table_is_skipped(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    _write_requests(run_dir, [{"id": "t", "kind": "finite_identity_table", "identity": "commutative"}])
    summary = auto_wire_scoped_verifications(run_dir)
    validate_run(run_dir)
    assert summary["appended"] == []
    assert any("missing or empty operation table" in s["reason"] for s in summary["skipped"])
    assert _pv_entries(run_dir) == []


def test_too_expensive_request_is_skipped(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    _write_requests(run_dir, [
        {"id": "r", "kind": "finite_ring_identity", "identity": "freshmans_dream", "modulus": AUTO_MAX_MODULUS + 1},
        {"id": "big", "kind": "finite_identity_table", "identity": "commutative", "table": cyclic_group_table(2)},
    ])
    # also exceed the table-order cap explicitly
    summary = auto_wire_scoped_verifications(run_dir, max_table_order=1)
    assert any("too expensive" in s["reason"] for s in summary["skipped"])
    assert any(s["id"] == "big" for s in summary["skipped"])


def test_non_holding_table_fails_closed_to_unverified(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    _write_requests(run_dir, [
        {"id": "np", "kind": "finite_identity_table", "identity": "commutative", "table": LEFT_PROJ},
    ])
    auto_wire_scoped_verifications(run_dir)
    validate_run(run_dir)
    earned = {e["id"]: e["earned_status"] for e in _pv_entries(run_dir)}
    assert earned["auto_np"] == "unverified"


def test_malformed_requests_file_fails_closed(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    # an invalid kind makes the whole structured file malformed -> load raises.
    write_yaml(
        run_dir / "scoped_verification_requests.yaml",
        {"claim_id": _claim_id(run_dir), "requests": [{"id": "x", "kind": "free_text_proof", "identity": "x"}]},
    )
    with pytest.raises(Exception):
        auto_wire_scoped_verifications(run_dir)


def test_claim_mismatch_appends_nothing(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    _write_requests(
        run_dir,
        [{"id": "t", "kind": "finite_identity_table", "identity": "commutative", "table": cyclic_group_table(4)}],
        claim_id="some_other_claim",
    )
    summary = auto_wire_scoped_verifications(run_dir)
    validate_run(run_dir)
    assert summary["appended"] == []
    assert _pv_entries(run_dir) == []


# --- the core negative test: natural language alone cannot earn checked ------
def test_natural_language_only_cannot_trigger_instance_checked(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    # NO requests artifact at all: the strategy_synthesis is prose only. Auto-wiring
    # reads only the structured requests file, so prose can never earn a status.
    summary = auto_wire_scoped_verifications(run_dir)
    validate_run(run_dir)
    assert summary == {"appended": [], "skipped": []}
    assert all(e["earned_status"] != "instance_checked" for e in _pv_entries(run_dir))
    assert _grade(run_dir)["proof_language_status"] != "proved"


def test_request_cannot_carry_a_status_field() -> None:
    # a request has no status/earned field; an attempt to smuggle one is rejected.
    with pytest.raises(Exception):
        ScopedVerificationRequest.model_validate(
            {"id": "x", "kind": "finite_ring_identity", "identity": "freshmans_dream",
             "modulus": 5, "earned_status": "instance_checked"}
        )


# --- old manual CLI behaviour still works (regression) ----------------------
def test_manual_cli_path_still_earns_instance_checked(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    entry = verify_identity_table_in_run(
        run_dir, table=cyclic_group_table(4), identity="commutative", entry_id="manual_1"
    )
    validate_run(run_dir)
    assert entry["earned_status"] == "instance_checked"
    # the auto path then coexists, appending alongside the manual entry.
    _write_requests(run_dir, [
        {"id": "a", "kind": "finite_ring_identity", "identity": "square_of_sum", "modulus": 7},
    ])
    auto_wire_scoped_verifications(run_dir)
    validate_run(run_dir)
    ids = {e["id"] for e in _pv_entries(run_dir)}
    assert {"manual_1", "auto_a"} <= ids


# --- CLI --------------------------------------------------------------------
def test_cli_auto_wire_verifications(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    _write_requests(run_dir, [
        {"id": "t", "kind": "finite_identity_table", "identity": "commutative", "table": cyclic_group_table(4)},
        {"id": "bad", "kind": "finite_identity_table", "identity": "commutative"},
    ])
    out = CliRunner().invoke(app, ["auto-wire-verifications", "--run", str(run_dir)])
    assert out.exit_code == 0, out.output
    assert "instance_checked" in out.output
    assert "NOT a universal proof" in out.output
    lower = out.output.lower()
    for phrase in ("proof-close", "proof complete", "we proved"):
        assert phrase not in lower
