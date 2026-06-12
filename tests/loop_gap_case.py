"""Shared planted-gap dogfood case for the Goal 42 loop tests.

The order-6 false-theorem gap was fixed by the deterministic dihedral probe
(cycle 1 autofix), so tests that pin the loop's DETECTION logic (reviewer P1,
triage priority, unfixed-baseline replay, stop-gate open-findings flag) plant
the gap with a false case that is still genuinely beyond the probe's scope:
order 21 is odd (dihedral groups have even order, so the probe is provably
silent), yet Z_7 x| Z_3 is non-abelian, so the claim is false and the smoke
pipeline still yields zero counterexample tests for it.

Kept in ONE place so the three consuming test files cannot drift.
"""

from __future__ import annotations

from espresso_proof.loop_harness import DEFAULT_CASES

ORDER21_GAP_CASE = {
    "case_id": "order21_abelian_false",
    "claim": "Every group of order 21 is abelian.",
    "case_family": "false_theorem",
    "expected_difficulty": "smoke",
    "known_truth_status": "evaluator_only_false",
    "what_this_case_tests": ["counterexample reasoning beyond the dihedral probe"],
    "must_not_claim": [
        "Espresso proved the theorem",
        "refuted without a checked counterexample",
    ],
}


def _default_case(case_id: str) -> dict:
    # copy: never hand consumers live references into module-global state.
    return dict(next(c for c in DEFAULT_CASES if c["case_id"] == case_id))


def gap_cases() -> dict:
    """A 3-case smoke set mirroring the original first-3 composition
    (true theorem, false theorem with a live gap, missing hypothesis)."""

    return {
        "cases": [
            _default_case("p2_groups_true"),
            ORDER21_GAP_CASE,
            _default_case("continuous_max_missing_hypothesis"),
        ]
    }
