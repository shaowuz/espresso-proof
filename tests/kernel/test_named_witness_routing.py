"""A reviewer blocker that names a decidable property must reach the verifier.

The gap this covers, observed on a real run: a reviewer asked for "an accepted
finite-group derivation or exact source establishing solvability of GL_2(F_3)",
a deterministic witness for exactly that already existed, and nothing connected
the two — so the run stopped and asked a human for something the machine could
settle.
"""

from __future__ import annotations

import pytest

from espresso_proof.kernel.models import ProofVerificationEntry, ScopedVerificationRequests
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


# --- second witness: the registry had to take an entry without a dispatcher change


def test_registry_holds_both_witnesses() -> None:
    assert set(NAMED_FINITE_WITNESSES) >= {"gl2_f3_solvable", "gl2_f3_distinct_from_s4xc2"}


def test_probe_proposes_both_for_a_gl2_f3_claim() -> None:
    reqs = propose_named_witness_requests(
        {"normalized_statement": "GL_2(F_3) is a Galois group over Q."}
    )
    assert {r["witness"] for r in reqs} == {"gl2_f3_solvable", "gl2_f3_distinct_from_s4xc2"}
    assert len({r["id"] for r in reqs}) == len(reqs)  # ids must not collide


def test_order_spectrum_separates_the_two_order_48_groups() -> None:
    from espresso_proof.verification.finite_group_verifier import (
        gl2_f3_order_spectrum_witness,
    )

    w = gl2_f3_order_spectrum_witness()
    assert w["order"] == 48 and w["rival_order"] == 48
    # The decisive fact: GL_2(F_3) has a nonsplit torus, S_4 x C_2 has no order-8
    # element at all.
    assert w["order_spectrum"]["8"] == 12
    assert "8" not in w["rival_order_spectrum"]
    assert w["confirmed"] is True


def test_separation_earns_nothing_about_galois() -> None:
    entry, _ = NAMED_FINITE_WITNESSES["gl2_f3_distinct_from_s4xc2"]("claim_001", entry_id="e")
    assert entry["earned_status"] == "instance_checked"
    blob = (entry["target"] + entry["result"] + entry["scope_note"]).lower()
    assert "galois" in entry["scope_note"].lower()  # explicitly disclaims Galois content
    for forbidden in ("proved", "proof complete", "[proof-close]"):
        assert forbidden not in blob


def test_every_registered_witness_builds_a_schema_valid_entry() -> None:
    """The gap that let a broken builder ship.

    The earlier tests called each builder and asserted on a few keys they
    happened to know about, so a builder inventing its own field names passed
    them and then failed the kernel at run time with eight validation errors.
    Validating against ProofVerificationEntry is the check that actually
    corresponds to what a run does.
    """

    assert NAMED_FINITE_WITNESSES, "registry must not be empty"
    for name, builder in NAMED_FINITE_WITNESSES.items():
        entry, _ = builder("claim_001", entry_id=name)
        ProofVerificationEntry.model_validate(entry)
        assert entry["earned_status"] in {"instance_checked", "unverified"}, name
        assert entry["scope_note"], name
