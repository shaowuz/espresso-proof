"""A checked status must survive the kernel re-deriving it, not just claiming it.

Found by a hostile pre-publication review and reproduced twice before the fix:
`_validate_proof_verification` granted `instance_checked` / `computation_checked`
by reading the `trusted` and `confirmed` booleans out of the artifact. Those are
written by whoever wrote the file, so a hand-authored `proof_verification.yaml`
earned a checked status with no machine work at all — one forged entry whose
target read "a group that was never built" passed `validate_run`, and a separate
report claiming `2+2 == 5` was "confirmed" passed as well.

The rule now: an entry claiming a checked status must name an `adapter_id` the
kernel can recompute, and the kernel re-runs it and requires its own result to
confirm. See kernel/recompute.py.
"""

from __future__ import annotations

import pytest

from espresso_proof.kernel.recompute import is_recomputable, recompute_result
from espresso_proof.verification.proof_verification import (
    NAMED_FINITE_WITNESSES,
    build_permutation_group_entry,
)


# --- the attack: a declaration is not evidence


def _forged(**over) -> dict:
    entry = {
        "id": "forged",
        "verifier_type": "finite_group_cayley",
        "claim_id": "claim_001",
        "target": "a group that was never built",
        "input_summary": "nothing was ever executed",
        "result": "fabricated confirming result",
        "trusted": True,
        "confirmed": True,
        "earned_status": "instance_checked",
        "scope_note": "forged by hand",
        "input_hash": "0" * 64,
        "output_hash": "0" * 64,
        "adapter_id": "none",
        "recompute_input": {},
    }
    entry.update(over)
    return entry


def test_unknown_adapter_cannot_be_recomputed() -> None:
    # The forged entry's own adapter_id: nothing can re-derive it.
    assert not is_recomputable("none")
    assert recompute_result("none", {}) is None


def test_borrowing_a_real_adapter_name_does_not_help() -> None:
    # A forger may claim a real adapter, but then the kernel actually runs it —
    # and a witness that regenerates its own data cannot produce the forged hash.
    entry = _forged(adapter_id="gl2_f3_solvability_witness")
    recomputed = recompute_result(entry["adapter_id"], entry["recompute_input"])
    assert recomputed is not None
    assert recomputed["transcript_sha256"] != entry["output_hash"]


def test_recompute_refuses_a_false_arithmetic_claim() -> None:
    # The second reproduced attack: "2+2 confirmed as 5".
    assert recompute_result("exact_modexp", {"base": 2, "exp": 1, "mod": 100, "expected": 5})[
        "confirmed"
    ] is False
    assert recompute_result("exact_modexp", {"base": 2, "exp": 10, "mod": 1000, "expected": 24})[
        "confirmed"
    ]


def test_recompute_fails_closed_on_junk_input() -> None:
    for spec in ({}, {"base": "2", "exp": 1, "mod": 3, "expected": 2}, {"base": 2, "exp": -1, "mod": 3, "expected": 1}):
        result = recompute_result("exact_modexp", spec)
        assert result is not None and result["confirmed"] is False


def test_a_crashing_adapter_is_not_a_confirmation() -> None:
    from espresso_proof.kernel.recompute import register_recomputer

    def _boom(_spec: dict) -> dict:
        raise RuntimeError("verifier exploded")

    register_recomputer("test_only_crashing_adapter", _boom)
    assert recompute_result("test_only_crashing_adapter", {}) is None


def test_a_non_dict_adapter_return_is_not_a_confirmation() -> None:
    from espresso_proof.kernel.recompute import register_recomputer

    register_recomputer("test_only_bad_return", lambda _spec: "confirmed")  # type: ignore[arg-type]
    assert recompute_result("test_only_bad_return", {}) is None


# --- the legitimate path must still work, or the fix is useless


@pytest.mark.parametrize("name", sorted(NAMED_FINITE_WITNESSES))
def test_every_named_witness_is_recomputable_and_matches(name: str) -> None:
    entry, _ = NAMED_FINITE_WITNESSES[name]("claim_001", entry_id=name)
    assert is_recomputable(entry["adapter_id"]), entry["adapter_id"]
    recomputed = recompute_result(entry["adapter_id"], entry.get("recompute_input"))
    assert recomputed is not None
    assert recomputed["confirmed"] is True
    assert recomputed["transcript_sha256"] == entry["output_hash"]


