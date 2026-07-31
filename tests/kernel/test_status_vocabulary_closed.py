"""The trusted-status vocabulary is CLOSED and every member has an explicit rule.

Two fall-through shapes are pinned shut here:

1. Enum-closure pins: the trust-bearing enums and provenance Literals are
   asserted by exact set equality. Adding a member is deliberately loud — the
   author must update these pins AND add an explicit fail-closed validator
   branch for the new member in the same change.
2. Default-deny backstop: a proof-language status the kernel validator has no
   backing rule for must be REJECTED, not silently treated as needing no
   backing (the pre-fix behavior for any new enum member).

Plus the human_attested rule: with no human-attestation artifact in V0, a
proof_verification entry can never earn it (it would be unverifiable say-so).
"""

from __future__ import annotations

from typing import get_args

import pytest

from espresso_proof.kernel.models import (
    Claim,
    HumanChoice,
    IdeaGrade,
    LLMAgentHumanGate,
    ProofLanguageStatus,
    ProofVerificationEntry,
    ProofVerificationReport,
    ProofVerificationStatus,
    RunManifest,
    SourceGroundingReport,
    VerifierType,
)
from espresso_proof.kernel.validators import KernelValidationError
from espresso_proof.kernel.validators.final_gates import (
    _validate_grade_proof_language_backed,
)
from espresso_proof.kernel.validators.machine_evidence_gates import (
    _validate_proof_verification,
)

# ---------------------------------------------------------------------------
# Enum / Literal closure pins (exact set equality, not membership)
# ---------------------------------------------------------------------------


def test_proof_language_status_member_set_is_pinned() -> None:
    assert set(ProofLanguageStatus.__members__) == {
        "idea_sketch",
        "proof_candidate",
        "agent_consensus_mock_checked",
        "source_checked_argument",
        "computation_checked",
        "lean_checked",
        "human_confirmed",
    }, (
        "ProofLanguageStatus changed. A new member MUST get an explicit "
        "fail-closed branch in _validate_grade_proof_language_backed before "
        "this pin is updated."
    )


def test_proof_verification_status_member_set_is_pinned() -> None:
    assert set(ProofVerificationStatus.__members__) == {
        "instance_checked",
        "computation_checked",
        "lean_checked",
        "human_attested",
        "unverified",
    }, (
        "ProofVerificationStatus changed. A new member MUST get an explicit "
        "earn/reject rule in _validate_proof_verification before this pin is "
        "updated."
    )


def test_verifier_type_member_set_is_pinned() -> None:
    assert set(VerifierType.__members__) == {
        "finite_group_cayley",
        "finite_identity_table",
        "finite_ring_identity",
        "matrix_rank",
        "exact_computation",
        "lean_bridge",
        "human_checked",
    }, (
        "VerifierType changed. A new backend MUST get an explicit trusted-earn "
        "rule (or rejection) in _validate_proof_verification / _TRUSTED_EARNED "
        "before this pin is updated."
    )


def test_choice_and_gate_provenance_literals_are_pinned() -> None:
    assert set(get_args(HumanChoice.model_fields["selected_by"].annotation)) == {
        "human",
        "auto",
    }
    assert set(get_args(LLMAgentHumanGate.model_fields["resolved_by"].annotation)) == {
        "human",
        "auto",
    }, (
        "Provenance vocabulary changed. The kernel checks are exhaustive "
        "(only 'human' may claim human_confirmed), but a new value still needs "
        "its own writer rules — update the pins deliberately."
    )


# ---------------------------------------------------------------------------
# Default-deny backstop for unknown proof-language statuses
# ---------------------------------------------------------------------------


def test_unknown_proof_language_status_is_rejected_not_ignored() -> None:
    # Simulates the exact hole: a future enum member ('machine_attested') that
    # nobody wrote a backing rule for. It must fail closed, not validate green.
    forged_grade = IdeaGrade.model_construct(proof_language_status="machine_attested")
    with pytest.raises(KernelValidationError, match="no backing rule"):
        _validate_grade_proof_language_backed(
            forged_grade,
            SourceGroundingReport.model_construct(steps=[]),
            None,
            None,
            None,
        )


def test_weak_statuses_still_validate_without_backing() -> None:
    for weak in (ProofLanguageStatus.idea_sketch, ProofLanguageStatus.proof_candidate):
        _validate_grade_proof_language_backed(
            IdeaGrade.model_construct(proof_language_status=weak),
            SourceGroundingReport.model_construct(steps=[]),
            None,
            None,
            None,
        )


# ---------------------------------------------------------------------------
# human_attested is not earnable in V0
# ---------------------------------------------------------------------------


def _human_attested_report() -> ProofVerificationReport:
    entry = ProofVerificationEntry.model_construct(
        id="pv_human",
        verifier_type=VerifierType.human_checked,
        claim_id="claim_test",
        target="the whole claim",
        input_summary="",
        result="a human says it is fine",
        trusted=False,
        confirmed=True,
        earned_status=ProofVerificationStatus.human_attested,
        scope_note="unverifiable say-so",
    )
    return ProofVerificationReport.model_construct(
        claim_id="claim_test",
        run_id="run_test",
        generated_at="2026-01-01T00:00:00+00:00",
        entries=[entry],
        notes=[],
    )


def test_human_attested_entry_is_rejected_fail_closed() -> None:
    claim = Claim.model_construct(id="claim_test")
    manifest = RunManifest.model_construct(
        run_id="run_test", artifacts={}, optional_artifacts={}
    )
    with pytest.raises(KernelValidationError, match="human_attested is not earnable"):
        _validate_proof_verification(
            _human_attested_report(),
            claim,
            manifest,
            IdeaGrade.model_construct(
                proof_language_status=ProofLanguageStatus.idea_sketch
            ),
        )
