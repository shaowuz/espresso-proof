"""A reviewer blocker that names a decidable property must reach the verifier.

The gap this covers, observed on a real run: a reviewer asked for "an accepted
finite-group derivation or exact source establishing solvability of GL_2(F_3)",
a deterministic witness for exactly that already existed, and nothing connected
the two — so the run stopped and asked a human for something the machine could
settle.
"""

from __future__ import annotations

import pytest

from espresso_proof.kernel.models import ScopedVerificationRequests
from espresso_proof.verification.proof_verification import NAMED_FINITE_WITNESSES
from espresso_proof.verification.verification_request_probes import (
    propose_named_witness_requests,
)


def test_probe_fires_on_a_claim_naming_the_group() -> None:
    for statement in (
        "There exists a finite Galois extension K/Q with Gal(K/Q) isomorphic to GL_2(F_3).",
        "GL2(F3) is a Galois group over Q.",
        "Show that GL_2(\\mathbb{F}_3) occurs as a Galois group over the rationals.",
    ):
        assert propose_named_witness_requests({"normalized_statement": statement}), statement


def test_probe_fails_closed_on_unrelated_claims() -> None:
    for statement in (
        "Every finite group of order p^2, where p is prime, is abelian.",
        "Every continuous function on a closed interval is bounded.",
        "",
    ):
        assert propose_named_witness_requests({"normalized_statement": statement}) == []


def test_request_validates_against_the_kernel_schema() -> None:
    claim = {"id": "claim_001", "normalized_statement": "GL_2(F_3) is a Galois group over Q."}
    doc = {"claim_id": claim["id"], "requests": propose_named_witness_requests(claim)}
    ScopedVerificationRequests.model_validate(doc)


def test_request_carries_a_pointer_and_no_finite_data() -> None:
    # The whole point of the named form: the request cannot smuggle in a
    # fabricated table, because it contains none. The verifier regenerates it.
    req = propose_named_witness_requests(
        {"normalized_statement": "GL_2(F_3) is a Galois group over Q."}
    )[0]
    assert req["kind"] == "named_finite_witness"
    assert req["witness"] in NAMED_FINITE_WITNESSES
    assert req.get("table") is None
    assert "status" not in req and "earned" not in req


def test_named_witness_resolves_and_confirms() -> None:
    builder = NAMED_FINITE_WITNESSES["gl2_f3_solvable"]
    entry, detail = builder("claim_001", entry_id="gl2_f3_solvable")
    assert entry["verifier_type"] == "finite_group_cayley"
    # GL_2(F_3) -> SL_2(F_3) -> Q_8 -> {+-I} -> 1
    assert detail["derived_series_orders"] == [48, 24, 8, 2, 1]
    assert detail["is_solvable"] is True


def test_a_confirmed_witness_still_earns_no_proof_status() -> None:
    # Solvability of the group is a side condition, not the inverse Galois
    # claim. Deciding it must not move the claim toward "proved".
    _, detail = NAMED_FINITE_WITNESSES["gl2_f3_solvable"]("claim_001", entry_id="e")
    blob = repr(detail).lower()
    for forbidden in ("proved", "proof complete", "[proof-close]"):
        assert forbidden not in blob


def test_unknown_witness_name_is_rejected() -> None:
    assert "no_such_witness" not in NAMED_FINITE_WITNESSES
    with pytest.raises(KeyError):
        NAMED_FINITE_WITNESSES["no_such_witness"]