def test_permutation_entry_recomputes_from_its_recorded_input() -> None:
    entry, _ = build_permutation_group_entry(
        "claim_001",
        entry_id="p",
        generators=[[1, 2, 3, 4, 0], [1, 2, 0, 3, 4]],  # A_5
        expected_order=60,
        expected_solvable=False,
    )
    assert entry["earned_status"] == "instance_checked"
    recomputed = recompute_result(entry["adapter_id"], entry["recompute_input"])
    assert recomputed["confirmed"] is True
    assert recomputed["transcript_sha256"] == entry["output_hash"]


def test_tampering_with_recorded_input_breaks_the_match() -> None:
    entry, _ = build_permutation_group_entry(
        "claim_001", entry_id="p",
        generators=[[1, 2, 3, 4, 0], [1, 2, 0, 3, 4]],
        expected_order=60, expected_solvable=False,
    )
    tampered = {**entry["recompute_input"], "expected_order": 61}
    recomputed = recompute_result(entry["adapter_id"], tampered)
    assert recomputed["confirmed"] is False


# --- round two: hash agreement alone must not earn a status


def test_a_public_hash_cannot_be_borrowed_for_an_arbitrary_target() -> None:
    """The round-one gate's own bypass, found in round two.

    Two shipped witnesses take no input and are fully deterministic, so their
    transcript hash is public: anyone can compute it and write an entry carrying
    that hash with a target sentence of their choosing. Hash agreement was
    therefore not enough — the kernel also has to require that the recorded
    target is what the adapter says it checked.
    """

    result = recompute_result("gl2_f3_solvability_witness", {})
    assert result["confirmed"] is True  # the witness itself is honest
    canonical = result["canonical_target"]
    assert canonical and canonical.startswith("solvability of")
    # A forged target does not start with what the adapter reports, which is
    # exactly what the validator now rejects.
    assert not "Sz(8) is a Galois group over Q".startswith(canonical)


def test_every_registered_adapter_reports_what_it_checked() -> None:
    from espresso_proof.kernel.recompute import _RECOMPUTERS, _ensure_installed

    _ensure_installed()
    inputs = {
        "permutation_group_witness": {"generators": [[1, 0, 2], [1, 2, 0]], "expected_order": 6},
        "verify_identity_on_table": {"table": [[0, 1], [1, 0]], "identity": "commutative"},
        "verify_ring_identity_mod": {"identity": "commutative", "modulus": 3},
        "verify_matrix_rank": {"matrix": [[1, 0], [0, 1]], "expected_rank": 2},
        "verify_group_abelian": {"table": [[0, 1], [1, 0]]},
        "exact_modexp": {"base": 2, "exp": 10, "mod": 1000, "expected": 24},
        "tensor_left_exact_counterexample": {"modulus": 2, "multiplier": 2, "witness": 1},
    }
    for adapter in _RECOMPUTERS:
        if adapter.startswith("test_only_"):
            continue
        out = recompute_result(adapter, inputs.get(adapter, {}))
        assert out is not None, adapter
        # An adapter that reports no canonical target cannot bind a target, so
        # the validator's target check would be vacuous for it. Record which
        # ones are in that state rather than pretending they are covered.
        assert "canonical_target" in out, adapter


def test_a_vacuous_group_cannot_earn_a_checked_status() -> None:
    from espresso_proof.verification.proof_verification import build_permutation_group_entry

    # The trivial group satisfies "order 1" and "solvable" regardless of what the
    # artifact claims to be about, so confirming it would trade one identity
    # element for an instance_checked.
    for gens, order in (([[0]], 1), ([[1, 0]], 2)):
        entry, _ = build_permutation_group_entry(
            "claim_001", entry_id="v", generators=gens,
            expected_order=order, expected_solvable=True,
        )
        assert entry["earned_status"] == "unverified", gens


# --- round three: the rule must be the SAME rule everywhere, and prefix-only
# binding was not binding


