from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.kernel import agent_run_status as ars
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.observability import now_iso
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.evidence_coordinator import run_evidence_batch_parallel
from espresso_proof.orchestration.orchestrator import orchestrate_run
from espresso_proof.orchestration.pressure_lane_barrier import (
    PRESSURE_LANE_BARRIER_FILENAME,
    record_pressure_lane_barrier_committing,
    record_pressure_lane_barrier_compute,
    seed_pressure_lane_barrier,
)
from espresso_proof.validation.errors import KernelValidationError

CLAIM = "Every group of order p^2, where p is prime, is abelian."
PRESSURE_AGENTS = ["counterexamples", "examples", "literature"]


def _fast_run(tmp_path: Path, name: str = "resume") -> Path:
    claim_path = tmp_path / f"{name}.md"
    claim_path.write_text(CLAIM, encoding="utf-8")
    result = orchestrate_run(
        project=name,
        input_path=claim_path,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    return Path(result["run_dir"])


def _barrier(run_dir: Path) -> dict:
    return read_yaml(run_dir / PRESSURE_LANE_BARRIER_FILENAME)


def _assert_completed(run_dir: Path) -> None:
    barrier = _barrier(run_dir)
    assert barrier["barrier_state"] == "completed"
    assert barrier["effect"] == "scheduling_only"
    assert barrier["advisory_only"] is True
    assert barrier["requested_agents"] == PRESSURE_AGENTS
    assert barrier["commit_order"] == PRESSURE_AGENTS
    assert {entry["status"] for entry in barrier["entries"]} == {"committed"}
    for agent in PRESSURE_AGENTS:
        assert ars.agent_status(run_dir, agent) == "done"
    validate_run(run_dir)


def test_pressure_lane_batch_resumes_from_seeded_barrier(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path, "seeded")
    seed_pressure_lane_barrier(run_dir, PRESSURE_AGENTS)
    validate_run(run_dir)

    run_evidence_batch_parallel(run_dir, PRESSURE_AGENTS, max_workers=2)

    _assert_completed(run_dir)


def test_pressure_lane_batch_resumes_from_running_barrier(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path, "running")
    seed_pressure_lane_barrier(run_dir, PRESSURE_AGENTS)
    started = now_iso()
    record_pressure_lane_barrier_compute(
        run_dir,
        {
            agent: {
                "agent": agent,
                "started_at": started,
                "duration_ms": 1,
                "error": None,
            }
            for agent in PRESSURE_AGENTS
        },
    )
    assert _barrier(run_dir)["barrier_state"] == "running"
    validate_run(run_dir)

    run_evidence_batch_parallel(run_dir, PRESSURE_AGENTS, max_workers=2)

    _assert_completed(run_dir)


def test_pressure_lane_batch_resumes_from_joining_barrier(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path, "joining")
    seed_pressure_lane_barrier(run_dir, PRESSURE_AGENTS)
    started = now_iso()
    record_pressure_lane_barrier_compute(
        run_dir,
        {
            agent: {
                "agent": agent,
                "started_at": started,
                "duration_ms": 1,
                "error": None,
            }
            for agent in PRESSURE_AGENTS
        },
    )
    record_pressure_lane_barrier_committing(run_dir, "counterexamples")
    assert _barrier(run_dir)["barrier_state"] == "joining"
    validate_run(run_dir)

    run_evidence_batch_parallel(run_dir, PRESSURE_AGENTS, max_workers=2)

    _assert_completed(run_dir)


def test_pressure_lane_barrier_rejects_status_smuggling(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path, "smuggle")
    barrier = seed_pressure_lane_barrier(run_dir, PRESSURE_AGENTS)
    barrier["notes"].append("This proved the pressure lane.")
    write_yaml(run_dir / PRESSURE_LANE_BARRIER_FILENAME, barrier)

    with pytest.raises(KernelValidationError, match="cannot write mathematical status"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("worker_shared_write", True),
        ("candidate_persisted", True),
    ],
)
def test_pressure_lane_barrier_rejects_worker_shared_state_claims(
    tmp_path: Path,
    field: str,
    value: bool,
) -> None:
    run_dir = _fast_run(tmp_path, f"worker_state_{field}")
    barrier = seed_pressure_lane_barrier(run_dir, PRESSURE_AGENTS)
    barrier["entries"][0][field] = value
    write_yaml(run_dir / PRESSURE_LANE_BARRIER_FILENAME, barrier)

    with pytest.raises(KernelValidationError, match="schema validation failed"):
        validate_run(run_dir)


