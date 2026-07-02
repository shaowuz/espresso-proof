from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.agent_run_status import agent_status, init_agent_run_status
from espresso_proof.kernel.failure_pattern_bank import audit_failure_patterns
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import StageStatusReport
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.agent_flow_status import (
    STAGE_STATUS_FILENAME,
    load_stage_status_projection,
    write_stage_status_projection,
)
from espresso_proof.kernel.agent_flow_spec import ordered_stage_ids
from espresso_proof.orchestration.mock_pipeline import run_mock_critique

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _run(tmp_path: Path) -> Path:
    return run_mock_critique(
        "stage_status",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )


def test_legacy_run_without_stage_status_gets_synthetic_projection(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "stage_status" not in manifest.get("optional_artifacts", {})

    report = load_stage_status_projection(run_dir)

    assert not (run_dir / STAGE_STATUS_FILENAME).exists()
    assert report.run_id == manifest["run_id"]
    assert tuple(stage.stage_id for stage in report.stages) == ordered_stage_ids()
    assert all(stage.advisory_only for stage in report.stages)
    validate_run(run_dir)


def test_write_stage_status_projection_registers_optional_artifact(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)

    report = write_stage_status_projection(run_dir)

    StageStatusReport.model_validate(report)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["stage_status"] == STAGE_STATUS_FILENAME
    assert (run_dir / STAGE_STATUS_FILENAME).exists()
    validate_run(run_dir)


def test_pending_human_choice_is_not_projected_done(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)

    report = write_stage_status_projection(run_dir)
    stages = {entry["stage_id"]: entry for entry in report["stages"]}

    assert stages["human_strategy_preference"]["status"] == "queued"
    assert "waiting for human strategy preference" in stages["human_strategy_preference"]["status_reason"]
    validate_run(run_dir)


def test_rewriting_stage_status_does_not_hash_the_projection_itself(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    write_stage_status_projection(run_dir)

    report = write_stage_status_projection(run_dir)

    assert all(
        STAGE_STATUS_FILENAME not in entry["artifact_hashes"]
        for entry in report["stages"]
    )
    validate_run(run_dir)


def test_stage_status_projection_is_process_only(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = write_stage_status_projection(run_dir)

    assert report["advisory_only"] is True
    assert report["effect"] == "process_status_only"
    assert all(entry["advisory_only"] is True for entry in report["stages"])
    assert all("proof_status" not in entry for entry in report["stages"])
    assert all("source_backed" not in entry for entry in report["stages"])


def test_legacy_projection_withholds_stages_until_dependencies_are_done(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    report = write_stage_status_projection(run_dir)
    stages = {entry["stage_id"]: entry for entry in report["stages"]}

    assert stages["prover"]["status"] == "not_started"
    assert stages["reviewers"]["status"] == "not_started"
    assert "prover" in stages["reviewers"]["status_reason"]
    assert stages["line_numbered_proof_writer"]["status"] == "not_started"
    assert stages["agent_consensus_seal"]["status"] == "not_started"
    assert "line_numbered_proof_writer" in stages["agent_consensus_seal"]["status_reason"]


def test_legacy_grade_artifacts_are_blocked_not_invisible_when_dependencies_pending(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)

    report = write_stage_status_projection(run_dir)
    stages = {entry["stage_id"]: entry for entry in report["stages"]}

    assert stages["grade"]["status"] == "blocked"
    assert "legacy artifact exists" in stages["grade"]["status_reason"]
    assert "failure_classifier" in stages["grade"]["status_reason"]
    assert stages["concise_answer"]["status"] == "blocked"
    assert "legacy artifact exists" in stages["concise_answer"]["status_reason"]
    assert "grade" in stages["concise_answer"]["status_reason"]
    validate_run(run_dir)


def test_failure_classifier_artifact_is_not_shadowed_by_not_run_board(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    write_yaml(run_dir / "llm_agent_run.yaml", {"projection_fixture": True})
    init_agent_run_status(run_dir, done={"claim_understanding"})
    assert agent_status(run_dir, "failure_patterns") == "not_run"

    audit_failure_patterns(run_dir)
    report = write_stage_status_projection(run_dir)
    stages = {entry["stage_id"]: entry for entry in report["stages"]}

    assert stages["prover"]["status"] == "done"
    assert stages["reviewers"]["status"] == "done"
    assert stages["failure_classifier"]["status"] == "done"
    assert stages["grade"]["status"] == "done"
    assert stages["concise_answer"]["status"] == "done"
    assert agent_status(run_dir, "failure_patterns") == "not_run"
    validate_run(run_dir)
