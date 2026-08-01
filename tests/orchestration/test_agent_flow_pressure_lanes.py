from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.agent_run_status import agent_status
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.observability import now_iso
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration import evidence_coordinator as ec
from espresso_proof.orchestration.evidence_coordinator import run_evidence_batch_auto
from espresso_proof.orchestration.agent_runner import run_single_agent
from espresso_proof.orchestration.orchestrator import orchestrate_run
from espresso_proof.kernel.pressure_lane_barrier import (
    record_pressure_lane_barrier_compute,
    seed_pressure_lane_barrier,
)
from espresso_proof.orchestration.pressure_lanes import (
    PRESSURE_LANE_STAGE_IDS,
    build_pressure_lane_view,
)
from espresso_proof.orchestration.run_dashboard import status_summary
from espresso_proof.ui.cockpit_view import build_panel, build_snapshot
from espresso_proof.ui.memo import build_concise_memo

CLAIM = "Every group of order p^2, where p is prime, is abelian.\n"
UNCLEAR_CLAIM = "Prove the thing about the nice mirror object.\n"


def _run(tmp_path: Path) -> Path:
    claim = tmp_path / "claim.md"
    claim.write_text(CLAIM, encoding="utf-8")
    result = orchestrate_run(
        project="pressure_lanes",
        input_path=claim,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    return Path(result["run_dir"])


def _unclear_run(tmp_path: Path) -> Path:
    claim = tmp_path / "unclear_claim.md"
    claim.write_text(UNCLEAR_CLAIM, encoding="utf-8")
    result = orchestrate_run(
        project="pressure_lanes_claim_gate",
        input_path=claim,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
    )
    return Path(result["run_dir"])


def _by_stage(run_dir: Path) -> dict[str, dict]:
    return {lane["stage_id"]: lane for lane in build_pressure_lane_view(run_dir)}


def test_fast_run_exposes_pressure_lanes_without_default_execution(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    lanes = _by_stage(run_dir)

    assert tuple(lanes) == PRESSURE_LANE_STAGE_IDS
    for stage_id, lane in lanes.items():
        assert lane["default_visible"] is True
        assert lane["default_execution"] == "on_demand"
        assert lane["status"] == "not_started", stage_id
        assert lane["agent_status"] == "not_run"
        assert lane["counts_as_evidence"] is False
        assert lane["pending_counts_as_evidence"] is False

    for agent in ("literature", "examples", "counterexamples"):
        assert agent_status(run_dir, agent) == "not_run"


def test_pressure_lanes_are_in_cockpit_snapshot_and_panel_slices(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    status = status_summary(run_dir)
    snapshot = build_snapshot(run_dir, run_dir.name, status)

    lanes = {lane["agent"]: lane for lane in snapshot["pressure_lanes"]}
    assert set(lanes) == {"literature", "examples", "counterexamples"}
    assert lanes["examples"]["status_word"] == "Not started"
    assert lanes["examples"]["counts_as_evidence"] is False

    examples_panel = build_panel(run_dir, run_dir.name, status, "examples")
    literature_panel = build_panel(run_dir, run_dir.name, status, "literature")
    assert examples_panel["pressure_lane"]["agent"] == "examples"
    assert examples_panel["pressure_lane"]["status"] == "not_started"
    assert literature_panel["pressure_lane"]["counts_as_evidence"] is False


def test_pending_pressure_lanes_show_in_memo_and_keep_routes_preliminary(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    memo = build_concise_memo(run_dir)

    assert "Pressure lanes:" in memo
    # Idle lanes collapse into one honest line (repeating "not started" once per
    # lane was noise); each lane is still NAMED and still not evidence.
    assert "Not started - pending; not counted as evidence" in memo
    for lane in ("Literature Review", "Examples", "Counterexamples"):
        assert lane in memo
    assert "Top routes (PRELIMINARY" in memo
    summary = status_summary(run_dir)
    assert summary["routes_preliminary"] is True
    assert summary["evidence"]["reference_supported_steps"] == 0


def test_running_pressure_lane_is_visible_and_not_evidence(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    from espresso_proof.kernel.agent_run_status import set_agent_status

    set_agent_status(run_dir, "examples", "running", increment_attempt=True)
    lane = _by_stage(run_dir)["examples"]

    assert lane["status"] == "running"
    assert lane["counts_as_evidence"] is False
    assert lane["evidence_note"] == "in progress; not yet evidence"
    from espresso_proof.orchestration.agent_flow_status import write_stage_status_projection

    write_stage_status_projection(run_dir)
    stage_status = read_yaml(run_dir / "stage_status.yaml")
    by_stage = {entry["stage_id"]: entry for entry in stage_status["stages"]}
    assert by_stage["examples"]["started_at"]
    assert by_stage["examples"]["finished_at"] is None


def test_terminal_stage_status_pressure_lanes_remain_visible_without_agent_run(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    stage_status = read_yaml(run_dir / "stage_status.yaml")
    by_stage = {entry["stage_id"]: entry for entry in stage_status["stages"]}
    generated_at = stage_status["generated_at"]
    by_stage["literature_review"].update(
        {
            "status": "blocked",
            "status_reason": "source provider disabled",
            "finished_at": generated_at,
        }
    )
    by_stage["examples"].update(
        {
            "status": "skipped",
            "status_reason": "lane deferred by policy",
            "finished_at": generated_at,
        }
    )
    by_stage["counterexamples"].update(
        {
            "status": "failed",
            "status_reason": "coordinator validation failed",
            "error_summary": "simulated validation failure",
            "finished_at": generated_at,
        }
    )
    write_yaml(run_dir / "stage_status.yaml", stage_status)
    validate_run(run_dir)

    lanes = _by_stage(run_dir)

    assert lanes["literature_review"]["status"] == "blocked"
    assert lanes["examples"]["status"] == "skipped"
    assert lanes["counterexamples"]["status"] == "failed"
    assert {lane["agent_status"] for lane in lanes.values()} == {"not_run"}
    assert all(lane["counts_as_evidence"] is False for lane in lanes.values())
    assert "blocked; contributes no evidence" in lanes["literature_review"]["evidence_note"]
    assert lanes["examples"]["evidence_note"] == "skipped; contributes no evidence"
    assert lanes["counterexamples"]["evidence_note"] == (
        "failed; contributes no evidence until rerun"
    )


def test_kernel_invalid_stage_status_is_ignored_by_pressure_lane_view(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    stage_status = read_yaml(run_dir / "stage_status.yaml")
    by_stage = {entry["stage_id"]: entry for entry in stage_status["stages"]}
    examples = by_stage["examples"]
    examples["status"] = "done"
    examples["finished_at"] = stage_status["generated_at"]
    examples["artifact_hashes"]["examples.yaml"] = "missing"
    write_yaml(run_dir / "stage_status.yaml", stage_status)

    lane = _by_stage(run_dir)["examples"]

    assert lane["status"] == "not_started"
    assert lane["agent_status"] == "not_run"
    assert lane["counts_as_evidence"] is False
    assert lane["evidence_note"] == "pending; not counted as evidence"


def test_claim_clear_blocked_pressure_lanes_show_gate_reason(
    tmp_path: Path,
) -> None:
    run_dir = _unclear_run(tmp_path)
    validate_run(run_dir)

    lanes = _by_stage(run_dir)

    for stage_id in PRESSURE_LANE_STAGE_IDS:
        lane = lanes[stage_id]
        assert lane["status"] == "skipped"
        assert lane["claim_clear_blocked"] is True
        assert lane["counts_as_evidence"] is False
        assert lane["evidence_note"] == (
            "blocked by claim-clear gate; contributes no evidence"
        )
    memo = build_concise_memo(run_dir)
    assert "Literature Review: Skipped - blocked by claim-clear gate" in memo
    assert "Examples: Skipped - blocked by claim-clear gate" in memo
    assert "Counterexamples: Skipped - blocked by claim-clear gate" in memo


def test_done_stage_status_does_not_bypass_agent_evidence_gate(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)

    out = run_single_agent(run_dir, "examples", provider="stub")
    assert out["status"] == "done"
    from espresso_proof.kernel.agent_run_status import set_agent_status

    set_agent_status(run_dir, "examples", "not_run")
    validate_run(run_dir)

    lane = _by_stage(run_dir)["examples"]

    assert lane["status"] == "done"
    assert lane["status_word"] == "Done"
    assert lane["agent_status"] == "not_run"
    assert lane["counts_as_evidence"] is False
    assert lane["evidence_note"] == (
        "stage finished, but agent evidence gate is not done; contributes no evidence"
    )


def test_done_examples_lane_refreshes_without_running_other_lanes(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)

    out = run_single_agent(run_dir, "examples", provider="stub")

    assert out["status"] == "done"
    lanes = _by_stage(run_dir)
    assert lanes["examples"]["status"] == "done"
    assert lanes["examples"]["counts_as_evidence"] is True
    assert lanes["literature_review"]["status"] == "not_started"
    assert lanes["counterexamples"]["status"] == "not_started"
    assert lanes["literature_review"]["counts_as_evidence"] is False
    assert lanes["counterexamples"]["counts_as_evidence"] is False
    stage_status = read_yaml(run_dir / "stage_status.yaml")
    by_stage = {entry["stage_id"]: entry for entry in stage_status["stages"]}
    assert by_stage["examples"]["status"] == "done"
    assert by_stage["literature_review"]["status"] == "not_started"


def test_done_pressure_lane_without_artifact_is_not_evidence(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    from espresso_proof.kernel.agent_run_status import set_agent_status

    set_agent_status(run_dir, "examples", "done", increment_attempt=True)
    (run_dir / "examples.yaml").unlink()

    lane = _by_stage(run_dir)["examples"]

    assert lane["status"] == "done"
    assert lane["counts_as_evidence"] is False
    assert "artifact is unavailable" in lane["evidence_note"]


def test_explicit_batch_writes_process_only_pressure_lane_barrier(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)

    out = run_evidence_batch_auto(
        run_dir,
        ["literature", "examples", "counterexamples"],
        provider="stub",
        max_workers=2,
    )

    assert {result["status"] for result in out["agents"].values()} == {"done"}
    barrier = read_yaml(run_dir / "pressure_lane_barrier.yaml")
    assert barrier["barrier_state"] == "completed"
    assert barrier["effect"] == "scheduling_only"
    assert barrier["advisory_only"] is True
    assert barrier["commit_order"] == ["counterexamples", "examples", "literature"]
    assert {entry["status"] for entry in barrier["entries"]} == {"committed"}
    assert all(entry["worker_shared_write"] is False for entry in barrier["entries"])
    assert all(entry["candidate_persisted"] is False for entry in barrier["entries"])

    lanes = _by_stage(run_dir)
    assert lanes["examples"]["counts_as_evidence"] is True
    assert lanes["counterexamples"]["counts_as_evidence"] is True
    assert lanes["literature_review"]["counts_as_evidence"] is False


def test_pressure_lane_compute_failure_is_visible_and_not_evidence(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    seed_pressure_lane_barrier(run_dir, ["examples"])
    record_pressure_lane_barrier_compute(
        run_dir,
        {
            "examples": {
                "agent": "examples",
                "started_at": now_iso(),
                "duration_ms": 7,
                "error": "simulated worker compute failure",
            }
        },
    )
    validate_run(run_dir)

    lane = _by_stage(run_dir)["examples"]

    assert lane["status"] == "failed"
    assert lane["status_word"] == "Needs check"
    assert lane["agent_status"] == "not_run"
    assert lane["counts_as_evidence"] is False
    assert lane["evidence_note"] == (
        "worker compute failed; contributes no evidence until rerun"
    )


def test_pressure_lane_commit_failure_records_failed_not_done(
    tmp_path: Path,
    monkeypatch,
) -> None:
    run_dir = _run(tmp_path)

    def fail_commit(run_dir_arg, agent, **kwargs):
        return {"agent": agent, "status": "failed", "error": "simulated commit failure"}

    monkeypatch.setattr(ec, "run_single_agent", fail_commit)
    out = ec.run_evidence_batch_parallel(run_dir, ["literature"], max_workers=1)

    assert out["agents"]["literature"]["status"] == "failed"
    barrier = read_yaml(run_dir / "pressure_lane_barrier.yaml")
    assert barrier["barrier_state"] == "failed"
    [entry] = barrier["entries"]
    assert entry["agent"] == "literature"
    assert entry["status"] == "failed"
    assert agent_status(run_dir, "literature") != "done"
    lane = _by_stage(run_dir)["literature_review"]
    assert lane["status"] == "failed"
    assert lane["status_word"] == "Needs check"
    assert lane["counts_as_evidence"] is False
    assert lane["evidence_note"] == (
        "coordinator commit failed; contributes no evidence until rerun"
    )
    validate_run(run_dir)
