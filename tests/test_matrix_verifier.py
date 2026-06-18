"""Goal 06: matrix rank/determinant scoped verifier.

A trusted deterministic backend earns instance_checked for ONE explicit integer
matrix (a finite/computational check, e.g. a concrete rank/dimension jump), never
a universal proof. Negatives: a mismatch/malformed/unsupported request fails
closed to unverified; a finite check does not upgrade idea grade / prove a
universal claim; a hand-edited unconfirmed checked status is rejected; a request
for the wrong claim is skipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.verification.matrix_verifier import verify_matrix_rank
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.verification.proof_verification import (
    auto_wire_scoped_verifications,
    verify_matrix_rank_in_run,
)
from espresso_proof.kernel.validators import KernelValidationError, validate_run

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path) -> Path:
    run_dir = run_mock_critique("mx", FIXTURES / "vague_claim_unclear" / "input.md", tmp_path)
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


def _entries(run_dir: Path) -> list[dict]:
    return read_yaml(run_dir / "proof_verification.yaml")["entries"]


# --- backend --------------------------------------------------------------

def test_backend_rank_and_determinant_verdicts() -> None:
    assert verify_matrix_rank([[1, 2], [2, 4]], expected_rank=1)["verdict"] == "confirmed"
    assert verify_matrix_rank([[1, 2], [2, 4]], expected_rank=2)["verdict"] == "refuted"
    assert verify_matrix_rank([[1, 2], [3, 4]], expected_determinant=-2)["verdict"] == "confirmed"
    assert verify_matrix_rank([[1, 2], [3, 4]], expected_determinant=5)["verdict"] == "refuted"
    assert verify_matrix_rank([[1, 0], [0, 1]], expected_rank=2, expected_determinant=1)["verdict"] == "confirmed"


def test_backend_error_and_unsupported_fail_closed() -> None:
    assert verify_matrix_rank([[1, 2, 3], [4, 5, 6]], expected_determinant=0)["verdict"] == "error"  # non-square
    assert verify_matrix_rank([[1, 2], [3]], expected_rank=1)["verdict"] == "error"  # ragged
    assert verify_matrix_rank("notamatrix", expected_rank=1)["verdict"] == "error"
    assert verify_matrix_rank([[1, 2], [3, 4]])["verdict"] == "unsupported"  # nothing requested


def test_backend_caps_fail_closed() -> None:
    big_dim = [[1] * 17 for _ in range(17)]
    assert verify_matrix_rank(big_dim, expected_rank=1)["verdict"] == "error"
    over_abs = [[10**7, 0], [0, 1]]
    assert verify_matrix_rank(over_abs, expected_rank=2)["verdict"] == "error"


# --- run integration ------------------------------------------------------

def test_confirmed_matrix_check_earns_instance_checked(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    entry = verify_matrix_rank_in_run(
        run_dir, matrix=[[1, 0], [0, 1]], entry_id="mx1", expected_rank=2, expected_determinant=1
    )
    assert entry["earned_status"] == "instance_checked"
    assert entry["verifier_type"] == "matrix_rank"
    assert "NOT a universal proof" in entry["scope_note"]
    validate_run(run_dir)


def test_refuted_matrix_check_stays_unverified(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    entry = verify_matrix_rank_in_run(run_dir, matrix=[[1, 2], [2, 4]], entry_id="mx2", expected_rank=2)
    assert entry["earned_status"] == "unverified"  # actual rank is 1
    validate_run(run_dir)


def test_finite_matrix_check_does_not_upgrade_idea_grade(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    grade_before = read_yaml(run_dir / "idea_grade.yaml")
    verify_matrix_rank_in_run(run_dir, matrix=[[2, 0], [0, 3]], entry_id="mx3", expected_determinant=6)
    validate_run(run_dir)
    grade_after = read_yaml(run_dir / "idea_grade.yaml")
    assert grade_after["status"] == grade_before["status"]
    assert grade_after["proof_language_status"] == grade_before["proof_language_status"]


# --- auto-wire request bridge ---------------------------------------------

def _write_requests(run_dir: Path, requests: list[dict]) -> None:
    write_yaml(
        run_dir / "scoped_verification_requests.yaml",
        {"claim_id": _claim_id(run_dir), "requests": requests},
    )


def test_auto_wire_matrix_rank_confirmed(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_requests(run_dir, [
        {"id": "r1", "kind": "matrix_rank", "table": [[1, 0], [0, 1]], "expected_rank": 2,
         "target": "rank of the identity 2x2"},
    ])
    summary = auto_wire_scoped_verifications(run_dir)
    assert summary["appended"] and summary["appended"][0]["earned_status"] == "instance_checked"
    assert any(e["verifier_type"] == "matrix_rank" and e["earned_status"] == "instance_checked"
               for e in _entries(run_dir))
    validate_run(run_dir)


def test_auto_wire_matrix_rank_over_cap_and_missing_expectation_skipped(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    big = [[1] * 13 for _ in range(13)]  # > AUTO_MAX_TABLE_ORDER (12)
    _write_requests(run_dir, [
        {"id": "big", "kind": "matrix_rank", "table": big, "expected_rank": 1},
        {"id": "noexp", "kind": "matrix_rank", "table": [[1, 0], [0, 1]]},  # no expected_*
    ])
    summary = auto_wire_scoped_verifications(run_dir)
    skipped = {s["id"] for s in summary["skipped"]}
    assert "big" in skipped and "noexp" in skipped
    assert not summary["appended"]
    validate_run(run_dir)


def test_auto_wire_wrong_claim_request_skipped(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    write_yaml(
        run_dir / "scoped_verification_requests.yaml",
        {"claim_id": "some_other_claim",
         "requests": [{"id": "r1", "kind": "matrix_rank", "table": [[1, 0], [0, 1]], "expected_rank": 2}]},
    )
    summary = auto_wire_scoped_verifications(run_dir)
    assert not summary["appended"]
    assert summary["skipped"]
    validate_run(run_dir)


# --- hard safety: an unconfirmed checked status is rejected ----------------

def test_hand_edited_unconfirmed_instance_checked_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    verify_matrix_rank_in_run(run_dir, matrix=[[1, 0], [0, 1]], entry_id="mx", expected_rank=2)
    pv = read_yaml(run_dir / "proof_verification.yaml")
    pv["entries"][0]["confirmed"] = False  # claim a checked status without a confirming result
    write_yaml(run_dir / "proof_verification.yaml", pv)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_cli_verify_matrix_rank(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    result = CliRunner().invoke(app, [
        "verify-matrix-rank-in-run", "--run", str(run_dir),
        "--matrix", "[[1,0],[0,1]]", "--id", "cli1", "--expected-rank", "2",
    ])
    assert result.exit_code == 0, result.output
    assert "instance_checked" in result.output
    validate_run(run_dir)