def test_pressure_lane_barrier_rejects_empty_seed_plan(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path, "empty_barrier")
    barrier = seed_pressure_lane_barrier(run_dir, PRESSURE_AGENTS)
    barrier["requested_agents"] = []
    barrier["commit_order"] = []
    barrier["entries"] = []
    write_yaml(run_dir / PRESSURE_LANE_BARRIER_FILENAME, barrier)

    with pytest.raises(KernelValidationError, match="at least one agent"):
        validate_run(run_dir)


def test_pressure_lane_barrier_entries_must_follow_commit_order(
    tmp_path: Path,
) -> None:
    run_dir = _fast_run(tmp_path, "barrier_entry_order")
    barrier = seed_pressure_lane_barrier(run_dir, PRESSURE_AGENTS)
    barrier["entries"] = list(reversed(barrier["entries"]))
    write_yaml(run_dir / PRESSURE_LANE_BARRIER_FILENAME, barrier)

    with pytest.raises(KernelValidationError, match="entries must follow commit_order"):
        validate_run(run_dir)


def test_pressure_lane_barrier_requested_agents_must_follow_commit_order(
    tmp_path: Path,
) -> None:
    run_dir = _fast_run(tmp_path, "barrier_requested_order")
    barrier = seed_pressure_lane_barrier(run_dir, PRESSURE_AGENTS)
    barrier["requested_agents"] = list(reversed(barrier["requested_agents"]))
    write_yaml(run_dir / PRESSURE_LANE_BARRIER_FILENAME, barrier)

    with pytest.raises(
        KernelValidationError,
        match="requested_agents must follow commit_order",
    ):
        validate_run(run_dir)


@pytest.mark.parametrize(
    ("barrier_state", "entry_status"),
    [
        ("seeded", "computed"),
        ("running", "committed"),
    ],
)
def test_pressure_lane_barrier_rejects_impossible_state_entry_status(
    tmp_path: Path,
    barrier_state: str,
    entry_status: str,
) -> None:
    run_dir = _fast_run(tmp_path, f"barrier_state_{barrier_state}_{entry_status}")
    barrier = seed_pressure_lane_barrier(run_dir, PRESSURE_AGENTS)
    now = now_iso()
    barrier["barrier_state"] = barrier_state
    barrier["updated_at"] = now
    barrier["entries"][0]["status"] = entry_status
    barrier["entries"][0]["started_at"] = now
    barrier["entries"][0]["finished_at"] = now
    write_yaml(run_dir / PRESSURE_LANE_BARRIER_FILENAME, barrier)

    with pytest.raises(KernelValidationError, match=f"{barrier_state} pressure lane"):
        validate_run(run_dir)


def test_pressure_lane_barrier_joining_requires_commit_phase_entry(
    tmp_path: Path,
) -> None:
    run_dir = _fast_run(tmp_path, "joining_without_commit_phase")
    barrier = seed_pressure_lane_barrier(run_dir, PRESSURE_AGENTS)
    now = now_iso()
    barrier["barrier_state"] = "joining"
    barrier["updated_at"] = now
    for entry in barrier["entries"]:
        entry["status"] = "computed"
        entry["started_at"] = now
        entry["finished_at"] = now
    write_yaml(run_dir / PRESSURE_LANE_BARRIER_FILENAME, barrier)

    with pytest.raises(KernelValidationError, match="commit-phase entry"):
        validate_run(run_dir)


def test_pressure_lane_barrier_committed_requires_agent_done(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path, "stale")
    barrier = seed_pressure_lane_barrier(run_dir, PRESSURE_AGENTS)
    now = now_iso()
    barrier["barrier_state"] = "completed"
    barrier["updated_at"] = now
    for entry in barrier["entries"]:
        entry["status"] = "committed"
        entry["started_at"] = now
        entry["finished_at"] = now
    write_yaml(run_dir / PRESSURE_LANE_BARRIER_FILENAME, barrier)

    with pytest.raises(KernelValidationError, match="committed but agent board"):
        validate_run(run_dir)
