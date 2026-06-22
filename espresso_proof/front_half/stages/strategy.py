"""Front-half Strategy adapter (Phase 04 physical split).

The service boundaries live in ``espresso_proof.strategy``. This adapter keeps
the legacy front-half stage name and artifacts stable while delegating route
generation, grading, brief construction, scoped requests, and time allocation
to separate services.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.agents.llm_agents import LLMClient
from espresso_proof.strategy.brief import (
    build_strategy_artifacts,
    write_scoped_verification_requests,
    write_strategy_artifacts,
)
from espresso_proof.strategy.generation import generate_strategy_candidates
from espresso_proof.strategy.grading import grade_strategy_candidates
from espresso_proof.strategy.time_allocation import write_time_allocation_after_strategy_grader
from ..registry import register_stage


@register_stage("strategy")
def _strategy(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    raw = generate_strategy_candidates(client=client, context=context)
    grading = grade_strategy_candidates(raw=raw, context=context)
    strategy_artifacts = build_strategy_artifacts(
        context=context,
        raw=raw,
        grading=grading,
    )
    write_strategy_artifacts(run_dir, context=context, artifacts=strategy_artifacts)

    request_count = write_scoped_verification_requests(run_dir, context=context)
    allocation_plan = write_time_allocation_after_strategy_grader(
        run_dir,
        grading=grading,
    )

    return {
        "unsafe_language": [],
        "notes": [
            f"strategy wrote {len(grading.routes)} candidate route(s) "
            f"(idea_status={grading.idea_status}; scores are candidate confidence, not proof status)"
            + (
                f"; {request_count} deterministic scoped-verification request(s) proposed"
                if request_count
                else ""
            )
            + (
                "; provisional time allocation started"
                if allocation_plan is not None
                else ""
            )
        ],
    }


__all__ = ["_strategy"]
