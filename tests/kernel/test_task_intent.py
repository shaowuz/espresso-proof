from __future__ import annotations

from espresso_proof.claim.task_intent import infer_task_intent, is_witness_intent


def test_find_counterexample_intent_is_witness_task() -> None:
    intent = infer_task_intent(
        {
            "raw_text": "Find a counterexample that tensor product is left exact.",
            "normalized_statement": (
                "Exhibit abelian groups and a short exact sequence such that "
                "tensoring is not exact at the left end."
            ),
        }
    )

    assert intent == "find_counterexample"
    assert is_witness_intent(intent) is True


def test_plain_prove_intent_stays_prove_task() -> None:
    assert (
        infer_task_intent({"raw_text": "Prove every group of prime order is cyclic."})
        == "prove"
    )


def test_prove_no_counterexample_is_not_witness_search() -> None:
    intent = infer_task_intent(
        {"raw_text": "Prove there is no counterexample to left exactness here."}
    )

    assert intent == "prove"
    assert is_witness_intent(intent) is False
