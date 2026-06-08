"""Front-half stage: strategy (Phase 04 physical split)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ...hypothesis_normalization import normalize_ambient_object_hypotheses
from ...io import read_yaml, write_yaml
from ...llm_agents import LLMClient, load_agent_context, safe_list, safe_text
from ...memo import build_concise_memo
from ...memory import write_run_memory
from ...models import (
    Claim,
    ComputationChecks,
    CounterexampleReport,
    ExampleSelection,
    FrontHalfRunReport,
    HypothesisLedger,
    IdeaGrade,
    LiteratureReviewReport,
    Review,
    SourceGroundingReport,
    StrategyBrief,
    StrategySynthesis,
    TestReport,
)
from ...strategy_rules import (
    HIGH_SCORE_THRESHOLD,
    STANDARD_SCORE_LIFT_LIMIT,
    collect_strategy_rule_violations,
)
from ...validators import validate_run
from ..registry import register_stage
from ..safety import (
    _CANDIDATE_SOURCE_STATUSES,
    _EXPLORATION_MODES,
    _HYPOTHESIS_STATUSES,
    _IDEA_STATUSES,
    _LITERATURE_RELEVANCE,
    _LOCATOR_KINDS,
    _NONTRIVIALITY,
    _coerce_satisfies,
    _default_investigation_route,
    _neutralize_review_if_failed,
    _normalize_locator,
    _safe_lock_route,
)


_STRATEGY_SYSTEM = (
    "You are the Espresso Proof Strategy agent (generator + grader + brief). "
    "Propose CANDIDATE proof routes for the locked claim and grade them "
    "CONSERVATIVELY. Scores are candidate confidence, NOT proof status: a high "
    "score never means the claim is proved. Source gaps must be named. If the "
    "claim is still unclear, the only honest route is to lock the claim first. "
    "Respond with ONLY a JSON object."
)

_STRATEGY_OUTPUT_SCHEMA = {
    "routes": [
        {
            "title": "string",
            "why_this_route": "string",
            "main_risk": "string",
            "estimated_effort": "quick_test|normal_pass|deep_exploration|long_watch",
            "next_loop_plan": "string",
        }
    ],
    "question_for_mathematician": "string",
    "synthesis_note": "string",
}


def _normalize_strategy_routes(raw_routes: object, *, idea_grade: dict) -> list[dict]:
    idea_score = int(idea_grade.get("score", 0) or 0)
    idea_status = idea_grade.get("status", "unclear")
    # Cap scores below the high-score threshold and within the standard lift, so
    # the unchecked-evidence / high-score / idea-status rules can never trip.
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
        # deep_exploration would trigger unchecked-evidence checks; keep it modest.
        if effort not in _EXPLORATION_MODES or effort in {"deep_exploration", "long_watch"}:
            effort = "normal_pass"
        routes.append(
            {
                "id": f"strategy_route_{index:03d}",
                "title": title,
                "score": max(0, min(score_cap, 50)),
                # SAFETY: fragile is conservative; never promising from the front half.
                "status": "fragile",
                "why_this_route": safe_text(raw.get("why_this_route", "")) or "candidate route",
                "survived_tests": [],
                "failed_tests": [],
                "main_risk": safe_text(raw.get("main_risk", "")) or "route may depend on unproven steps",
                # SAFETY: front-half has done no source grounding -> candidate only.
                "source_status": "candidate",
                "estimated_effort": effort,
                "next_loop_plan": safe_text(raw.get("next_loop_plan", "")) or "pressure-test the route",
            }
        )
        if len(routes) >= 3:
            break
    return routes


@register_stage("strategy")
def _strategy(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    run_id = context["manifest"]["run_id"]
    claim = context["claim"]
    claim_id = claim["id"]
    idea_grade = context.get("idea_grade") or {"status": "unclear", "score": 0}
    idea_status = idea_grade.get("status", "unclear")

    payload = {
        "task": "generate_and_grade_strategies",
        "claim": {
            "normalized_statement": claim.get("normalized_statement", ""),
            "conclusion": claim.get("conclusion", ""),
        },
        "idea_status": idea_status,
        "required_json_schema": _STRATEGY_OUTPUT_SCHEMA,
        "rules": [
            "scores are candidate confidence, never proof status",
            "name source gaps; do not claim source_backed",
            "an unclear claim can only recommend locking the claim first",
        ],
    }
    raw = client.complete_json(
        system=_STRATEGY_SYSTEM, prompt=json.dumps(payload, indent=2)
    ) or {}

    # An unclear claim may ONLY recommend the claim-lock route (kernel rule).
    if idea_status == "unclear":
        routes = [_safe_lock_route(idea_grade)]
    else:
        routes = _normalize_strategy_routes(raw.get("routes"), idea_grade=idea_grade)
        # Pre-check against the kernel strategy rules; fall back if anything trips.
        if not routes or collect_strategy_rule_violations(
            routes,
            idea_grade,
            review=context.get("review"),
            computations=context.get("computation_checks"),
            counterexamples=context.get("counterexamples"),
            tests=context.get("tests"),
        ):
            # Precise claim with no usable agent routes: offer a concrete
            # investigation route (not just "lock the claim"). Still candidate,
            # conservative, and no proof is claimed.
            routes = [_default_investigation_route(idea_grade)]

    allowed_choices = [
        {"strategy_id": r["id"], "effort_options": [r["estimated_effort"]]} for r in routes
    ]
    allowed_choices.append({"strategy_id": "none", "effort_options": ["quick_test"]})

    synthesis = {
        "claim_id": claim_id,
        "synthesized_from": [
            run_id,
            "literature_review.yaml",
            "tests.yaml",
            "counterexamples.yaml",
            "source_grounding.yaml",
            "review.yaml",
            "idea_grade.yaml",
            "loop_control.yaml",
        ],
        "routes": routes,
        "discarded_routes": [],
        "synthesis_note": safe_text(raw.get("synthesis_note", ""))
        or "Candidate routes proposed and graded conservatively; scores are not proof status.",
    }
    brief = {
        "claim_id": claim_id,
        "generated_after_runs": [run_id],
        "top_strategies": routes,
        "also_considered": [],
        "question_for_mathematician": safe_text(raw.get("question_for_mathematician", ""))
        or "Which route should receive time, if any?",
        "allowed_human_choices": allowed_choices,
    }
    StrategySynthesis.model_validate(synthesis)
    StrategyBrief.model_validate(brief)

    write_yaml(run_dir / artifacts["strategy_synthesis"], synthesis)
    write_yaml(run_dir / artifacts["strategy_brief"], brief)
    return {
        "unsafe_language": [],
        "notes": [
            f"strategy wrote {len(routes)} candidate route(s) "
            f"(idea_status={idea_status}; scores are candidate confidence, not proof status)"
        ],
    }


# ---------------------------------------------------------------------------
# S6 — 10 Example/Counterexample Attacker
# ---------------------------------------------------------------------------

