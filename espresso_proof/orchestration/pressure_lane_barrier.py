"""Durable pressure-lane execution barrier helpers.

The barrier is scheduling/process metadata only. It records the explicit
SEED/RUN/JOIN/RESUME discipline for batch pressure lanes while keeping worker
candidates in memory and committing artifacts only through the existing
``run_single_agent`` validation path.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import PressureLaneBarrierReport
from espresso_proof.kernel.observability import now_iso

PRESSURE_LANE_BARRIER_FILENAME = "pressure_lane_barrier.yaml"
PRESSURE_LANE_BARRIER_MODE = "explicit_batch_parallel_compute_serial_commit"

PRESSURE_LANE_BARRIER_COMMIT_ORDER: tuple[str, ...] = (
    "counterexamples",
    "examples",
    "literature",
    "failure_patterns",
)

PRESSURE_LANE_BARRIER_AGENT_STAGE_IDS: dict[str, str] = {
    "literature": "literature_review",
    "examples": "examples",
    "counterexamples": "counterexamples",
    "failure_patterns": "failure_classifier",
}

PRESSURE_LANE_BARRIER_AGENT_ROLES: dict[str, str] = {
    "literature": "pressure_lane",
    "examples": "pressure_lane",
    "counterexamples": "pressure_lane",
    "failure_patterns": "dependent_audit",
}


def order_for_pressure_lane_barrier(agents: set[str]) -> list[str]:
    """Return deterministic barrier/commit order for requested agents."""

    ordered = [a for a in PRESSURE_LANE_BARRIER_COMMIT_ORDER if a in agents]
    ordered += [
        a for a in sorted(agents) if a not in PRESSURE_LANE_BARRIER_COMMIT_ORDER
    ]
    return ordered


def load_pressure_lane_barrier(run_dir: Path) -> dict | None:
    """Load the persisted barrier dict, or ``None`` if absent."""

    path = Path(run_dir) / PRESSURE_LANE_BARRIER_FILENAME
    if not path.exists():
        return None
    return read_yaml(path)


def seed_pressure_lane_barrier(run_dir: Path, agents: list[str]) -> dict:
    """SEED: write the deterministic lane plan before worker compute starts."""

    run_dir = Path(run_dir)
    ordered = order_for_pressure_lane_barrier(set(agents))
    unknown = [
        agent
        for agent in ordered
        if agent not in PRESSURE_LANE_BARRIER_AGENT_STAGE_IDS
    ]
    if unknown:
        raise ValueError(f"unknown pressure lane barrier agents: {unknown}")

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    now = now_iso()
    report = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": now,
        "updated_at": now,
        "mode": PRESSURE_LANE_BARRIER_MODE,
        "barrier_state": "seeded",
        "requested_agents": ordered,
        "commit_order": ordered,
        "entries": [
            _entry(agent, status="seeded")
            for agent in ordered
        ],
        "advisory_only": True,
        "effect": "scheduling_only",
        "notes": [
            "Scheduling barrier only; lane completion does not imply mathematical validation.",
            "Worker candidates are not persisted here; coordinator commits through validated agent paths.",
        ],
    }
    return _write_pressure_lane_barrier(run_dir, report)


def record_pressure_lane_barrier_compute(
    run_dir: Path,
    timed: dict[str, dict],
) -> dict:
    """RUN: record worker compute outcomes after all workers have joined memory."""

    report = _existing_report(run_dir)
    now = now_iso()
    entries = _entries_by_agent(report)
    for agent, info in timed.items():
        if agent not in entries:
            continue
        error = info.get("error")
        entries[agent].update(
            {
                "status": "compute_failed" if error else "computed",
                "started_at": info.get("started_at"),
                "finished_at": now,
                "duration_ms": int(info.get("duration_ms") or 0),
                "error_summary": str(error)[:500] if error else None,
                "worker_shared_write": False,
                "candidate_persisted": False,
            }
        )
    report["barrier_state"] = "running"
    report["updated_at"] = now
    report["entries"] = [entries[agent] for agent in report["commit_order"]]
    return _write_pressure_lane_barrier(run_dir, report)


def record_pressure_lane_barrier_committing(run_dir: Path, agent: str) -> dict:
    """JOIN: mark one lane as entering coordinator-owned serial commit."""

    report = _existing_report(run_dir)
    now = now_iso()
    entries = _entries_by_agent(report)
    entry = entries[agent]
    entry.update(
        {
            "status": "committing",
            "started_at": entry.get("started_at") or now,
            "finished_at": None,
            "duration_ms": entry.get("duration_ms"),
            "worker_shared_write": False,
            "candidate_persisted": False,
        }
    )
    report["barrier_state"] = "joining"
    report["updated_at"] = now
    report["entries"] = [entries[name] for name in report["commit_order"]]
    return _write_pressure_lane_barrier(run_dir, report)


def record_pressure_lane_barrier_commit_result(
    run_dir: Path,
    agent: str,
    result: dict,
) -> dict:
    """JOIN: record the validated commit result for one lane."""

    status = "committed" if result.get("status") == "done" else "failed"
    return record_pressure_lane_barrier_agent_result(
        run_dir,
        agent,
        status=status,
        error_summary=result.get("error"),
    )


def record_pressure_lane_barrier_agent_result(
    run_dir: Path,
    agent: str,
    *,
    status: str,
    error_summary: str | None = None,
) -> dict:
    """Record a terminal coordinator result for one lane."""

    report = _existing_report(run_dir)
    now = now_iso()
    entries = _entries_by_agent(report)
    entry = entries[agent]
    entry.update(
        {
            "status": status,
            "started_at": entry.get("started_at") or now,
            "finished_at": now,
            "error_summary": str(error_summary)[:500] if error_summary else None,
            "worker_shared_write": False,
            "candidate_persisted": False,
        }
    )
    report["barrier_state"] = "joining"
    report["updated_at"] = now
    report["entries"] = [entries[name] for name in report["commit_order"]]
    return _write_pressure_lane_barrier(run_dir, report)


def finalize_pressure_lane_barrier(run_dir: Path) -> dict:
    """RESUME/FINALIZE: close the barrier after serial commits finish."""

    report = _existing_report(run_dir)
    statuses = {entry["status"] for entry in report["entries"]}
    report["barrier_state"] = "completed" if statuses == {"committed"} else "failed"
    report["updated_at"] = now_iso()
    return _write_pressure_lane_barrier(run_dir, report)


def _entry(agent: str, *, status: str) -> dict:
    return {
        "agent": agent,
        "stage_id": PRESSURE_LANE_BARRIER_AGENT_STAGE_IDS[agent],
        "role": PRESSURE_LANE_BARRIER_AGENT_ROLES[agent],
        "status": status,
        "started_at": None,
        "finished_at": None,
        "duration_ms": None,
        "error_summary": None,
        "worker_shared_write": False,
        "candidate_persisted": False,
    }


def _existing_report(run_dir: Path) -> dict:
    report = load_pressure_lane_barrier(run_dir)
    if report is None:
        raise FileNotFoundError(PRESSURE_LANE_BARRIER_FILENAME)
    return report


def _entries_by_agent(report: dict) -> dict[str, dict]:
    return {
        str(entry["agent"]): dict(entry)
        for entry in report.get("entries", [])
    }


def _write_pressure_lane_barrier(run_dir: Path, report: dict) -> dict:
    PressureLaneBarrierReport.model_validate(report)
    run_dir = Path(run_dir)
    write_yaml(run_dir / PRESSURE_LANE_BARRIER_FILENAME, report)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})[
        "pressure_lane_barrier"
    ] = PRESSURE_LANE_BARRIER_FILENAME
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return report


__all__ = [
    "PRESSURE_LANE_BARRIER_AGENT_ROLES",
    "PRESSURE_LANE_BARRIER_AGENT_STAGE_IDS",
    "PRESSURE_LANE_BARRIER_COMMIT_ORDER",
    "PRESSURE_LANE_BARRIER_FILENAME",
    "PRESSURE_LANE_BARRIER_MODE",
    "finalize_pressure_lane_barrier",
    "load_pressure_lane_barrier",
    "order_for_pressure_lane_barrier",
    "record_pressure_lane_barrier_agent_result",
    "record_pressure_lane_barrier_commit_result",
    "record_pressure_lane_barrier_committing",
    "record_pressure_lane_barrier_compute",
    "seed_pressure_lane_barrier",
]
