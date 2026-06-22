"""Runtime compatibility driver for the executable AGENT_FLOW graph.

This module is intentionally thin. It lets the orchestrator ask the declarative
AGENT_FLOW spec / legacy map for today's compatible runtime plan, and it owns the
transactional write of the process-only ``stage_status.yaml`` projection.

It does not run proof agents, interpret mathematical status, or override the
kernel. All writes re-enter ``validate_run`` before they are accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from espresso_proof.front_half.registry import FRONT_HALF_STAGES
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.agent_flow_legacy_map import (
    LEGACY_FRONT_HALF_FAST_ORDER,
    LEGACY_FRONT_HALF_FULL_ORDER,
    mapped_stage_ids,
    validate_agent_flow_legacy_map,
)
from espresso_proof.orchestration.agent_flow_spec import ordered_stage_ids
from espresso_proof.orchestration.agent_flow_status import (
    STAGE_STATUS_FILENAME,
    write_stage_status_projection,
)

if TYPE_CHECKING:
    from espresso_proof.kernel.stage_runner import StageRunner


@dataclass(frozen=True)
class AgentFlowRunPlan:
    """Current runtime plan derived from AGENT_FLOW compatibility metadata."""

    fast: bool
    conceptual_stage_ids: tuple[str, ...]
    front_half_stages: tuple[str, ...]
    deferred_front_half_stages: tuple[str, ...]
    notes: tuple[str, ...]


def build_agent_flow_run_plan(*, fast: bool = False) -> AgentFlowRunPlan:
    """Return the AGENT_FLOW-derived front-half plan for the current runtime."""

    _validate_driver_compatibility()
    front_half_stages = (
        LEGACY_FRONT_HALF_FAST_ORDER if fast else LEGACY_FRONT_HALF_FULL_ORDER
    )
    deferred_front_half_stages = tuple(
        stage for stage in LEGACY_FRONT_HALF_FULL_ORDER if stage not in front_half_stages
    )
    mode_note = (
        "Fast compatibility plan defers pressure lanes for on-demand execution."
        if fast
        else "Full compatibility plan follows the legacy front-half order."
    )
    return AgentFlowRunPlan(
        fast=fast,
        conceptual_stage_ids=ordered_stage_ids(),
        front_half_stages=front_half_stages,
        deferred_front_half_stages=deferred_front_half_stages,
        notes=(
            mode_note,
            "Plan is process scheduling only; mathematical trust remains kernel-owned.",
        ),
    )


def front_half_stages_for_runner(*, fast: bool = False) -> list[str]:
    """Return the physical front-half stage list to pass to the legacy runner."""

    return list(build_agent_flow_run_plan(fast=fast).front_half_stages)


def record_agent_flow_stage_status(
    run_dir: Path,
    *,
    runner: "StageRunner | None" = None,
    include_artifact_hashes: bool = False,
) -> dict:
    """Write ``stage_status.yaml`` and validate it inside an optional transaction.

    Runtime writes default to a hashless projection so later scheduling-only
    mutations can refresh the process status without being blocked by old
    artifact hashes. Hash-bound projections remain available through
    ``write_stage_status_projection(..., include_artifact_hashes=True)`` and are
    still checked by the kernel when present.
    """

    run_dir = Path(run_dir)
    report: dict = {}

    def _mutate() -> None:
        nonlocal report
        report = write_stage_status_projection(
            run_dir,
            include_artifact_hashes=include_artifact_hashes,
        )

    def _validate() -> None:
        validate_run(run_dir)

    if runner is None:
        from espresso_proof.kernel.stage_runner import StageRunner

        runner = StageRunner(run_dir)

    runner.run(
        name="agent_flow_stage_status",
        mutate=_mutate,
        validate=_validate,
        snapshot_paths=[STAGE_STATUS_FILENAME],
    )
    return report


def _validate_driver_compatibility() -> None:
    validate_agent_flow_legacy_map()
    if mapped_stage_ids() != ordered_stage_ids():
        raise RuntimeError("AGENT_FLOW legacy map no longer matches the spec")
    if tuple(FRONT_HALF_STAGES) != LEGACY_FRONT_HALF_FULL_ORDER:
        raise RuntimeError("front-half registry order drifted from AGENT_FLOW driver")
    fast_positions = [
        LEGACY_FRONT_HALF_FULL_ORDER.index(stage)
        for stage in LEGACY_FRONT_HALF_FAST_ORDER
    ]
    if fast_positions != sorted(fast_positions):
        raise RuntimeError("fast front-half order must preserve full front-half order")


__all__ = [
    "AgentFlowRunPlan",
    "build_agent_flow_run_plan",
    "front_half_stages_for_runner",
    "record_agent_flow_stage_status",
]
