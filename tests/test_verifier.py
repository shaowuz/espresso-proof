from __future__ import annotations

import pytest

from espresso_proof.kernel.models import ComputationChecks, ComputationVerificationReport
from espresso_proof.kernel.validators import (
    KernelValidationError,
    _validate_computation_verification,
)
from espresso_proof.verification.verifier import (
    VerifierError,
    _eval_arith,
    _matrix_determinant,
    _matrix_rank,
    _parse_matrix,
    _parse_polynomial,
    _permutation_order,
    _poly_str,
    build_verification_report,
    verify_check,
)


def _check(check_id: str, status: str, kind: str, expression: str, expected: str) -> dict:
    return {
        "id": check_id,
        "example_id": "ex_1",
        "claim_id": "claim_1",
        "computed_quantity": "q",
        "input_data": [],
        "method": "exact_arithmetic" if kind == "exact_arithmetic" else "CAS",
        "tool_or_source": "espresso-stdlib-verifier",
        "result": expected,
        "cross_check": [],
        "status": status,
        "machine_check": {"kind": kind, "expression": expression, "expected": expected},
        "notes": "",
    }


# --- backend execution ---------------------------------------------------


def test_eval_arith_exact_rational():
    assert _eval_arith("1/3 + 1/6") == _eval_arith("1/2")
    assert _eval_arith("2**5 - 9") == _eval_arith("23")


def test_eval_arith_rejects_unsafe():
    with pytest.raises(VerifierError):
        _eval_arith("__import__('os').system('echo hi')")
    with pytest.raises(VerifierError):
        _eval_arith("1/0")


def test_permutation_order_lcm_of_cycle_lengths():
    assert _permutation_order("(1 2 3)(4 5)") == 6
    assert _permutation_order("(1 2 3 4 5 6 7)") == 7


def test_verify_check_confirmed_and_refuted():
    confirmed = verify_check(_check("c1", "checked", "exact_arithmetic", "2**5 - 9", "23"))
    assert confirmed["verdict"] == "confirmed"
    assert confirmed["executed"] is True

    refuted = verify_check(_check("c2", "checked", "exact_arithmetic", "2**5 - 9", "24"))
    assert refuted["verdict"] == "refuted"
    assert refuted["computed"] == "23"


def test_verify_check_unsafe_is_error_not_executed():
    result = verify_check(_check("c3", "inconclusive", "exact_arithmetic", "foo(1)", "1"))
    assert result["verdict"] == "error"
    assert result["executed"] is False


def test_finite_enumeration_confirms_true_sum_identity():
    result = verify_check(
        _check(
            "fe1",
            "checked",
            "finite_enumeration",
            "@n[1:1000] sum(1,n)",
            "@n[1:1000] n*(n+1)/2",
        )
    )
    assert result["verdict"] == "confirmed"
    assert result["executed"] is True
    assert result["backend"] == "stdlib_finite_enumeration"


def test_finite_enumeration_confirms_prod_identity():
    result = verify_check(
        _check(
            "fe2",
            "checked",
            "finite_enumeration",
            "@k[0:0] prod(1,5)",
            "@k[0:0] 120",
        )
    )
    assert result["verdict"] == "confirmed"


def test_finite_enumeration_refutes_false_identity_with_counterexample():
    result = verify_check(
        _check("fe3", "checked", "finite_enumeration", "@n[1:50] n**2", "@n[1:50] 2*n")
    )
    assert result["verdict"] == "refuted"
    assert "counterexample" in result["computed"]


def test_finite_enumeration_fails_closed_on_huge_range():
    result = verify_check(
        _check(
            "fe4",
            "checked",
            "finite_enumeration",
            "@n[1:99999999] n",
            "@n[1:99999999] n",
        )
    )
    assert result["verdict"] == "error"
    assert result["executed"] is False


def test_finite_enumeration_fails_closed_on_composed_work_budget():
    result = verify_check(
        _check(
            "fe4b",
            "checked",
            "finite_enumeration",
            "@n[1:100000] sum(1,100000)",
            "@n[1:100000] 5000050000",
        )
    )
    assert result["verdict"] == "error"
    assert "work too large" in result["detail"]


def test_finite_enumeration_rejects_mismatched_range():
    result = verify_check(
        _check("fe5", "checked", "finite_enumeration", "@n[1:10] n", "@n[1:20] n")
    )
    assert result["verdict"] == "error"


def test_finite_enumeration_rejects_arbitrary_calls():
    result = verify_check(
        _check("fe6", "checked", "finite_enumeration", "@n[1:5] foo(n)", "@n[1:5] n")
    )
    assert result["verdict"] == "error"


def test_finite_enumeration_rejects_unknown_names():
    result = verify_check(
        _check("fe7", "checked", "finite_enumeration", "@n[1:5] m", "@n[1:5] n")
    )
    assert result["verdict"] == "error"


