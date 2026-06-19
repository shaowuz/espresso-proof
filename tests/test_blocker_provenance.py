"""Phase 3: the shared blocker-provenance module (extracted from loop_harness).

Locks the contract of the single source of truth that BOTH the loop harness and
the loop replay gate now use. Behavior is identical to the pre-extraction logic.
"""
from __future__ import annotations

from espresso_proof.loop.blocker_provenance import (
    classify_blocker,
    provenance_is_human_gated,
)


def test_provenance_is_human_gated_predicate() -> None:
    # has provenance and did NOT originate as a source request -> human-gated
    assert provenance_is_human_gated({"objection"}) is True
    assert provenance_is_human_gated({"missing_hypothesis"}) is True
    assert provenance_is_human_gated({"objection", "missing_hypothesis"}) is True
    # a source request (even mixed in) is NOT human-gated (a bounded-fix target)
    assert provenance_is_human_gated({"source_request"}) is False
    assert provenance_is_human_gated({"source_request", "objection"}) is False
    # no recorded provenance -> not human-gated (legacy keyword rules apply)
    assert provenance_is_human_gated(set()) is False


def test_classify_blocker_provenance_first() -> None:
    # provenance decides before keywords
    assert classify_blocker("anything", provenance={"missing_hypothesis"}) == "human_judgment_needed"
    assert classify_blocker("anything", provenance={"objection"}) == "reviewer_objection"
    # a source request falls through to the keyword rules on its text
    assert classify_blocker("need exact locator", provenance={"source_request"}) == "source_gap"


def test_classify_blocker_keyword_fallback() -> None:
    # no provenance -> keyword rules
    assert classify_blocker("need a citation / reference") == "source_gap"
    assert classify_blocker("exact theorem statement required") == "source_gap"
    assert classify_blocker("missing definition of admissible") == "missing_definition"
    assert classify_blocker("this computation needs a check") == "missing_computation"
    assert classify_blocker("needs human judgment") == "human_judgment_needed"
    assert classify_blocker("step 3 does not follow") == "failed_proof_step"


def test_classify_blocker_theorem_is_phrase_scoped() -> None:
    # a proof problem mentioning "theorem" is NOT a source gap unless it is a
    # locator/citation request phrase
    assert classify_blocker("the theorem's step 3 is invalid") == "failed_proof_step"
