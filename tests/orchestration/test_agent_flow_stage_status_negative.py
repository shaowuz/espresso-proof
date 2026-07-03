from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.agent_flow_status import (
    STAGE_STATUS_FILENAME,
    load_stage_status_projection,
    write_stage_status_projection,
)
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.validation.errors import KernelValidationError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _run(tmp_path: Path) -> Path:
    return run_mock_critique(
        "stage_status_negative",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )


def _write_status(run_dir: Path) -> dict:
    write_stage_status_projection(run_dir)
    return read_yaml(run_dir / STAGE_STATUS_FILENAME)


def _stage(report: dict, stage_id: str) -> dict:
    return next(stage for stage in report["stages"] if stage["stage_id"] == stage_id)


def test_stage_status_must_be_optional_artifact(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_status(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest["artifacts"]["stage_status"] = manifest["optional_artifacts"].pop("stage_status")
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    with pytest.raises(KernelValidationError, match="stage_status must be an optional"):
        validate_run(run_dir)


def test_load_stage_status_rejects_noncanonical_manifest_path(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_status(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest["optional_artifacts"]["stage_status"] = "nested/stage_status.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    with pytest.raises(KernelValidationError, match="stage_status.yaml"):
        load_stage_status_projection(run_dir)


def test_validate_run_rejects_missing_registered_stage_status(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_status(run_dir)
    (run_dir / STAGE_STATUS_FILENAME).unlink()

    with pytest.raises(KernelValidationError, match="optional_artifacts.stage_status"):
        validate_run(run_dir)


def test_load_stage_status_rejects_missing_registered_stage_status(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_status(run_dir)
    (run_dir / STAGE_STATUS_FILENAME).unlink()

    with pytest.raises(KernelValidationError, match="stage_status optional artifact is missing"):
        load_stage_status_projection(run_dir)


def test_stage_status_claim_id_must_match_locked_claim(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    report["claim_id"] = "claim-from-a-different-run"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="claim_id does not match"):
        validate_run(run_dir)


def test_stage_status_run_id_must_match_manifest(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    report["run_id"] = "run-from-a-different-run"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="run_id does not match"):
        validate_run(run_dir)


def test_stage_status_report_must_stay_advisory(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    report["advisory_only"] = False
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="schema validation failed"):
        validate_run(run_dir)


def test_stage_status_effect_must_stay_process_only(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    report["effect"] = "proof_status"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="schema validation failed"):
        validate_run(run_dir)


def test_unknown_stage_id_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    report["stages"][3]["stage_id"] = "made_up_stage"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="canonical order"):
        validate_run(run_dir)


def test_unknown_dependency_reference_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    _stage(report, "strategy_generator")["depends_on"].append("ghost_stage")
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(
        KernelValidationError,
        match="depends_on references unknown AGENT_FLOW stage",
    ):
        validate_run(run_dir)


def test_unknown_read_reference_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    _stage(report, "strategy_generator")["reads_from"].append("ghost_stage")
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(
        KernelValidationError,
        match="reads_from references unknown AGENT_FLOW stage",
    ):
        validate_run(run_dir)


def test_status_smuggling_field_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    _stage(report, "strategy_generator")["proof_status"] = "proved"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="schema validation failed"):
        validate_run(run_dir)


def test_status_smuggling_text_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    _stage(report, "strategy_generator")["status_reason"] = "verified proof complete"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="mathematical status fields"):
        validate_run(run_dir)


def test_windows_output_path_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    _stage(report, "strategy_generator")["outputs"].append("C:/escape.yaml")
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="run-relative"):
        validate_run(run_dir)


def test_windows_artifact_hash_path_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    _stage(report, "strategy_generator")["artifact_hashes"][
        "nested\\strategy.yaml"
    ] = "missing"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="POSIX"):
        validate_run(run_dir)


def test_unmapped_artifact_hash_path_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    _stage(report, "strategy_generator")["artifact_hashes"][
        "final_report.md"
    ] = "missing"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="not mapped to the stage"):
        validate_run(run_dir)


def test_done_stage_with_missing_artifact_hash_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    entry = _stage(report, "strategy_generator")
    entry["status"] = "done"
    entry["artifact_hashes"]["strategy_synthesis.yaml"] = "missing"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="done but artifact"):
        validate_run(run_dir)


def test_stale_artifact_hash_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_status(run_dir)
    (run_dir / "input.md").write_text(
        "A different input after stage_status was recorded.\n",
        encoding="utf-8",
    )

    with pytest.raises(KernelValidationError, match="artifact hash is stale"):
        validate_run(run_dir)


def test_claim_clear_blocked_downstream_done_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    _stage(report, "literature_review")["claim_clear_blocked"] = True
    _stage(report, "literature_review")["status"] = "done"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="claim_clear_blocked"):
        validate_run(run_dir)


def test_claim_clear_blocked_stage_cannot_be_manually_marked_done(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    literature = _stage(report, "literature_review")
    literature["claim_clear_blocked"] = True
    literature["status"] = "skipped"
    literature["finished_at"] = report["generated_at"]
    literature["status_reason"] = "blocked by claim-clear gate"
    _stage(report, "strategy_generator")["status"] = "done"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="cannot run while claim-clear is blocked"):
        validate_run(run_dir)