def test_verify_check_none_without_spec():
    check = _check("c4", "checked", "exact_arithmetic", "1", "1")
    check.pop("machine_check")
    assert verify_check(check) is None


def test_permutation_order_check_confirmed():
    result = verify_check(_check("c5", "checked", "permutation_order", "(1 2 3)(4 5)", "6"))
    assert result["verdict"] == "confirmed"


# --- validator enforcement ----------------------------------------------


def _report(results: list[dict]) -> ComputationVerificationReport:
    return ComputationVerificationReport.model_validate(
        {
            "schema_version": 1,
            "claim_id": "claim_1",
            "generated_at": "2026-06-03T00:00:00+00:00",
            "backend": "espresso-stdlib-verifier",
            "results": results,
        }
    )


def _checks(checks: list[dict]) -> ComputationChecks:
    return ComputationChecks.model_validate({"claim_id": "claim_1", "checks": checks})


def test_validator_blocks_refuted_checked():
    checks = _checks([_check("c1", "checked", "exact_arithmetic", "2**5 - 9", "24")])
    report = _report([verify_check(checks.checks[0].model_dump())])
    with pytest.raises(KernelValidationError, match="refuted"):
        _validate_computation_verification(checks, report)


def test_validator_requires_confirmation_for_checked_with_spec():
    checks = _checks([_check("c1", "checked", "exact_arithmetic", "2**5 - 9", "23")])
    # report missing the confirming result
    report = _report([])
    with pytest.raises(KernelValidationError, match="no confirming"):
        _validate_computation_verification(checks, report)


def test_validator_passes_confirmed_checked():
    checks = _checks([_check("c1", "checked", "exact_arithmetic", "2**5 - 9", "23")])
    report = _report([verify_check(checks.checks[0].model_dump())])
    _validate_computation_verification(checks, report)  # no raise


def test_validator_unknown_check_id():
    checks = _checks([_check("c1", "inconclusive", "exact_arithmetic", "1", "1")])
    report = _report(
        [
            {
                "check_id": "ghost",
                "claim_id": "claim_1",
                "kind": "exact_arithmetic",
                "backend": "stdlib_fraction",
                "executed": True,
                "verdict": "confirmed",
                "computed": "1",
                "expected": "1",
                "detail": "",
            }
        ]
    )
    with pytest.raises(KernelValidationError, match="unknown check"):
        _validate_computation_verification(checks, report)


def test_validator_noop_without_verification():
    checks = _checks([_check("c1", "checked", "exact_arithmetic", "2**5 - 9", "24")])
    _validate_computation_verification(checks, None)  # no raise when no report


def test_build_report_skips_specless_checks():
    spec_check = _check("c1", "checked", "exact_arithmetic", "1+1", "2")
    plain = _check("c2", "inconclusive", "exact_arithmetic", "x", "x")
    plain.pop("machine_check")
    report = build_verification_report({"claim_id": "claim_1", "checks": [spec_check, plain]})
    assert [r["check_id"] for r in report["results"]] == ["c1"]


# --- linear algebra over Q backends -------------------------------------


def test_parse_matrix_exact_fractions():
    matrix = _parse_matrix("[[1, 1/2], [-3, 4]]")
    assert matrix == [[_eval_arith("1"), _eval_arith("1/2")], [_eval_arith("-3"), _eval_arith("4")]]


def test_parse_matrix_rejects_ragged_and_oversize():
    with pytest.raises(VerifierError):
        _parse_matrix("[[1, 2], [3]]")  # ragged
    with pytest.raises(VerifierError):
        _parse_matrix("[" + ",".join("[0]" for _ in range(13)) + "]")  # too many rows


def test_matrix_rank_exact():
    assert _matrix_rank([[_eval_arith("1"), _eval_arith("0")], [_eval_arith("0"), _eval_arith("1")]]) == 2
    # singular: second row is twice the first -> rank 1
    assert _matrix_rank([[_eval_arith("1"), _eval_arith("2")], [_eval_arith("2"), _eval_arith("4")]]) == 1
    # rectangular, full row rank
    assert _matrix_rank([[_eval_arith("1"), _eval_arith("2"), _eval_arith("3")]]) == 1


def test_matrix_determinant_exact_including_rational_entries():
    assert _matrix_determinant([[_eval_arith("1"), _eval_arith("2")], [_eval_arith("3"), _eval_arith("4")]]) == _eval_arith("-2")
    # rational entries, exact result
    assert _matrix_determinant([[_eval_arith("1/2"), _eval_arith("0")], [_eval_arith("0"), _eval_arith("1/3")]]) == _eval_arith("1/6")
    # singular -> 0
    assert _matrix_determinant([[_eval_arith("1"), _eval_arith("2")], [_eval_arith("2"), _eval_arith("4")]]) == 0


