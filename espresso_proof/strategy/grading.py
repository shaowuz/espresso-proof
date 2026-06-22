"""Strategy Grader service boundary.

The grader turns raw candidate routes into kernel-shaped, conservative route
objects. It remains pure: no artifact writes and no time allocation side effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from espresso_proof.agents.llm_agents import safe_text
from espresso_proof.kernel.strategy_rules import (
    HIGH_SCORE_THRESHOLD,
    STANDARD_SCORE_LIFT_LIMIT,
    collect_strategy_rule_violations,
)

STRATEGY_GRADER_STAGE_ID: Literal["strategy_grader"] = "strategy_grader"
_EXPLORATION_MODES = {"quick_test", "normal_pass", "deep_exploration", "long_watch"}
_IDEA_STATUSES = {"promising", "fragile", "likely_false", "refuted", "unclear"}


@dataclass(frozen=True)
class StrategyGradingResult:
    """Conservative route grading result from the Strategy Grader stage."""

    routes: list[dict]
    idea_status: str
    trigger_stage: Literal["strategy_grader"] = STRATEGY_GRADER_STAGE_ID


def _safe_lock_route(idea_grade: dict) -> dict:
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


def normalize_strategy_routes(raw_routes: object, *, idea_grade: dict) -> list[dict]:
    """Normalize model-supplied route candidates into StrategyBriefItem dicts."""

    idea_score = int(idea_grade.get("score", 0) or 0)
    score_cap = min(HIGH_SCORE_THRESHOLD - 1, idea_score + STANDARD_SCORE_LIFT_LIMIT)
    routes: list[dict] = []
    if not isinstance(raw_routes, list):
        return routes
    for index, raw in enumerate(raw_routes, start=1):
        if not isinstance(raw, dict):
            continue
        title = safe_text(raw.get("title", "")).strip()
        if not title or title == "No usable agent text returned.":
            continue
        effort = raw.get("estimated_effort")
        # Deep exploration would trigger unchecked-evidence checks; keep this
        # first pass modest and scheduling-only.
        if effort not in _EXPLORATION_MODES or effort in {"deep_exploration", "long_watch"}:
            effort = "normal_pass"
        routes.append(
            {
                "id": f"strategy_route_{index:03d}",
                "title": title,
                "score": max(0, min(score_cap, 50)),
                "status": "fragile",
                "why_this_route": safe_text(raw.get("why_this_route", "")) or "candidate route",
                "survived_tests": [],
                "failed_tests": [],
                "main_risk": safe_text(raw.get("main_risk", "")) or "route may depend on unproven steps",
                "source_status": "candidate",
                "estimated_effort": effort,
                "next_loop_plan": safe_text(raw.get("next_loop_plan", "")) or "pressure-test the route",
            }
        )
        if len(routes) >= 3:
            break
    return routes


def grade_strategy_candidates(*, raw: dict, context: dict) -> StrategyGradingResult:
    """Build conservative graded routes from generator output and run context."""

    idea_grade = context.get("idea_grade") or {"status": "unclear", "score": 0}
    idea_status = str(idea_grade.get("status", "unclear"))
    if idea_status == "unclear":
        routes = [_safe_lock_route(idea_grade)]
    else:
        routes = normalize_strategy_routes(raw.get("routes"), idea_grade=idea_grade)
        if not routes or collect_strategy_rule_violations(
            routes,
            idea_grade,
            review=context.get("review"),
            computations=context.get("computation_checks"),
            counterexamples=context.get("counterexamples"),
            tests=context.get("tests"),
        ):
            routes = [_default_investigation_route(idea_grade)]
    return StrategyGradingResult(routes=routes, idea_status=idea_status)


__all__ = [
    "STRATEGY_GRADER_STAGE_ID",
    "StrategyGradingResult",
    "grade_strategy_candidates",
    "normalize_strategy_routes",
]
