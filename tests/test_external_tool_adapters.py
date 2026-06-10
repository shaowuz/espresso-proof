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

from espresso_proof.external_tool_adapters import run_exact_modexp_adapter
from espresso_proof.io import read_yaml
from espresso_proof.memo import build_concise_memo
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.proof_language import contains_proof_language
from espresso_proof.validators import validate_run

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path) -> Path:
    return run_mock_critique("g25c", FIXTURES / "promising_but_unproved" / "input.md", tmp_path)


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
