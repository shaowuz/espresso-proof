"""Strategy Brief service boundary.

This service constructs and persists the existing strategy artifacts without
changing their names or schemas.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from espresso_proof.kernel.proof_language import safe_text
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import (
    ScopedVerificationRequests,
    StrategyBrief,
    StrategySynthesis,
)
from espresso_proof.strategy.grading import StrategyGradingResult


@dataclass(frozen=True)
class StrategyArtifacts:
    """The existing strategy artifacts built from generated + graded routes."""

    synthesis: dict
    brief: dict



# Ordered cheapest-first; index is the tier.
EFFORT_TIERS = ("quick_test", "normal_pass", "deep_exploration", "long_watch")

# What each tier is expected to cost, in total model tokens. Deliberately coarse:
# it exists so a declared budget can be turned into a tier ceiling, not to
# predict spend. Measured order of magnitude from real runs: a quick_test prover
# pass on an open problem ran ~750k input tokens across 13 calls.
EFFORT_TOKEN_COST = {
    "quick_test": 800_000,
    "normal_pass": 1_600_000,
    "deep_exploration": 3_200_000,
    "long_watch": 6_400_000,
}


def _declared_effort_ceiling(context: dict) -> str | None:
    """The highest tier a declared token budget can pay for, or None.

    Reads only what the run recorded. No budget recorded means no ceiling and
    no behaviour change.
    """

    budget = context.get("token_budget")
    if budget is None:
        manifest = context.get("manifest") or {}
        budget = manifest.get("token_budget")
    try:
        budget = int(budget)
    except (TypeError, ValueError):
        return None
    if budget <= 0:
        return None
    affordable = [tier for tier in EFFORT_TIERS if EFFORT_TOKEN_COST[tier] <= budget]
    return affordable[-1] if affordable else EFFORT_TIERS[0]


def _effort_options_for(estimated: str, ceiling: str | None) -> list[str]:
    """Tiers the human may approve for a route: its estimate, plus any headroom
    the declared budget pays for.

    The estimate is always included even when the budget cannot afford it — the
    grader's judgement of what the route needs is not overridden by a small
    budget, it is simply not funded beyond that.
    """

    if estimated not in EFFORT_TIERS:
        return [estimated]
    if ceiling is None or ceiling not in EFFORT_TIERS:
        return [estimated]
    lo = EFFORT_TIERS.index(estimated)
    hi = EFFORT_TIERS.index(ceiling)
    if hi <= lo:
        return [estimated]
    return list(EFFORT_TIERS[lo : hi + 1])


def build_strategy_artifacts(
    *,
    context: dict,
    raw: dict,
    grading: StrategyGradingResult,
) -> StrategyArtifacts:
    """Build strategy_synthesis.yaml and strategy_brief.yaml payloads."""

    manifest = context["manifest"]
    run_id = manifest["run_id"]
    claim_id = context["claim"]["id"]
    routes = grading.routes
    # A route used to offer exactly the one effort tier the grader estimated, so
    # a route graded quick_test was capped at a single prover round no matter how
    # much the human was willing to spend. On an open problem -- one with no
    # one-round route -- that made the loop structurally unable to iterate.
    #
    # The route's estimate still sets the floor: it is the grader's judgement of
    # what the route needs, and it is always offered. What the declared budget
    # adds is headroom above it. A run with no declared budget behaves exactly as
    # before.
    declared = _declared_effort_ceiling(context)
    allowed_choices = [
        {
            "strategy_id": route["id"],
            "effort_options": _effort_options_for(route["estimated_effort"], declared),
        }
        for route in routes
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
    return StrategyArtifacts(synthesis=synthesis, brief=brief)


def write_strategy_artifacts(
    run_dir: Path,
    *,
    context: dict,
    artifacts: StrategyArtifacts,
) -> None:
    """Persist strategy_synthesis.yaml and strategy_brief.yaml."""

    run_dir = Path(run_dir)
    manifest_artifacts = context["manifest"]["artifacts"]
    write_yaml(run_dir / manifest_artifacts["strategy_synthesis"], artifacts.synthesis)
    write_yaml(run_dir / manifest_artifacts["strategy_brief"], artifacts.brief)


def write_scoped_verification_requests(run_dir: Path, *, context: dict) -> int:
    """Persist deterministic scoped-verification requests proposed from strategy context."""

    from espresso_proof.verification.verification_request_probes import (
        propose_scoped_verification_requests,
    )

    run_dir = Path(run_dir)
    claim = context["claim"]
    claim_id = claim["id"]
    requests = propose_scoped_verification_requests(claim)
    if not requests:
        return 0
    requests_doc = {"claim_id": claim_id, "requests": requests}
    ScopedVerificationRequests.model_validate(requests_doc)
    write_yaml(run_dir / "scoped_verification_requests.yaml", requests_doc)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["scoped_verification_requests"] = (
        "scoped_verification_requests.yaml"
    )
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return len(requests)


__all__ = [
    "StrategyArtifacts",
    "build_strategy_artifacts",
    "write_scoped_verification_requests",
    "write_strategy_artifacts",
]
