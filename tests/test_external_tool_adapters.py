"""Goal 25 (slice 3): one allowlisted deterministic adapter.

The exact_modexp adapter runs a REAL deterministic tool (exact integer modular
exponentiation) in the sandbox and, ONLY on a confirmed result, earns a SCOPED
``computation_checked`` proof-verification entry THROUGH the kernel bridge. A
non-confirming / failed run, or any non-allowlisted tool, earns nothing. The
scoped entry never upgrades the grade to a universal proof.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.verification.external_tool_adapters import run_exact_modexp_adapter
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.kernel.proof_language import contains_proof_language
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path) -> Path:
    run = run_mock_critique("g25c", FIXTURES / "promising_but_unproved" / "input.md", tmp_path)
    manifest = read_yaml(run / "run_manifest.yaml")
    ledger_path = run / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run)
    return run


def _pv_entries(run: Path) -> list[dict]:
    path = run / "proof_verification.yaml"
    return read_yaml(path)["entries"] if path.exists() else []


def _grade(run: Path) -> dict:
    return read_yaml(run / read_yaml(run / "run_manifest.yaml")["artifacts"]["idea_grade"])


# --- confirming run earns a SCOPED entry through the bridge ----------------
def test_confirmed_run_earns_scoped_computation_checked(tmp_path: Path) -> None:
    run = _run(tmp_path)
    grade_before = _grade(run)
    result = run_exact_modexp_adapter(run, base=2, exp=10, mod=1000, expected=24, entry_id="m1")
    assert result["earned"] is True
    validate_run(run)  # the earned entry is kernel-admissible
    entries = _pv_entries(run)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["verifier_type"] == "exact_computation"
    assert entry["earned_status"] == "computation_checked"
    assert entry["trusted"] is True and entry["confirmed"] is True
    assert "not a universal proof" in entry["scope_note"].lower()
    # the external tool run is also recorded as raw provenance (trusted allowlisted)
    etr = read_yaml(run / "external_tool_runs.yaml")["runs"]
    assert any(r["tool_name"] == "exact_modexp" and r["trusted"] is True for r in etr)
    # earning a SCOPED entry does NOT upgrade the idea grade (Goal 23 honored)
    assert _grade(run) == grade_before


def test_confirmed_run_renders_scoped_no_proof_language(tmp_path: Path) -> None:
    run = _run(tmp_path)
    run_exact_modexp_adapter(run, base=3, exp=4, mod=100, expected=81, entry_id="m1")
    memo = build_concise_memo(run)
    # scoped machine-verified instance is shown; no global proof language
    assert "scoped" in memo.lower()
    assert contains_proof_language(memo) is None


# --- fail closed: non-confirming / bad input earns nothing ----------------
def test_wrong_expected_earns_nothing(tmp_path: Path) -> None:
    run = _run(tmp_path)
    result = run_exact_modexp_adapter(run, base=2, exp=10, mod=1000, expected=999, entry_id="m1")
    assert result["earned"] is False
    assert _pv_entries(run) == []  # no proof-verification entry written
    validate_run(run)  # the run (with only a raw tool record) still validates


def test_non_integer_or_bad_operands_fail_closed(tmp_path: Path) -> None:
    run = _run(tmp_path)
    with pytest.raises(ValueError, match="must be an int"):
        run_exact_modexp_adapter(run, base="2; rm -rf /", exp=1, mod=5, expected=2, entry_id="x")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="mod >= 1"):
        run_exact_modexp_adapter(run, base=2, exp=-1, mod=0, expected=0, entry_id="x")
    assert _pv_entries(run) == []


def test_earned_entry_is_only_via_bridge_and_admissible(tmp_path: Path) -> None:
    # The adapter's earned entry passes the kernel proof-verification gate
    # (trusted + confirmed + verifier_type exact_computation -> computation_checked);
    # a second confirming run appends a distinct entry through the same bridge.
    run = _run(tmp_path)
    run_exact_modexp_adapter(run, base=5, exp=3, mod=1000, expected=125, entry_id="a")
    run_exact_modexp_adapter(run, base=7, exp=2, mod=1000, expected=49, entry_id="b")
    validate_run(run)
    ids = {e["id"] for e in _pv_entries(run)}
    assert ids == {"a_pv", "b_pv"}


def test_confirmed_entry_carries_reproducibility_provenance(tmp_path: Path) -> None:
    # Goal 38: a checked computation records HOW to replay it. Provenance only;
    # the earning gate (trusted + confirmed via the kernel bridge) is unchanged.
    import hashlib

    run_dir = _run(tmp_path)
    result = run_exact_modexp_adapter(
        run_dir, base=4, exp=1, mod=4, expected=0, entry_id="repro_001"
    )
    assert result["earned"] is True
    report = read_yaml(run_dir / "proof_verification.yaml")
    entry = next(e for e in report["entries"] if e["id"] == "repro_001_pv")
    assert entry["adapter_id"] == "exact_modexp"
    assert entry["code_version"].startswith("espresso-proof ")
    assert entry["dependency_version"].startswith("python ")
    expected_input = hashlib.sha256(b"exact_modexp|4|1|4|0").hexdigest()
    assert entry["input_hash"] == expected_input
    assert entry["output_hash"] == hashlib.sha256(b"0").hexdigest()
    assert "--base 4 --exp 1 --mod 4 --expected 0 --id repro_001" in entry["replay_command"]
    # determinism: replaying the same inputs reproduces identical hashes.
    again = run_exact_modexp_adapter(
        run_dir, base=4, exp=1, mod=4, expected=0, entry_id="repro_002"
    )
    report2 = read_yaml(run_dir / "proof_verification.yaml")
    entry2 = next(e for e in report2["entries"] if e["id"] == "repro_002_pv")
    assert entry2["input_hash"] == entry["input_hash"]
    assert entry2["output_hash"] == entry["output_hash"]
    assert again["earned"] is True


def test_entries_without_reproducibility_fields_still_validate() -> None:
    # Additive-only schema change: pre-Goal-38 entries (no provenance fields)
    # must keep validating.
    from espresso_proof.kernel.models import ProofVerificationEntry

    entry = ProofVerificationEntry.model_validate(
        {
            "id": "legacy_001",
            "verifier_type": "exact_computation",
            "claim_id": "claim_001",
            "target": "t",
            "input_summary": "i",
            "result": "r",
            "trusted": True,
            "confirmed": True,
            "earned_status": "computation_checked",
            "scope_note": "scoped",
        }
    )
    assert entry.adapter_id == ""
    assert entry.replay_command == ""