def test_matrix_determinant_requires_square():
    with pytest.raises(VerifierError):
        _matrix_determinant([[_eval_arith("1"), _eval_arith("2"), _eval_arith("3")]])


def test_verify_check_matrix_rank_confirmed_and_refuted():
    confirmed = verify_check(_check("r1", "checked", "matrix_rank", "[[1, 2], [2, 4]]", "1"))
    assert confirmed["verdict"] == "confirmed"
    assert confirmed["executed"] is True
    assert confirmed["backend"] == "stdlib_linalg_rank"

    refuted = verify_check(_check("r2", "checked", "matrix_rank", "[[1, 2], [2, 4]]", "2"))
    assert refuted["verdict"] == "refuted"
    assert refuted["computed"] == "1"


def test_verify_check_matrix_determinant_confirmed_and_refuted():
    confirmed = verify_check(_check("d1", "checked", "matrix_determinant", "[[1, 2], [3, 4]]", "-2"))
    assert confirmed["verdict"] == "confirmed"
    assert confirmed["backend"] == "stdlib_linalg_det"

    refuted = verify_check(_check("d2", "checked", "matrix_determinant", "[[1, 2], [3, 4]]", "-3"))
    assert refuted["verdict"] == "refuted"
    assert refuted["computed"] == "-2"


def test_verify_check_matrix_malformed_is_error_not_executed():
    # non-square determinant fails closed to error, never confirmed
    result = verify_check(_check("d3", "inconclusive", "matrix_determinant", "[[1, 2, 3]]", "0"))
    assert result["verdict"] == "error"
    assert result["executed"] is False


def test_validator_accepts_confirmed_matrix_and_blocks_refuted():
    # a real machine-confirmed determinant earns "checked"
    ok = _checks([_check("c1", "checked", "matrix_determinant", "[[1, 2], [3, 4]]", "-2")])
    _validate_computation_verification(ok, _report([verify_check(ok.checks[0].model_dump())]))  # no raise
    # a refuted determinant cannot support "checked"
    bad = _checks([_check("c1", "checked", "matrix_determinant", "[[1, 2], [3, 4]]", "-3")])
    with pytest.raises(KernelValidationError, match="refuted"):
        _validate_computation_verification(bad, _report([verify_check(bad.checks[0].model_dump())]))


# --- polynomial identity over Q backend ---------------------------------


def test_parse_polynomial_normal_form_is_canonical():
    # (x+1)^2 and x^2 + 2x + 1 normalize to the same polynomial
    assert _parse_polynomial("(x + 1)**2") == _parse_polynomial("x**2 + 2*x + 1")
    # order and grouping do not matter
    assert _parse_polynomial("1 + x") == _parse_polynomial("x + 1")


def test_parse_polynomial_multivariate_and_constant_division():
    assert _parse_polynomial("(x + y)**2") == _parse_polynomial("x**2 + 2*x*y + y**2")
    # division by a nonzero constant is exact over Q
    assert _parse_polynomial("(2*x + 2)/2") == _parse_polynomial("x + 1")


def test_parse_polynomial_rejects_nonconstant_division():
    with pytest.raises(VerifierError):
        _parse_polynomial("x**2 / x")  # division by a non-constant polynomial
    with pytest.raises(VerifierError):
        _parse_polynomial("1/0")  # division by zero


def test_poly_str_is_stable():
    assert _poly_str(_parse_polynomial("x + 1")) == _poly_str(_parse_polynomial("1 + x"))


def test_verify_check_polynomial_identity_confirmed_and_refuted():
    confirmed = verify_check(_check("p1", "checked", "polynomial_identity", "(x + 1)**2", "x**2 + 2*x + 1"))
    assert confirmed["verdict"] == "confirmed"
    assert confirmed["executed"] is True
    assert confirmed["backend"] == "stdlib_polynomial"

    refuted = verify_check(_check("p2", "checked", "polynomial_identity", "(x + 1)**2", "x**2 + 1"))
    assert refuted["verdict"] == "refuted"


def test_verify_check_polynomial_malformed_is_error_not_executed():
    result = verify_check(_check("p3", "inconclusive", "polynomial_identity", "x**2 / x", "x"))
    assert result["verdict"] == "error"
    assert result["executed"] is False


def test_validator_accepts_confirmed_polynomial_and_blocks_refuted():
    # divisibility is expressible as an identity f == g*q; a confirmed factorization earns "checked"
    ok = _checks([_check("c1", "checked", "polynomial_identity", "x**2 - 1", "(x - 1)*(x + 1)")])
    _validate_computation_verification(ok, _report([verify_check(ok.checks[0].model_dump())]))  # no raise
    bad = _checks([_check("c1", "checked", "polynomial_identity", "x**2 - 1", "(x - 1)*(x - 1)")])
    with pytest.raises(KernelValidationError, match="refuted"):
        _validate_computation_verification(bad, _report([verify_check(bad.checks[0].model_dump())]))
