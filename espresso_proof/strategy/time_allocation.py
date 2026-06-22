"""Strategy-facing Time Allocation service boundary.

The kernel model/writer remains in ``espresso_proof.kernel.time_allocation`` for
now. This module gives orchestration and strategy code a named service boundary:
allocation may be started only from graded strategy output.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.time_allocation import (
    TIME_ALLOCATION_FILENAME,
    build_time_allocation_plan,
    write_time_allocation_plan,
)
from espresso_proof.strategy.grading import STRATEGY_GRADER_STAGE_ID, StrategyGradingResult

_GRADED_ROUTE_REQUIRED_FIELDS = {"id", "score", "status"}


def _has_graded_routes(grading: StrategyGradingResult) -> bool:
    return bool(grading.routes) and all(
        isinstance(route, dict)
        and _GRADED_ROUTE_REQUIRED_FIELDS.issubset(route)
        for route in grading.routes
    )


def write_time_allocation_after_strategy_grader(
    run_dir: Path,
    *,
    grading: StrategyGradingResult,
):
    """Start scheduling-only allocation after Strategy Grader has produced routes."""

    if grading.trigger_stage != STRATEGY_GRADER_STAGE_ID:
        raise ValueError("time allocation can start only after strategy_grader")
    if not _has_graded_routes(grading):
        raise ValueError("time allocation requires graded strategy routes")
    return write_time_allocation_plan(run_dir)


__all__ = [
    "TIME_ALLOCATION_FILENAME",
    "build_time_allocation_plan",
    "write_time_allocation_after_strategy_grader",
]