def test_time_allocation_cannot_start_before_strategy_grader_done(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    _stage(report, "strategy_grader")["status"] = "skipped"
    time_allocation = _stage(report, "time_allocation")
    time_allocation["status"] = "done"
    time_allocation["finished_at"] = report["generated_at"]
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="strategy_grader is done"):
        validate_run(run_dir)


def test_running_stage_status_requires_started_at(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    entry = _stage(report, "strategy_generator")
    entry["status"] = "running"
    entry["started_at"] = None
    entry["finished_at"] = None
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="running stage_status entry requires started_at"):
        validate_run(run_dir)


@pytest.mark.parametrize("status", ["not_started", "queued"])
def test_inactive_stage_status_cannot_have_started_at(
    tmp_path: Path,
    status: str,
) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    entry = _stage(report, "strategy_generator")
    entry["status"] = status
    entry["started_at"] = report["generated_at"]
    entry["finished_at"] = None
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(
        KernelValidationError,
        match=f"{status} stage_status entry cannot have started_at",
    ):
        validate_run(run_dir)


def test_stage_status_timestamps_must_be_iso8601(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    entry = _stage(report, "strategy_generator")
    entry["status"] = "running"
    entry["started_at"] = "not a timestamp"
    entry["finished_at"] = None
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="started_at must be ISO 8601"):
        validate_run(run_dir)


def test_stage_status_finished_at_cannot_precede_started_at(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    entry = _stage(report, "strategy_generator")
    entry["status"] = "done"
    entry["started_at"] = "2030-01-01T00:00:00+00:00"
    entry["finished_at"] = "2029-01-01T00:00:00+00:00"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="finished_at cannot be before started_at"):
        validate_run(run_dir)


def test_stage_status_generated_at_must_be_iso8601(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    report["generated_at"] = "not a timestamp"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="generated_at must be ISO 8601"):
        validate_run(run_dir)


def test_stage_status_cannot_mark_stage_done_before_dependency_done(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    report = _write_status(run_dir)
    reviewers = _stage(report, "reviewers")
    reviewers["status"] = "done"
    reviewers["finished_at"] = report["generated_at"]
    reviewers["status_reason"] = "manual override"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)

    with pytest.raises(KernelValidationError, match="before dependency prover is done"):
        validate_run(run_dir)


# ---------------------------------------------------------------------------
# PR1a hard-negatives: logs are never evidence, and the projection honors the
# same dependency rule the kernel validator enforces.
# ---------------------------------------------------------------------------

from espresso_proof.kernel.observability import append_agent_event
from espresso_proof.orchestration.agent_flow_status import (
    _agent_event_stage_projection,
    build_stage_status_projection,
)


def _writer_entry(report: dict) -> dict:
    return next(
        s for s in report["stages"] if s["stage_id"] == "line_numbered_proof_writer"
    )


def test_finished_event_without_artifact_cannot_upgrade_stage(tmp_path: Path) -> None:
    # [hard-negative] a finished log line with no artifact must NOT move the
    # stage: done is decided by artifact existence alone (logs are not
    # evidence).
    assert (
        _agent_event_stage_projection(tmp_path, "line_numbered_proof_writer", "2026-07-02T00:02:00+00:00")
        is None
        or True
    )
    append_agent_event(
        tmp_path,
        agent_name="line_numbered_proof_writer",
        provider="external",
        adapter="",
        stage="line_numbered_proof_writer",
        agent_status="started",
        started_at="2026-07-02T00:00:00+00:00",
    )
    append_agent_event(
        tmp_path,
        agent_name="line_numbered_proof_writer",
        provider="external",
        adapter="",
        stage="line_numbered_proof_writer",
        agent_status="finished",
        finished_at="2026-07-02T00:01:00+00:00",
    )
    assert (
        _agent_event_stage_projection(
            tmp_path, "line_numbered_proof_writer", "2026-07-02T00:02:00+00:00"
        )
        is None
    )


def test_other_stage_events_do_not_move_this_stage(tmp_path: Path) -> None:
    append_agent_event(
        tmp_path,
        agent_name="proof_consensus_line_proof",
        provider="external",
        adapter="",
        stage="agent_consensus_seal",
        agent_status="failed",
        finished_at="2026-07-02T00:01:00+00:00",
        error_summary="boom",
    )
    assert (
        _agent_event_stage_projection(
            tmp_path, "line_numbered_proof_writer", "2026-07-02T00:02:00+00:00"
        )
        is None
    )


def test_projection_withheld_when_dependencies_not_done(tmp_path: Path) -> None:
    # [hard-negative] the mock run's prover stage is NOT done, so a stray
    # writer failure event must stay invisible in stage_status (the kernel
    # validator forbids failed-before-dependencies; the projection honors the
    # same rule) while remaining on the observability record.
    run_dir = _run(tmp_path)
    append_agent_event(
        run_dir,
        agent_name="line_numbered_proof_writer",
        provider="external",
        adapter="",
        stage="line_numbered_proof_writer",
        agent_status="failed",
        finished_at="2026-07-02T00:01:00+00:00",
        error_summary="boom",
    )
    report = build_stage_status_projection(run_dir, include_artifact_hashes=False)
    assert _writer_entry(report)["status"] == "not_started"
