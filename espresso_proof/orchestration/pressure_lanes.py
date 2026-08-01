"""Display contract for default-visible pressure lanes.

This module is read-only. It exposes the Literature / Examples /
Counterexamples lanes as process state for product surfaces without executing
them, scheduling parallel work, or upgrading proof/source/evidence status.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.agent_run_status import AGENT_ARTIFACTS, AGENT_TITLES, agent_status
from espresso_proof.kernel.validators import validate_run
from espresso_proof.kernel.agent_flow_spec import get_stage
from espresso_proof.orchestration.agent_flow_status import load_stage_status_projection
from espresso_proof.kernel.pressure_lane_barrier import load_pressure_lane_barrier

PRESSURE_LANE_STAGE_IDS: tuple[str, ...] = (
    "literature_review",
    "examples",
    "counterexamples",
)

PRESSURE_LANE_AGENTS: dict[str, str] = {
    "literature_review": "literature",
    "examples": "examples",
    "counterexamples": "counterexamples",
}

_STATUS_WORDS = {
    "not_started": "Not started",
    "queued": "Queued",
    "running": "Running",
    "done": "Done",
    "skipped": "Skipped",
    "blocked": "Blocked",
    "failed": "Needs check",
}

_AGENT_TO_STAGE_STATUS = {
    "not_run": "not_started",
    "queued": "queued",
    "running": "running",
    "done": "done",
    "skipped": "skipped",
    "failed": "failed",
}

_VISIBLE_STAGE_STATUS_WHEN_AGENT_NOT_RUN = {
    "queued",
    "running",
    "done",
    "skipped",
    "blocked",
    "failed",
}


def build_pressure_lane_view(run_dir: Path) -> list[dict]:
    """Return default-visible pressure lane state for UI/memo surfaces."""

    run_dir = Path(run_dir)
    stage_entries = _stage_entries(run_dir)
    barrier_entries = _barrier_entries(run_dir)
    lanes: list[dict] = []
    for stage_id in PRESSURE_LANE_STAGE_IDS:
        spec = get_stage(stage_id)
        agent = PRESSURE_LANE_AGENTS[stage_id]
        stage_entry = stage_entries.get(stage_id, {})
        barrier_entry = barrier_entries.get(agent, {})
        live_agent_status = agent_status(run_dir, agent)
        artifact_present = _lane_artifact_present(run_dir, agent)
        claim_clear_blocked = bool(stage_entry.get("claim_clear_blocked"))
        status = _process_status(live_agent_status, stage_entry, barrier_entry)
        lanes.append(
            {
                "stage_id": stage_id,
                "agent": agent,
                "title": AGENT_TITLES.get(agent, spec.display_name),
                "display_name": spec.display_name,
                "status": status,
                "status_word": _STATUS_WORDS.get(status, "Unknown"),
                "agent_status": live_agent_status,
                "default_visible": True,
                "default_execution": "on_demand",
                "counts_as_evidence": _counts_as_evidence(
                    agent,
                    live_agent_status,
                    artifact_present,
                    claim_clear_blocked=claim_clear_blocked,
                ),
                "pending_counts_as_evidence": False,
                "evidence_note": _evidence_note(
                    agent,
                    live_agent_status,
                    status,
                    artifact_present,
                    claim_clear_blocked=claim_clear_blocked,
                    barrier_entry=barrier_entry,
                ),
                "claim_clear_blocked": claim_clear_blocked,
            }
        )
    return lanes


def pressure_lane_memo_lines(run_dir: Path) -> list[str]:
    """Human-readable pressure lane lines for the compact memo."""

    lanes = build_pressure_lane_view(run_dir)
    if not lanes:
        return []
    # One line per lane that has something of its OWN to report; every idle
    # lane shares one collapsed line. The section itself stays — pending
    # pressure is exactly why routes render PRELIMINARY.
    def _idle(lane: dict) -> bool:
        return "not started" in str(lane.get("status_word", "")).lower()

    lines = ["Pressure lanes:"]
    idle_titles: list[str] = []
    for lane in lanes:
        if _idle(lane):
            idle_titles.append(str(lane["title"]))
            continue
        lines.append(
            f"  - {lane['title']}: {lane['status_word']} - {lane['evidence_note']}"
        )
    if idle_titles:
        lines.append(
            "  - " + ", ".join(idle_titles) + ": Not started - pending; not counted as evidence"
        )
    return lines


def _stage_entries(run_dir: Path) -> dict[str, dict]:
    try:
        validate_run(run_dir)
        report = load_stage_status_projection(run_dir)
    except Exception:  # noqa: BLE001 - display-only fallback
        return {}
    return {entry.stage_id: entry.model_dump(mode="json") for entry in report.stages}


def _barrier_entries(run_dir: Path) -> dict[str, dict]:
    try:
        barrier = load_pressure_lane_barrier(run_dir)
    except Exception:  # noqa: BLE001 - display-only fallback
        return {}
    if not isinstance(barrier, dict):
        return {}
    return {
        str(entry.get("agent")): entry
        for entry in barrier.get("entries", [])
        if isinstance(entry, dict) and entry.get("role") == "pressure_lane"
    }


def _barrier_process_status(barrier_entry: dict) -> str:
    status = str(barrier_entry.get("status") or "")
    return {
        "seeded": "queued",
        "computed": "queued",
        "compute_failed": "failed",
        "committing": "running",
        "committed": "done",
        "failed": "failed",
    }.get(status, "")


def _process_status(
    agent_status_value: str,
    stage_entry: dict,
    barrier_entry: dict,
) -> str:
    stage_status = str(stage_entry.get("status") or "")
    if (
        agent_status_value == "not_run"
        and stage_status in _VISIBLE_STAGE_STATUS_WHEN_AGENT_NOT_RUN
    ):
        return stage_status
    if agent_status_value == "not_run":
        barrier_status = _barrier_process_status(barrier_entry)
        if barrier_status:
            return barrier_status
    mapped = _AGENT_TO_STAGE_STATUS.get(agent_status_value)
    if mapped is not None:
        return mapped
    return stage_status or "not_started"


def _lane_artifact_present(run_dir: Path, agent: str) -> bool:
    artifact = AGENT_ARTIFACTS.get(agent)
    return bool(artifact) and (Path(run_dir) / artifact).exists()


def _counts_as_evidence(
    agent: str,
    agent_status_value: str,
    artifact_present: bool,
    *,
    claim_clear_blocked: bool,
) -> bool:
    if claim_clear_blocked:
        return False
    if agent_status_value != "done":
        return False
    # Literature remains candidate context; source grounding earns reference
    # support separately.
    return agent in {"examples", "counterexamples"} and artifact_present


def _evidence_note(
    agent: str,
    agent_status_value: str,
    process_status: str,
    artifact_present: bool,
    *,
    claim_clear_blocked: bool,
    barrier_entry: dict,
) -> str:
    if claim_clear_blocked:
        return "blocked by claim-clear gate; contributes no evidence"
    if agent_status_value == "done":
        if agent == "literature":
            return "candidate context only; not reference support"
        if not artifact_present:
            return "finished, but validated artifact is unavailable; contributes no evidence until rerun"
        return "finished; underlying validated artifact may contribute evidence"
    barrier_status = str(barrier_entry.get("status") or "")
    if barrier_status == "compute_failed":
        return "worker compute failed; contributes no evidence until rerun"
    if barrier_status == "failed":
        return "coordinator commit failed; contributes no evidence until rerun"
    if process_status == "done":
        return "stage finished, but agent evidence gate is not done; contributes no evidence"
    if process_status == "running":
        return "in progress; not yet evidence"
    if process_status == "queued":
        return "queued; not yet evidence"
    if process_status == "skipped":
        return "skipped; contributes no evidence"
    if process_status == "failed":
        return "failed; contributes no evidence until rerun"
    if process_status == "blocked":
        return "blocked; contributes no evidence"
    return "pending; not counted as evidence"


__all__ = [
    "PRESSURE_LANE_AGENTS",
    "PRESSURE_LANE_STAGE_IDS",
    "build_pressure_lane_view",
    "pressure_lane_memo_lines",
]
