from __future__ import annotations

import pytest

from espresso_proof.models import ComputationChecks, ComputationVerificationReport
from espresso_proof.validators import (
    KernelValidationError,
    _validate_computation_verification,
)
from espresso_proof.verifier import (
    VerifierError,
    _eval_arith,
    _permutation_order,
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
