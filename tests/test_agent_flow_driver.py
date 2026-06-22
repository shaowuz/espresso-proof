from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.front_half.registry import FRONT_HALF_STAGES
from espresso_proof.kernel.io import read_yaml
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.agent_flow_driver import (
    build_agent_flow_run_plan,
    front_half_stages_for_runner,
    record_agent_flow_stage_status,
)
from espresso_proof.orchestration.agent_flow_legacy_map import (
    LEGACY_FRONT_HALF_FAST_ORDER,
    LEGACY_FRONT_HALF_FULL_ORDER,
    mapped_stage_ids,
)
from espresso_proof.orchestration.agent_flow_spec import ordered_stage_ids
from espresso_proof.orchestration.agent_flow_status import STAGE_STATUS_FILENAME
from espresso_proof.orchestration.agent_runner import run_single_agent
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.orchestration.orchestrator import FAST_CORE_STAGES, orchestrate_run

CLAIM = "Every group of order p^2, where p is prime, is abelian.\n"


def _claim(tmp_path: Path, text: str = CLAIM) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(text, encoding="utf-8")
    return path


def _stage(report: dict, stage_id: str) -> dict:
    return next(stage for stage in report["stages"] if stage["stage_id"] == stage_id)


def test_driver_plan_matches_spec_and_front_half_runtime_order() -> None:
    full = build_agent_flow_run_plan(fast=False)
    fast = build_agent_flow_run_plan(fast=True)

    assert full.conceptual_stage_ids == ordered_stage_ids() == mapped_stage_ids()
    assert full.front_half_stages == tuple(FRONT_HALF_STAGES) == LEGACY_FRONT_HALF_FULL_ORDER
    assert fast.front_half_stages == tuple(FAST_CORE_STAGES) == LEGACY_FRONT_HALF_FAST_ORDER
    assert front_half_stages_for_runner(fast=False) == FRONT_HALF_STAGES
    assert front_half_stages_for_runner(fast=True) == FAST_CORE_STAGES
    assert fast.deferred_front_half_stages == (
        "literature_review",
        "examples",
        "counterexamples",
        "check_generator",
        "attacker",
    )


def test_driver_records_runtime_stage_status_as_hashless_process_projection(
    tmp_path: Path,
) -> None:
    run_dir = run_mock_critique("agent_flow_driver", _claim(tmp_path), tmp_path)

    report = record_agent_flow_stage_status(run_dir)

    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["stage_status"] == STAGE_STATUS_FILENAME
    assert (run_dir / STAGE_STATUS_FILENAME).exists()
    assert report["advisory_only"] is True
    assert report["effect"] == "process_status_only"
    assert all(stage["artifact_hashes"] == {} for stage in report["stages"])


def test_driver_stage_status_write_rolls_back_on_validation_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from espresso_proof.kernel.stage_runner import StageError
    from espresso_proof.orchestration import agent_flow_driver as driver

    run_dir = run_mock_critique("agent_flow_driver", _claim(tmp_path), tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "stage_status" not in manifest.get("optional_artifacts", {})

    def fail_after_stage_status_write(path: Path) -> None:
        assert (Path(path) / STAGE_STATUS_FILENAME).exists()
        raise RuntimeError("injected stage-status validation failure")

    monkeypatch.setattr(driver, "validate_run", fail_after_stage_status_write)

    with pytest.raises(StageError, match="agent_flow_stage_status"):
        record_agent_flow_stage_status(run_dir)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "stage_status" not in manifest.get("optional_artifacts", {})
    assert not (run_dir / STAGE_STATUS_FILENAME).exists()


def test_orchestrator_uses_driver_plan_and_persists_stage_status(
    tmp_path: Path,
) -> None:
    result = orchestrate_run(
        project="agent_flow_driver",
        input_path=_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    run_dir = Path(result["run_dir"])

    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["stage_status"] == STAGE_STATUS_FILENAME
    front_half_run = read_yaml(run_dir / "front_half_run.yaml")
    assert [stage["stage"] for stage in front_half_run["stages"]] == FAST_CORE_STAGES

    stage_status = read_yaml(run_dir / STAGE_STATUS_FILENAME)
    assert _stage(stage_status, "literature_review")["status"] == "not_started"
    assert _stage(stage_status, "examples")["status"] == "not_started"
    assert _stage(stage_status, "counterexamples")["status"] == "not_started"
    assert _stage(stage_status, "time_allocation")["status"] == "done"
    assert _stage(stage_status, "prover")["status"] == "done"
    assert _stage(stage_status, "reviewers")["status"] == "done"
    assert all(stage["artifact_hashes"] == {} for stage in stage_status["stages"])


def test_on_demand_agent_refreshes_persisted_stage_status(tmp_path: Path) -> None:
    result = orchestrate_run(
        project="agent_flow_driver",
        input_path=_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    run_dir = Path(result["run_dir"])

    out = run_single_agent(run_dir, "examples", provider="stub")

    assert out["status"] == "done"
    validate_run(run_dir)
    stage_status = read_yaml(run_dir / STAGE_STATUS_FILENAME)
    assert _stage(stage_status, "examples")["status"] == "done"
    assert _stage(stage_status, "literature_review")["status"] == "not_started"


def test_on_demand_agent_surfaces_running_stage_status_before_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from espresso_proof.orchestration import agent_runner

    result = orchestrate_run(
        project="agent_flow_driver",
        input_path=_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    run_dir = Path(result["run_dir"])
    original_body = agent_runner._run_agent_body
    observed: list[dict] = []

    def observe_running_stage_status(
        run_dir_arg: Path,
        agent: str,
        *,
        client,
        candidate=None,
    ) -> None:
        stage_status = read_yaml(Path(run_dir_arg) / STAGE_STATUS_FILENAME)
        examples = _stage(stage_status, "examples")
        observed.append(examples)
        original_body(
            run_dir_arg,
            agent,
            client=client,
            candidate=candidate,
        )

    monkeypatch.setattr(agent_runner, "_run_agent_body", observe_running_stage_status)

    out = agent_runner.run_single_agent(run_dir, "examples", provider="stub")

    assert out["status"] == "done"
    assert len(observed) == 1
    assert observed[0]["status"] == "running"
    assert observed[0]["started_at"]
    assert observed[0]["finished_at"] is None
    validate_run(run_dir)


def test_claim_clear_gate_recording_rolls_back_on_validation_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from espresso_proof.orchestration import orchestrator as orch
    from espresso_proof.kernel.stage_runner import StageError

    original_validate = orch.validate_run
    seen_gate_validation = False

    def fail_after_claim_gate_write(run_dir: Path) -> None:
        nonlocal seen_gate_validation
        original_validate(run_dir)
        if (Path(run_dir) / "claim_clear_gate.yaml").exists():
            seen_gate_validation = True
            raise RuntimeError("injected claim-clear validation failure")

    monkeypatch.setattr(orch, "validate_run", fail_after_claim_gate_write)

    with pytest.raises(StageError, match="claim_clear_gate"):
        orchestrate_run(
            project="agent_flow_driver",
            input_path=_claim(tmp_path),
            output_root=tmp_path,
            provider="stub",
            front_half="stub",
            fast=True,
        )

    assert seen_gate_validation is True
    run_dir = next(tmp_path.rglob("run_manifest.yaml")).parent
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["current_state"] == "FRONT_HALF_DONE"
    assert "claim_clear_gate" not in manifest.get("optional_artifacts", {})
    assert not (run_dir / "claim_clear_gate.yaml").exists()
