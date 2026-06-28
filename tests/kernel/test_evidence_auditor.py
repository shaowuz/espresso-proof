"""Real Evidence Auditor: a computation EARNS computation_checked only from a
confirming machine result. Agent never self-certifies; idea_grade untouched;
the verifier is DoS-hardened."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.kernel.validators import KernelValidationError, validate_run
from espresso_proof.verification.verifier import audit_evidence, verify_check

FIXTURES = Path("tests/fixtures")


def _mark_claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)


def _run_with_machine_check(tmp_path: Path, *, kind: str, expression: str, expected: str) -> Path:
    # vague claim => idea_grade unclear with empty survived/failed (no evidence
    # cross-checks), so the computation can be audited in isolation.
    run_dir = run_mock_critique("ea_demo", FIXTURES / "vague_claim_unclear" / "input.md", tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    claim_id = read_yaml(run_dir / artifacts["locked_claim"])["id"]
    comp = {
        "claim_id": claim_id,
        "checks": [
            {
                "id": "comp_001",
                "example_id": "ex_seed",
                "claim_id": claim_id,
                "computed_quantity": "a value",
                "input_data": [],
                "method": "manual_review_needed",
                "tool_or_source": "agent proposal",
                "result": "agent-proposed",
                "cross_check": [],
                "status": "human_needed",
                "machine_check": {"kind": kind, "expression": expression, "expected": expected, "notes": ""},
                "notes": "",
            }
        ],
    }
    write_yaml(run_dir / artifacts["computation_checks"], comp)
    _mark_claim_clear(run_dir)
    validate_run(run_dir)
    return run_dir


def _grade(run_dir: Path) -> dict:
    return read_yaml(run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["idea_grade"])


def test_confirmed_machine_check_earns_checked(tmp_path: Path) -> None:
    run_dir = _run_with_machine_check(tmp_path, kind="exact_arithmetic", expression="2+2", expected="4")
    before = _grade(run_dir)
    audit_evidence(run_dir)
    validate_run(run_dir)
    comp = read_yaml(run_dir / "computation_checks.yaml")
    assert comp["checks"][0]["status"] == "checked"
    assert comp["checks"][0]["method"] == "exact_arithmetic"
    ver = read_yaml(run_dir / "computation_verification.yaml")
    assert any(r["check_id"] == "comp_001" and r["verdict"] == "confirmed" for r in ver["results"])
    # idea_grade is NOT changed by auditing (per-computation evidence, not a proof).
    after = _grade(run_dir)
    assert after["proof_language_status"] == before["proof_language_status"]
    assert after["status"] == before["status"]


def test_refuted_machine_check_becomes_wrong_never_checked(tmp_path: Path) -> None:
    run_dir = _run_with_machine_check(tmp_path, kind="exact_arithmetic", expression="2+2", expected="5")
    audit_evidence(run_dir)
    validate_run(run_dir)
    comp = read_yaml(run_dir / "computation_checks.yaml")
    assert comp["checks"][0]["status"] == "wrong"
    assert comp["checks"][0]["status"] != "checked"


def test_permutation_order_confirmed_earns_checked(tmp_path: Path) -> None:
    # (1 2 3)(4 5) has order lcm(3,2)=6.
    run_dir = _run_with_machine_check(tmp_path, kind="permutation_order", expression="(1 2 3)(4 5)", expected="6")
    audit_evidence(run_dir)
    validate_run(run_dir)
    comp = read_yaml(run_dir / "computation_checks.yaml")
    assert comp["checks"][0]["status"] == "checked"
    assert comp["checks"][0]["method"] == "exact_arithmetic"  # method label; kind records permutation_order
    assert comp["checks"][0]["machine_check"]["kind"] == "permutation_order"


def test_error_and_unsupported_stay_human_needed(tmp_path: Path) -> None:
    bad = _run_with_machine_check(tmp_path, kind="exact_arithmetic", expression="2+/", expected="4")
    audit_evidence(bad)
    validate_run(bad)
    assert read_yaml(bad / "computation_checks.yaml")["checks"][0]["status"] == "human_needed"


def test_verifier_dos_huge_exponent_fails_closed_fast() -> None:
    check = {
        "id": "c", "claim_id": "claim_001",
        "machine_check": {"kind": "exact_arithmetic", "expression": "2", "expected": "10**10000000", "notes": ""},
    }
    start = time.monotonic()
    result = verify_check(check)
    elapsed = time.monotonic() - start
    assert result["verdict"] == "error"  # never confirmed
    assert elapsed < 2.0  # guard prevents the giant computation (no hang)


def test_dos_spec_never_earns_checked(tmp_path: Path) -> None:
    run_dir = _run_with_machine_check(tmp_path, kind="exact_arithmetic", expression="9**9**9", expected="9**9**9")
    audit_evidence(run_dir)
    validate_run(run_dir)
    assert read_yaml(run_dir / "computation_checks.yaml")["checks"][0]["status"] == "human_needed"


def test_refuted_cannot_be_hand_flipped_to_checked(tmp_path: Path) -> None:
    run_dir = _run_with_machine_check(tmp_path, kind="exact_arithmetic", expression="2+2", expected="5")
    audit_evidence(run_dir)  # -> wrong, computation_verification present (refuted)
    comp = read_yaml(run_dir / "computation_checks.yaml")
    comp["checks"][0]["status"] = "checked"
    comp["checks"][0]["method"] = "exact_arithmetic"
    write_yaml(run_dir / "computation_checks.yaml", comp)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)  # refuted check cannot claim checked


def test_examples_stage_never_self_certifies_machine_check(tmp_path: Path) -> None:
    # A model that supplies a machine_check AND tries status=checked: the examples
    # normalizer stores the spec but keeps the computation human_needed.
    from espresso_proof.agents.front_half_agents import run_front_half_agent_pass
    from tests.orchestration.test_front_half_agents import _FakeClient

    run_dir = run_mock_critique("ea_demo", FIXTURES / "vague_claim_unclear" / "input.md", tmp_path)
    _mark_claim_clear(run_dir)
    client = _FakeClient(
        {
            "examples": [
                {"name": "ex A", "satisfies_all_claim_hypotheses": True, "what_it_tests": ["x"],
                 "expected_conclusion": "c", "observed_or_inferred_behavior": "b",
                 "nontriviality": "standard", "source_or_reason": "r"}
            ],
            "computation_checks": [
                {"example_name": "ex A", "computed_quantity": "2+2", "result": "4",
                 "status": "checked",  # must be ignored
                 "machine_check": {"kind": "exact_arithmetic", "expression": "2+2", "expected": "4"}},
            ],
        }
    )
    run_front_half_agent_pass(run_dir=run_dir, client=client, stages=["examples"])
    validate_run(run_dir)
    comp = read_yaml(run_dir / "computation_checks.yaml")
    assert comp["checks"], comp
    for c in comp["checks"]:
        assert c["status"] == "human_needed"  # never self-certified
        assert c["machine_check"]["kind"] == "exact_arithmetic"  # spec stored for the machine


def test_memo_and_inspect_render_machine_checked(tmp_path: Path) -> None:
    run_dir = _run_with_machine_check(tmp_path, kind="exact_arithmetic", expression="2+2", expected="4")
    audit_evidence(run_dir)
    runner = CliRunner()
    memo = runner.invoke(app, ["memo", "--run", str(run_dir)]).output
    inspected = runner.invoke(app, ["inspect", "--run", str(run_dir)]).output
    assert "Proof-route steps:" in memo
    assert "Checked by computation for the stated finite/computational scope" in memo
    assert "machine-checked" in inspected.lower()
    for surface in (memo.lower(), inspected.lower()):
        for phrase in ("proof-close", "proof complete", "we proved", "proved the claim"):
            assert phrase not in surface
