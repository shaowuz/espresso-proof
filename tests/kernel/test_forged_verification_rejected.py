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
    assert recompute_result("exact_modexp", {"base": 2, "exp": 1, "mod": 100, "expected": 5}) == {
        "confirmed": False,
        "transcript_sha256": None,
    }
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
