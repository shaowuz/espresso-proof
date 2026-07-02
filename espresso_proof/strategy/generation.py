"""Strategy Generator service boundary.

The generator asks for candidate routes and returns raw model output. It is
pure with respect to a run directory: it writes no artifacts and cannot start
time allocation.
"""

from __future__ import annotations

import json

from espresso_proof.kernel.llm_client import LLMClient

STRATEGY_SYSTEM = (
    "You are the Espresso Proof Strategy Generator. Propose CANDIDATE proof "
    "routes for the locked claim. Do not score, grade, rank, allocate time, or "
    "construct the strategy brief; those are separate Espresso services. Source "
    "gaps must be named. If the claim is still unclear, the only honest route is "
    "to lock the claim first. Respond with ONLY a JSON object."
)

STRATEGY_OUTPUT_SCHEMA = {
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


def strategy_generation_payload(context: dict) -> dict:
    """Build the Strategy Generator prompt payload from validated context."""

    claim = context["claim"]
    idea_grade = context.get("idea_grade") or {"status": "unclear", "score": 0}
    return {
        "task": "generate_candidate_strategies",
        "claim": {
            "normalized_statement": claim.get("normalized_statement", ""),
            "conclusion": claim.get("conclusion", ""),
        },
        "idea_status": idea_grade.get("status", "unclear"),
        "required_json_schema": STRATEGY_OUTPUT_SCHEMA,
        "rules": [
            "do not score, rank, or grade routes",
            "do not construct the strategy brief",
            "do not start or request time allocation",
            "name source gaps; do not claim source_backed",
            "an unclear claim can only recommend locking the claim first",
        ],
    }


def generate_strategy_candidates(*, client: LLMClient, context: dict) -> dict:
    """Return raw candidate-route output from the model, without writing files."""

    raw = client.complete_json(
        system=STRATEGY_SYSTEM,
        prompt=json.dumps(strategy_generation_payload(context), indent=2),
    )
    return raw if isinstance(raw, dict) else {}


__all__ = [
    "STRATEGY_OUTPUT_SCHEMA",
    "STRATEGY_SYSTEM",
    "generate_strategy_candidates",
    "strategy_generation_payload",
]