def test_arbitrary_mathematics_cannot_ride_behind_a_checked_prefix() -> None:
    """The round-two binding's own bypass.

    The target check was `entry.target.startswith(canonical)`, which pins a
    prefix and leaves the rest free. A true 4-element table check could therefore
    be published as "...table of order 4 - hence every group of order p^2 is
    abelian for every prime p" and still earn instance_checked. Only the
    machine-authored wording plus one labelled "(requested for: ...)" note is
    allowed now.
    """

    from espresso_proof.kernel.recompute import checked_entry_rejection_reason, recompute_result

    canonical = recompute_result("verify_group_abelian", {"table": [[0, 1], [1, 0]]})[
        "canonical_target"
    ]
    entry = {
        "id": "e",
        "trusted": True,
        "confirmed": True,
        "adapter_id": "verify_group_abelian",
        "recompute_input": {"table": [[0, 1], [1, 0]]},
        "target": canonical + " - hence every group of order p^2 is abelian for every prime p",
        "output_hash": "",
    }
    reason = checked_entry_rejection_reason(entry)
    assert reason is not None and "ride along" in reason

    # The machine-authored target alone, and the labelled note, both stay legal.
    assert checked_entry_rejection_reason({**entry, "target": canonical}) is None
    assert (
        checked_entry_rejection_reason(
            {**entry, "target": canonical + " (requested for: some claim)"}
        )
        is None
    )


def test_the_note_cannot_be_closed_early_to_append_content() -> None:
    from espresso_proof.kernel.recompute import checked_entry_rejection_reason, recompute_result
    from espresso_proof.verification.proof_verification import requested_for_note

    canonical = recompute_result("verify_group_abelian", {"table": [[0, 1], [1, 0]]})[
        "canonical_target"
    ]
    base = {
        "id": "e", "trusted": True, "confirmed": True,
        "adapter_id": "verify_group_abelian",
        "recompute_input": {"table": [[0, 1], [1, 0]]},
        "output_hash": "",
    }
    escaped = canonical + " (requested for: x) and therefore all primes p"
    assert checked_entry_rejection_reason({**base, "target": escaped}) is not None
    # The builder's note strips the parens that would allow it.
    note = requested_for_note("x) and therefore all primes p")
    assert note.count("(") == 1 and note.count(")") == 1 and note.endswith(")")
    assert checked_entry_rejection_reason({**base, "target": canonical + note}) is None


def test_every_shipped_builder_survives_its_own_binding_rule() -> None:
    # A builder whose target does not satisfy the rule would break the legitimate
    # path; this is the regression guard for that.
    from espresso_proof.kernel.recompute import checked_entry_rejection_reason
    from espresso_proof.verification import proof_verification as pv

    built = list(pv.build_finite_group_entries("claim_001", [2, 3]))
    built.append(pv.build_gl2_f3_solvability_entry("claim_001")[0])
    built.append(pv.build_gl2_f3_order_spectrum_entry("claim_001", entry_id="s")[0])
    built.append(
        pv.build_permutation_group_entry(
            "claim_001", entry_id="p",
            generators=[[1, 2, 3, 4, 0], [1, 2, 0, 3, 4]],
            expected_order=60, expected_solvable=False,
            target="Sz(8) is a Galois group over Q",
        )[0]
    )
    built.append(
        pv.build_identity_table_entry(
            "claim_001", entry_id="t", table=[[0, 1], [1, 0]],
            identity="commutative", target="every group of order p^2 is abelian (all p)",
        )
    )
    built.append(
        pv.build_matrix_rank_entry(
            "claim_001", entry_id="m", matrix=[[1, 0], [0, 1]], expected_rank=2,
            target="rank claim",
        )
    )
    for entry in built:
        if entry.get("earned_status") == "instance_checked":
            assert checked_entry_rejection_reason(entry) is None, entry["id"]


def test_a_consumer_of_checked_evidence_uses_the_kernel_rule() -> None:
    """`_split_machine_answered_blockers` read the file by PATH and trusted its booleans.

    That made an UNREGISTERED forged proof_verification.yaml enough to silence
    reviewer blockers: unregistered means the validator's recompute gate never
    looked at it. The consumer now asks the kernel the same question.
    """

    import inspect

    from espresso_proof.agents import agent_pass

    source = inspect.getsource(agent_pass._split_machine_answered_blockers)
    assert "checked_entry_rejection_reason" in source
    # The old predicate must be gone, not merely supplemented.
    assert 'e.get("trusted") and e.get("confirmed")' not in source


def test_the_forged_entry_is_rejected_by_the_shared_rule_too() -> None:
    from espresso_proof.kernel.recompute import checked_entry_rejection_reason

    assert checked_entry_rejection_reason(_forged()) is not None
    assert checked_entry_rejection_reason(_forged(adapter_id="gl2_f3_solvability_witness")) is not None
    assert checked_entry_rejection_reason({}) is not None
    assert checked_entry_rejection_reason(None) is not None
