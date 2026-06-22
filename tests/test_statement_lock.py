from __future__ import annotations

from pathlib import Path

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.formal.statement_lock import (
    build_statement_lock,
    formal_status_allowed,
    validate_statement_lock,
    write_statement_lock,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.mock_pipeline import run_mock_critique

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path) -> Path:
    run_dir = run_mock_critique(
        "statement_lock",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)
    return run_dir


def _claim_statement(run_dir: Path) -> str:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    return claim["normalized_statement"]


def test_statement_lock_accepts_explicit_exact_equivalence(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    lock = build_statement_lock(
        run_dir,
        formal_statement=_claim_statement(run_dir),
        explicit_equivalence_confirmed=True,
        equivalence_evidence="test fixture exact lock",
    )
    ok, reason = validate_statement_lock(run_dir, lock)
    assert ok is True, reason
    assert lock["equivalence_status"] == "locked_equivalent"
    assert formal_status_allowed(run_dir, lock) is True

    write_statement_lock(run_dir, lock)
    validate_run(run_dir)  # optional artifact is advisory; it must not break old gates


def test_statement_lock_rejects_unconfirmed_or_drifted_formal_statement(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    unconfirmed = build_statement_lock(
        run_dir,
        formal_statement=_claim_statement(run_dir),
        explicit_equivalence_confirmed=False,
    )
    ok, reason = validate_statement_lock(run_dir, unconfirmed)
    assert ok is False
    assert "not locked" in reason

    drifted = build_statement_lock(
        run_dir,
        formal_statement=_claim_statement(run_dir) + " with an extra hypothesis",
        explicit_equivalence_confirmed=True,
    )
    ok, reason = validate_statement_lock(run_dir, drifted)
    assert ok is False
    assert "not locked" in reason
    assert drifted["equivalence_status"] == "statement_drift"


def test_statement_lock_goes_stale_when_locked_claim_changes(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    lock = build_statement_lock(
        run_dir,
        formal_statement=_claim_statement(run_dir),
        explicit_equivalence_confirmed=True,
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_path = run_dir / manifest["artifacts"]["locked_claim"]
    claim = read_yaml(claim_path)
    claim["normalized_statement"] = claim["normalized_statement"] + " altered"
    write_yaml(claim_path, claim)

    ok, reason = validate_statement_lock(run_dir, lock)
    assert ok is False
    assert "stale" in reason
    assert formal_status_allowed(run_dir, lock) is False
