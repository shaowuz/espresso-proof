"""Shared front-half safety surface (Phase 04 physical split).

Status vocabularies + the conservative guards/helpers shared by more than one
stage. Imported by stages, never copied; this is a leaf module (it must not
import any stage module).
"""

from __future__ import annotations

from ..llm_agents import safe_text
from ..models import Review

# Valid hypothesis statuses (kernel enum). A model-supplied status outside this
# set is coerced to "unclear" — never trusted verbatim.
_HYPOTHESIS_STATUSES = {"explicit", "inferred", "missing", "unclear"}

# Literature Review may ONLY propose candidates. Any model-supplied source
# status outside this set (notably source_backed / misapplied) is downgraded to
# "candidate" — only Source Grounding (06) may set source_backed.
_CANDIDATE_SOURCE_STATUSES = {"candidate", "unverified", "not_found"}
_LOCATOR_KINDS = {
    "theorem", "proposition", "lemma", "corollary", "definition",
    "section", "page", "arxiv_id", "url", "unknown",
}
_LITERATURE_RELEVANCE = {
    "core", "background", "example_source",
    "counterexample_source", "possible_theorem_source",
}
_NONTRIVIALITY = {"routine", "standard", "literature_counterexample", "research_level"}

_IDEA_STATUSES = {"promising", "fragile", "likely_false", "refuted", "unclear"}
_EXPLORATION_MODES = {"quick_test", "normal_pass", "deep_exploration", "long_watch"}


def _normalize_locator(raw: object) -> dict:
    if isinstance(raw, dict):
        kind = raw.get("kind")
        value = safe_text(raw.get("value", "")).strip()
        if kind not in _LOCATOR_KINDS:
            kind = "unknown"
        if not value:
            kind, value = "unknown", "not specified"
        return {"kind": kind, "value": value}
    return {"kind": "unknown", "value": "not specified"}



def _coerce_satisfies(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "yes"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "no"}:
        return False
    return "unclear"



def _safe_lock_route(idea_grade: dict) -> dict:
    """A route that is valid under the strategy rules for ANY idea grade.

    Score is capped well below the high-score threshold and never lifts above the
    idea grade, the effort is cheap, and the id is the canonical claim-lock route
    (the only route an unclear claim may recommend).
    """

    idea_score = int(idea_grade.get("score", 0) or 0)
    idea_status = idea_grade.get("status", "unclear")
    if idea_status not in _IDEA_STATUSES:
        idea_status = "unclear"
    score = max(0, min(idea_score, 30))
    return {
        "id": "strategy_lock_claim",
        "title": "Lock and clarify the claim before deeper strategy work",
        "score": score,
        "status": idea_status,
        "why_this_route": "The claim must be precise before strategies can be graded.",
        "survived_tests": [],
        "failed_tests": [],
        "main_risk": "Proceeding without a locked claim creates false progress.",
        "source_status": "candidate",
        "estimated_effort": "quick_test",
        "next_loop_plan": "Lock the precise claim, then re-run strategy generation.",
    }



def _default_investigation_route(idea_grade: dict) -> dict:
    """A concrete, conservative next route for a precise-but-unproven claim.

    Used when the claim is precise enough to study but the agent supplied no
    usable route. It is candidate-only, scored well below the high-score
    threshold and within the standard lift, and claims no proof.
    """

    idea_score = int(idea_grade.get("score", 0) or 0)
    return {
        "id": "strategy_investigate_route",
        "title": "Investigate the leading route and pin its key lemma(s)",
        "score": max(0, min(idea_score, 40)),
        "status": "fragile",
        "why_this_route": (
            "The claim is precise enough to study; test the most plausible route "
            "and identify the lemmas and sources it depends on."
        ),
        "survived_tests": [],
        "failed_tests": [],
        "main_risk": "The route may depend on unproven lemmas or missing sources.",
        "source_status": "candidate",
        "estimated_effort": "normal_pass",
        "next_loop_plan": "Pin each needed lemma to an exact source and test small cases.",
    }



def _neutralize_review_if_failed(review: dict) -> dict | None:
    """For a precise claim, relax a placeholder 'failed' review to neutral.

    The front half has NOT run the real reviewers yet (those run in the
    Prover/Reviewers pass), so a scaffolded ``failed`` verdict is not an honest
    signal for a precise claim and would wrongly block meaningful routes. This
    sets the reviewers to ``unclear`` (pending real review) without asserting any
    pass/proof. Returns the new review dict, or None if nothing needed changing.
    """

    reviewers = review.get("reviewers", [])
    if not any(r.get("verdict") in {"fail", "failed"} for r in reviewers):
        return None
    new_reviewers = []
    for r in reviewers:
        nr = dict(r)
        nr["verdict"] = "unclear"
        nr["severity"] = "medium"
        nr["required_reruns"] = []
        new_reviewers.append(nr)
    new_review = dict(review)
    new_review["reviewers"] = new_reviewers
    new_review["attempt_status"] = "unclear"
    new_review["verdict"] = "unclear"
    note = "Front-half review pending; real reviewers run in the Prover/Reviewers pass."
    new_review["downgrade_reasons"] = list(review.get("downgrade_reasons") or []) + [note]
    Review.model_validate(new_review)
    return new_review


