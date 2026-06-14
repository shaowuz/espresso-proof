"""Goal 57C: durable rational-witness counterexample for a FALSE polynomial identity.

The polynomial_identity backend already verifies confirmed/refuted over Q. These
tests cover the genuinely-missing piece: when the identity is FALSE, an explicit
rational witness (assignment + exact lhs/rhs/nonzero difference) is produced and
persisted as a step-only CounterexampleTest, with hard caps that fail closed.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.models import CounterexampleTest
from espresso_proof.orchestrator import orchestrate_run
from espresso_proof.validators import validate_run
from espresso_proof.verifier import (
    VerifierError,
    find_polynomial_counterexample,
    persist_polynomial_witness,
    verify_check,
    _parse_polynomial,
)


def _check(check_id: str, expression: str, expected: str) -> dict:
    return {
        "id": check_id,
        "example_id": "ex_1",
        "claim_id": "claim_001",
        "computed_quantity": "q",
        "input_data": [],
        "method": "CAS",
        "tool_or_source": "espresso-stdlib-verifier",
        "result": expected,
        "cross_check": [],
        "status": "inconclusive",
        "machine_check": {
            "kind": "polynomial_identity",
            "expression": expression,
            "expected": expected,
        },
        "notes": "",
    }


def _run_dir(tmp_path: Path) -> Path:
    claim = tmp_path / "claim.md"
    claim.write_text(
        "Let p be a prime. Every group G of order p^2 is abelian.\n", encoding="utf-8"
    )
    result = orchestrate_run(
        project="poly57c",
        input_path=claim,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
    )
    return Path(result["run_dir"])


# --- the witness itself --------------------------------------------------


def test_false_identity_produces_exact_rational_witness():
    # (x+y)^3 != x^3 + y^3 over Q
    result = verify_check(_check("p1", "(x + y)**3", "x**3 + y**3"))
    assert result["verdict"] == "refuted"
    witness = result["witness"]
    assignment = witness["assignment"]
    # accept whichever grid point the search finds first; it must be a real
    # separating point with difference == lhs - rhs and difference != 0.
    subs = {var: Fraction(val) for var, val in assignment.items()}
    lhs = _parse_polynomial("(x + y)**3")
    rhs = _parse_polynomial("x**3 + y**3")
    from espresso_proof.verifier import _poly_eval_at

    lhs_val = _poly_eval_at(lhs, subs)
    rhs_val = _poly_eval_at(rhs, subs)
    assert lhs_val != rhs_val
    assert Fraction(witness["lhs_value"]) == lhs_val
    assert Fraction(witness["rhs_value"]) == rhs_val
    assert Fraction(witness["difference"]) == lhs_val - rhs_val
    assert Fraction(witness["difference"]) != 0


def test_true_identity_has_no_witness_and_no_status_upgrade():
    result = verify_check(_check("p2", "(x + 1)**2", "x**2 + 2*x + 1"))
    assert result["verdict"] == "confirmed"
    assert "witness" not in result
    # a TRUE scoped identity never carries any global/proof status field.
    assert "proved" not in result
    assert result.get("idea_grade") is None


# --- persistence through the EXISTING counterexamples path ----------------


def test_witness_persisted_as_step_only_counterexample(tmp_path):
    run_dir = _run_dir(tmp_path)
    check = _check("p_persist", "(x + y)**3", "x**3 + y**3")
    result = verify_check(check)
    path = persist_polynomial_witness(run_dir, check, result)
    assert path is not None and path.name == "counterexamples.yaml"

    bank = read_yaml(path)
    tests = [t for t in bank["tests"] if t["id"] == "polywitness_p_persist"]
    assert len(tests) == 1
    test = tests[0]
    # conservative scope flags enforce the boundary
    assert test["satisfies_all_claim_hypotheses"] is False
    assert test["refutes_step_only"] is True
    assert test["refutes_claim"] is False
    assert test["target"] == "proof_step"
    assert test["result"] == "failed"
    # CounterexampleTest model accepts it (scope guard passes)
    CounterexampleTest.model_validate(test)
    # run still valid after persisting a witness
    validate_run(run_dir)


def test_persist_is_idempotent(tmp_path):
    run_dir = _run_dir(tmp_path)
    check = _check("p_idem", "(x + y)**3", "x**3 + y**3")
    result = verify_check(check)
    persist_polynomial_witness(run_dir, check, result)
    persist_polynomial_witness(run_dir, check, result)
    bank = read_yaml(run_dir / "counterexamples.yaml")
    assert sum(1 for t in bank["tests"] if t["id"] == "polywitness_p_idem") == 1


def test_confirmed_identity_persists_nothing(tmp_path):
    run_dir = _run_dir(tmp_path)
    check = _check("p_true", "(x + 1)**2", "x**2 + 2*x + 1")
    result = verify_check(check)
    assert persist_polynomial_witness(run_dir, check, result) is None


# --- scope guard is structural, not prose --------------------------------


def test_polynomial_witness_with_refutes_claim_true_is_rejected():
    check = _check("p_bad", "(x + y)**3", "x**3 + y**3")
    result = verify_check(check)
    from espresso_proof.verifier import _polynomial_witness_test

    test = _polynomial_witness_test(check, result)
    # an attempt to escalate the step-only witness to a claim refutation is
    # rejected by CounterexampleTest.validate_refutation_scope (structural).
    test["refutes_claim"] = True
    with pytest.raises(ValidationError):
        CounterexampleTest.model_validate(test)


# --- fail-closed caps ----------------------------------------------------


def test_too_many_variables_fails_closed():
    expr = " + ".join("abcdefg")  # 7 distinct variables > _MAX_WITNESS_VARS (6)
    result = verify_check(_check("p_vars", expr, "0"))
    assert result["verdict"] == "error"
    assert result["executed"] is False
    assert "witness" not in result


def test_expansion_exploding_input_fails_closed():
    # A large product of distinct binomials: within length but explodes the
    # normal-form size, tripping the existing _MAX_POLY_TERMS cap -> error.
    factors = "*".join(f"(x{i} + 1)" for i in range(40))
    result = verify_check(_check("p_explode", factors, "1"))
    assert result["verdict"] == "error"
    assert result["executed"] is False
    assert "witness" not in result


def test_high_total_degree_fails_closed():
    # within length and within the term cap, but total degree 60 > 50 -> error,
    # so an over-degree FALSE identity earns NO witness (fail closed).
    result = verify_check(_check("p_deg", "x**60", "x**60 + 1"))
    assert result["verdict"] == "error"
    assert "witness" not in result


def test_malformed_and_undeclared_inputs_fail_closed():
    # division by a non-constant polynomial (non-Q-polynomial domain)
    bad = verify_check(_check("p_div", "x**2 / x", "x"))
    assert bad["verdict"] == "error"
    # a syntactically malformed expression
    malformed = verify_check(_check("p_syntax", "x +", "x"))
    assert malformed["verdict"] == "error"


def test_find_counterexample_caps_raise_directly():
    many = {((f"v{i}", 1),): Fraction(1) for i in range(7)}
    with pytest.raises(VerifierError):
        find_polynomial_counterexample(many, {})
