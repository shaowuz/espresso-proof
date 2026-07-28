"""Simple-claim instant draft + earned-status label filter.

The order-3 run left the user on a dead page for a long time. The instant lane
 Must give a simple recognized claim a fast advisory draft / explicit
state, and that draft must not assert an earned-status verdict
(source-backed / checked / refuted) — which ``scrub_proof_language`` does NOT
catch. Reuses the existing lane; adds the verdict filter.
"""

from __future__ import annotations

import time

import pytest

from espresso_proof.ui.instant_lane import VISIBLE_LABEL, build_instant_draft
from espresso_proof.kernel.proof_language import contains_draft_status_claim, scrub_draft_status_claims

ORDER3 = "prove that a group of order 3 is cyclic"


class _FastClient:
    provider = "stub"
    model = "fast-test-model"

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def complete_json(self, *, system: str, prompt: str) -> dict:
        return self._payload


class _SlowClient:
    provider = "external"
    model = "slow-test-model"

    def complete_json(self, *, system: str, prompt: str) -> dict:
        time.sleep(5.0)
        return {"answer": "too late", "routes": [], "caveats": []}


# -- fast visible state for a simple claim (the order-3 regression) -----------

def test_order3_gets_fast_labeled_draft_without_client() -> None:
    t0 = time.monotonic()
    draft = build_instant_draft(ORDER3, created_at="t0")
    assert time.monotonic() - t0 < 1.0  # fast, never blocks
    assert draft.status in ("fallback", "draft")
    assert draft.visible_label == VISIBLE_LABEL
    assert draft.advisory_only is True
    assert draft.writes_kernel_status is False
    assert draft.answer.strip()  # a useful visible state, not a dead page


def test_order3_slow_model_falls_back_within_budget() -> None:
    t0 = time.monotonic()
    draft = build_instant_draft(ORDER3, client=_SlowClient(), budget_seconds=0.5, created_at="t0")
    assert time.monotonic() - t0 < 3.0  # abandoned the slow model, fell back fast
    assert draft.status in ("unavailable", "fallback")
    assert draft.answer.strip()


# -- earned-status verdict filter (the safety gap scrub_proof_language misses) -

def test_model_draft_verdict_labels_are_scrubbed() -> None:
    draft = build_instant_draft(
        ORDER3,
        client=_FastClient(
            {
                "answer": "This statement is checked and source-backed; it is refuted.",
                "routes": ["The route is verified by the standard argument."],
                "caveats": ["The lemma was proven earlier."],
            }
        ),
        created_at="t0",
    )
    blob = " ".join([draft.answer, *draft.routes_preview, *draft.caveats]).lower()
    for forbidden in ("source-backed", "source backed", "refuted", "is checked", "is verified", "was proven"):
        assert forbidden not in blob
    assert draft.advisory_only is True


# -- proof_language helpers: verdict forms vs advisory phrasing ---------------

@pytest.mark.parametrize("text", [
    "this is source-backed",
    "the claim is refuted",
    "it is checked",
    "the lemma was proven",
    "the route is now verified",
])
def test_contains_draft_status_claim_catches_verdicts(text: str) -> None:
    assert contains_draft_status_claim(text) is not None


@pytest.mark.parametrize("text", [
    "this still needs to be checked",
    "the source needs a check",
    "we should verify this later",
    "no counterexample has been found yet",
])
def test_contains_draft_status_claim_spares_advisory(text: str) -> None:
    assert contains_draft_status_claim(text) is None


def test_scrub_draft_status_claims_neutralizes_verdicts_only() -> None:
    assert "[unverified]" in scrub_draft_status_claims("the claim is checked")
    # advisory phrasing is preserved verbatim
    assert scrub_draft_status_claims("needs to be checked") == "needs to be checked"
