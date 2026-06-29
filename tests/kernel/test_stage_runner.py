"""Phase 02 — StageRunner transaction / rollback tests.

Fault injection around artifact write, manifest write, validation, state
transition, plus path-escape rejection and idempotent recovery. The runner must
restore the last valid state on failure, never advance lifecycle state on
failure, never leave the manifest pointing at a dirty optional artifact, and
never delete files outside the run directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.orchestrator import orchestrate_run
from espresso_proof.kernel.stage_runner import StageError, StageRunner
from espresso_proof.kernel.validators import validate_run


def _valid_run(tmp_path: Path) -> Path:
    claim = tmp_path / "claim.md"
    claim.write_text("Let p be a prime. Every group G of order p^2 is abelian.\n", encoding="utf-8")
    result = orchestrate_run(
        project="stage_runner",
        input_path=claim,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
    )
    return result["run_dir"]


def _state(run_dir: Path) -> str:
    return read_yaml(run_dir / "run_manifest.yaml")["current_state"]


def test_validation_failure_rolls_back_and_does_not_advance(tmp_path: Path) -> None:
    run_dir = _valid_run(tmp_path)
    state_before = _state(run_dir)
    claim_path = run_dir / "locked_claim.yaml"
    original = claim_path.read_bytes()
    runner = StageRunner(run_dir)

    def mutate() -> None:
        claim_path.write_text("not: [valid, claim\n", encoding="utf-8")  # corrupt

    with pytest.raises(StageError):
        runner.run(
            name="bad",
            mutate=mutate,
            validate=lambda: validate_run(run_dir),
            state_after="COMPLETED_MEMO",
        )

    assert claim_path.read_bytes() == original  # restored
    assert _state(run_dir) == state_before  # not advanced
    assert runner.outcomes[-1].status == "rolled_back"
    assert runner.outcomes[-1].error  # failure context recorded, not swallowed
    validate_run(run_dir)  # run is still valid


def test_crash_after_artifact_write_drops_new_file(tmp_path: Path) -> None:
    run_dir = _valid_run(tmp_path)
    new_file = run_dir / "scratch_new.yaml"
    runner = StageRunner(run_dir)

    def mutate() -> None:
        write_yaml(new_file, {"x": 1})
        raise RuntimeError("crash after write")

    with pytest.raises(StageError):
        runner.run(name="crashy", mutate=mutate, validate=lambda: None)

    assert not new_file.exists()  # whole-run snapshot drops the new file


def test_crash_after_manifest_write_clears_dirty_optional_artifact(tmp_path: Path) -> None:
    run_dir = _valid_run(tmp_path)
    runner = StageRunner(run_dir)

    def mutate() -> None:
        write_yaml(run_dir / "bogus.yaml", {"k": "v"})
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        manifest.setdefault("optional_artifacts", {})["bogus"] = "bogus.yaml"
        write_yaml(run_dir / "run_manifest.yaml", manifest)
        raise RuntimeError("crash after manifest write")

    with pytest.raises(StageError):
        runner.run(name="manifesty", mutate=mutate, validate=lambda: None)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "bogus" not in manifest.get("optional_artifacts", {})  # no dirty pointer
    assert not (run_dir / "bogus.yaml").exists()


def test_crash_before_state_advance_keeps_state(tmp_path: Path) -> None:
    run_dir = _valid_run(tmp_path)
    state_before = _state(run_dir)
    runner = StageRunner(run_dir)

    with pytest.raises(StageError):
        runner.run(
            name="validate_boom",
            mutate=lambda: None,
            validate=lambda: (_ for _ in ()).throw(RuntimeError("validation boom")),
            state_after="COMPLETED_MEMO",
        )

    assert _state(run_dir) == state_before


def test_path_escape_rejected(tmp_path: Path) -> None:
    run_dir = _valid_run(tmp_path)
    runner = StageRunner(run_dir)
    with pytest.raises(ValueError, match="escapes run directory"):
        runner.run(
            name="escape",
            mutate=lambda: None,
            validate=lambda: None,
            snapshot_paths=["../outside.yaml"],
        )


def test_success_advances_state_and_records_ok(tmp_path: Path) -> None:
    run_dir = _valid_run(tmp_path)
    runner = StageRunner(run_dir)
    marker = run_dir / "marker.txt"

    outcome = runner.run(
        name="ok_stage",
        mutate=lambda: marker.write_text("hi", encoding="utf-8"),
        validate=lambda: None,
        state_after="COMPLETED_MEMO",
    )
    assert outcome.status == "ok"
    assert outcome.state_after == "COMPLETED_MEMO"
    assert _state(run_dir) == "COMPLETED_MEMO"
    assert marker.exists()


def test_idempotent_recovery_after_failed_stage(tmp_path: Path) -> None:
    run_dir = _valid_run(tmp_path)
    state_before = _state(run_dir)
    runner = StageRunner(run_dir)

    with pytest.raises(StageError):
        runner.run(
            name="first_try",
            mutate=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            validate=lambda: None,
            state_after="COMPLETED_MEMO",
        )
    assert _state(run_dir) == state_before  # recovered to last valid state

    # re-running the stage successfully now advances from that valid state
    outcome = runner.run(
        name="second_try",
        mutate=lambda: None,
        validate=lambda: validate_run(run_dir),
        state_after="COMPLETED_MEMO",
    )
    assert outcome.status == "ok"
    assert _state(run_dir) == "COMPLETED_MEMO"
