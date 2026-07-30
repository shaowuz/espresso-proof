"""The second checked-status path had to be recomputed too.

Round one and round two of the hostile review closed `proof_verification.yaml`:
an entry claiming a checked status is now re-derived by the kernel and bound to
what the adapter says it checked. Round three found the same defect one artifact
over. `computation_verification.yaml` carries `executed` and `verdict` booleans
written by whoever wrote the file, and they back the grade-level
`computation_checked` (`kernel/validators/_part3.py`) and a `scoped_checked`
subgoal node (`_part4.py`). Nothing re-ran them.

Reproduced end to end before the fix: a run whose ONLY machine evidence was a
hand-written result naming `a_backend_that_does_not_exist`, whose expression was
the prose sentence "forall p prime: every group of order p^2 is abelian", passed
`validate_run` and moved `proof_language_status` from `idea_sketch` to
`computation_checked`.

The rule now: an executed, confirming result must survive the kernel re-deriving
it through the same backend the single writer uses — verdict, backend and
computed value all have to agree.
"""

from __future__ import annotations

import pytest

from espresso_proof.kernel.models import (
    ComputationChecks,
    ComputationVerificationReport,
    ComputationVerificationResult,
)
from espresso_proof.kernel.recompute import recompute_computation_check
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validation.evidence import _validate_computation_verification


def _checks(status: str = "inconclusive") -> ComputationChecks:
    return ComputationChecks.model_validate(
        {
            "claim_id": "claim_001",
            "checks": [
                {
                    "id": "chk_001",
                    "example_id": "ex_001",
                    "claim_id": "claim_001",
                    "computed_quantity": "2 + 2",
                    "input_data": ["2", "2"],
                    "method": "exact_arithmetic",
                    "tool_or_source": "stdlib_fraction",
                    "result": "4",
                    "status": status,
                }
            ],
        }
    )


def _report(**over) -> ComputationVerificationReport:
    result = {
        "check_id": "chk_001",
        "claim_id": "claim_001",
        "kind": "exact_arithmetic",
        "expression": "2 + 2",
        "expected": "4",
        "backend": "stdlib_fraction",
        "executed": True,
        "verdict": "confirmed",
        "computed": "4",
        "detail": "executed '2 + 2'",
    }
    result.update(over)
    return ComputationVerificationReport.model_validate(
        {
            "claim_id": "claim_001",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "backend": "espresso_verifier",
            "results": [result],
        }
    )


# --- the reproduced attack


def test_the_forged_result_is_rejected() -> None:
    forged = _report(
        kind="exact_arithmetic",
        expression="forall p prime: every group of order p^2 is abelian",
        expected="true",
        computed="true",
        backend="a_backend_that_does_not_exist",
        detail="no machine ever ran; this line is hand-written",
    )
    with pytest.raises(KernelValidationError, match="re-derived 'error'"):
        _validate_computation_verification(_checks(), forged)


def test_a_false_arithmetic_confirmation_is_rejected() -> None:
    with pytest.raises(KernelValidationError, match="re-derived 'refuted'"):
        _validate_computation_verification(_checks(), _report(expected="5", computed="5"))


def test_an_unknown_kind_cannot_confirm() -> None:
    # 'unsupported' is what the backend returns for a kind it does not implement:
    # an invented kind must not be able to smuggle a confirmation through.
    with pytest.raises(KernelValidationError, match="re-derived 'unsupported'"):
        _validate_computation_verification(_checks(), _report(kind="oracle_says_yes"))


def test_an_invented_backend_is_rejected_even_when_the_math_is_right() -> None:
    # 2 + 2 == 4 really does hold, so the verdict leg passes; attribution to a
    # backend that did not produce it must still fail.
    with pytest.raises(KernelValidationError, match="backend"):
        _validate_computation_verification(_checks(), _report(backend="lean4"))


def test_a_doctored_computed_value_is_rejected() -> None:
    with pytest.raises(KernelValidationError, match="records computed"):
        _validate_computation_verification(_checks(), _report(computed="4 (and therefore all p)"))


# --- the legitimate path must still pass, or the gate is useless


def test_a_genuine_confirming_result_passes() -> None:
    _validate_computation_verification(_checks(), _report())


def test_a_genuine_result_still_backs_a_checked_computation() -> None:
    checks = ComputationChecks.model_validate(
        {
            "claim_id": "claim_001",
            "checks": [
                {
                    "id": "chk_001",
                    "example_id": "ex_001",
                    "claim_id": "claim_001",
                    "computed_quantity": "2 + 2",
                    "input_data": ["2", "2"],
                    "method": "exact_arithmetic",
                    "tool_or_source": "stdlib_fraction",
                    "result": "4",
                    "status": "checked",
                    "machine_check": {
                        "kind": "exact_arithmetic",
                        "expression": "2 + 2",
                        "expected": "4",
                    },
                }
            ],
        }
    )
    _validate_computation_verification(checks, _report())


def test_unexecuted_and_refuted_results_are_not_gated() -> None:
    # The gate applies to confirmations only: a result the backend could not run,
    # or ran and refuted, claims nothing that needs re-deriving. (A refuted
    # result is separately forbidden from backing a checked status.)
    _validate_computation_verification(
        _checks(), _report(executed=False, verdict="error", computed="", backend="espresso_verifier")
    )
    _validate_computation_verification(_checks(), _report(verdict="refuted", computed="5"))


# --- the recompute helper itself


def test_recompute_is_deterministic_and_reports_its_backend() -> None:
    first = recompute_computation_check("exact_arithmetic", "2 + 2", "4")
    assert first == {"verdict": "confirmed", "backend": "stdlib_fraction", "computed": "4"}
    assert recompute_computation_check("exact_arithmetic", "2 + 2", "4") == first


def test_recompute_never_raises_on_junk() -> None:
    for spec in (
        ("exact_arithmetic", "import os", "0"),
        ("exact_arithmetic", "2 +", "4"),
        ("", "", ""),
        (None, None, None),
        ("exact_arithmetic", "2 ** 10 ** 10", "1"),
    ):
        out = recompute_computation_check(*spec)
        assert out is None or out["verdict"] != "confirmed", spec


def test_the_check_id_is_named_in_every_rejection() -> None:
    # The message has to identify which check failed; a run can carry many.
    for report in (
        _report(expected="5", computed="5"),
        _report(backend="lean4"),
        _report(kind="oracle_says_yes"),
    ):
        with pytest.raises(KernelValidationError, match="chk_001"):
            _validate_computation_verification(_checks(), report)


def test_only_the_writer_backend_names_survive() -> None:
    # Documents the closed set: a confirming result's backend must be one the
    # kernel can produce, so a future writer cannot invent an authority label.
    assert recompute_computation_check("exact_arithmetic", "2 + 2", "4")["backend"] == (
        "stdlib_fraction"
    )
    assert recompute_computation_check("matrix_rank", "[[1,0],[0,1]]", "2")["backend"] == (
        "stdlib_linalg_rank"
    )


def test_result_model_needs_no_new_field() -> None:
    # The gate works on artifacts already on disk: it re-derives from
    # (kind, expression, expected), all of which the schema already carries.
    fields = set(ComputationVerificationResult.model_fields)
    assert {"kind", "expression", "expected", "computed", "backend"} <= fields
    assert "adapter_id" not in fields
