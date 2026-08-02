"""The machine-check lane must be able to state a congruence.

Found by the five-round live audit. `check_generator` is the only lane that can
earn `computation_checked` without a human, and on a claim about `a^(p-1) mod
p^2` it recorded "appended 0 candidate machine check(s); dropped 4": the
evaluator allowed only `+ - * / **`, so no proposal about residues could be
executed and all four were refused. The refusals were also collapsed into a
single count, so the record could not say why.
"""

from __future__ import annotations

import pytest

from espresso_proof.verification.machine_check_guards import is_executable_machine_check
from espresso_proof.verification.verifier import VerifierError, _eval_arith, verify_check


def _check(kind: str, expression: str, expected: str) -> dict:
    return {
        "id": "c", "example_id": "e", "claim_id": "cl",
        "computed_quantity": "q", "input_data": [], "method": "manual_review_needed",
        "tool_or_source": "t", "result": expected, "cross_check": [],
        "status": "human_needed", "notes": "",
        "machine_check": {
            "kind": kind, "expression": expression, "expected": expected,
            "target_step_id": "s", "notes": "",
        },
    }


def test_a_congruence_is_now_expressible() -> None:
    assert _eval_arith("2**10 % 121") == 56
    assert _eval_arith("pow(2,10,121)") == 56
    # pow(a,b,m) is the only form that keeps a large exponent inside the
    # result-size bound: 2**1000 alone would be refused as too large.
    assert _eval_arith("pow(2,1000,121)") == _eval_arith("2**1000 % 121")


def test_real_number_theory_runs_end_to_end() -> None:
    # Fermat's little theorem for p = 11, checked over every residue...
    confirmed = verify_check(
        _check("finite_enumeration", "@a[1:10] pow(a,10,11)", "@a[1:10] 1")
    )
    assert confirmed["verdict"] == "confirmed" and confirmed["executed"]

    # ...and the p^2 lift refuted with an explicit witness.
    refuted = verify_check(
        _check("finite_enumeration", "@a[1:10] pow(a,10,121)", "@a[1:10] 1")
    )
    assert refuted["verdict"] == "refuted"
    assert "a=2" in refuted["computed"]


def test_the_generator_now_accepts_a_modular_check() -> None:
    assert is_executable_machine_check(
        {"kind": "exact_arithmetic", "expression": "pow(2,10,121)",
         "expected": "56", "target_step_id": "s", "notes": ""}
    )


@pytest.mark.parametrize(
    "expression",
    ["5 % 0", "pow(2,3,0)", "(1/2) % 3", "pow(2,-1,7)", "pow(2,10**6,121)"],
)
def test_unsafe_or_undefined_modular_forms_fail_closed(expression: str) -> None:
    with pytest.raises(VerifierError):
        _eval_arith(expression)


def test_a_drop_says_why() -> None:
    from espresso_proof.front_half.stages.check_generator import _normalize_generated_checks

    generated, dropped, reasons = _normalize_generated_checks(
        [
            {"kind": "lean", "expression": "x", "expected": "y", "target_step_id": "s1"},
            {"kind": "exact_arithmetic", "expression": "2+2", "expected": "4",
             "target_step_id": "does_not_exist"},
            {"kind": "exact_arithmetic", "expression": "pow(2,10,121)", "expected": "56",
             "target_step_id": "s1"},
        ],
        claim_id="c", default_example_id="ex_001", step_ids={"s1"}, existing_checks=[],
    )
    assert len(generated) == 1  # the modular one, which used to be impossible
    assert dropped == 2
    assert reasons == {"unsupported_kind": 1, "unknown_target_step": 1}
