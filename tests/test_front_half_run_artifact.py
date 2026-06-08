"""Phase 03 — durable front_half_run.yaml audit artifact.

Covers: stub success, mock all-skipped, failed-stage recorded, resume, repeated
run (no duplicate entries), invalid stage name, path escape, status-like injected
field rejection, and old-run compatibility.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.front_half_agents import write_front_half_run
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.models import FrontHalfRunReport
from espresso_proof.orchestrator import orchestrate_run, resume_run
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validation.front_half import validate_front_half_run
from espresso_proof.validators import validate_run

_FORBIDDEN_FIELDS = (
    "source_backed",
    "checked",
    "proved",
    "human_confirmed",
    "refutes_claim",
    "lean_checked",
    "proof_status",
)


def _claim(tmp_path: Path) -> Path:
    p = tmp_path / "claim.md"
    p.write_text("Let p be a prime. Every group G of order p^2 is abelian.\n", encoding="utf-8")
    return p


def _run(tmp_path: Path, *, front_half: str = "stub") -> Path:
    return orchestrate_run(
        project="fh",
        input_path=_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half=front_half,
    )["run_dir"]


def _read(run_dir: Path) -> dict:
    return read_yaml(run_dir / "front_half_run.yaml")


def test_stub_front_half_writes_registered_valid_artifact(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["front_half_run"] == "front_half_run.yaml"
    report = _read(run_dir)
    FrontHalfRunReport.model_validate(report)
    validate_run(run_dir)
    validate_front_half_run(run_dir)
    assert all(s["status"] == "ok" for s in report["stages"])
    # process evidence only — no status-upgrade fields anywhere
    for field in _FORBIDDEN_FIELDS:
        assert field not in report
        assert all(field not in s for s in report["stages"])


def test_mock_mode_records_all_stages_skipped(tmp_path: Path) -> None:
    run_dir = _run(tmp_path, front_half="mock")
    report = _read(run_dir)
    assert report["stages"]  # not dropped
    assert all(s["status"] == "skipped" and s["validation"] == "not_run" for s in report["stages"])


def test_failed_stage_recorded_not_dropped(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _read(run_dir)
    # an error stage that is durably recorded with validation=error is admissible
    report["stages"][0] = {
        "stage": report["stages"][0]["stage"],
        "status": "error",
        "validation": "error",
        "errors": ["normalizer blew up"],
        "unsafe_language": [],
    }
    write_yaml(run_dir / "front_half_run.yaml", report)
    validate_front_half_run(run_dir)  # recorded failure is fine
    # ...but an error stage masquerading as a passing validation is rejected
    report["stages"][0]["validation"] = "ok"
    write_yaml(run_dir / "front_half_run.yaml", report)
    with pytest.raises((KernelValidationError, ValueError)):
        validate_front_half_run(run_dir)


def test_resume_keeps_front_half_artifact_valid(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    brief = read_yaml(run_dir / "strategy_brief.yaml")
    resume_run(run_dir=run_dir, selected_strategy=brief["top_strategies"][0]["id"], effort="quick_test")
    assert (run_dir / "front_half_run.yaml").exists()
    validate_run(run_dir)
    validate_front_half_run(run_dir)


def test_repeated_write_does_not_duplicate_entries(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    before = _read(run_dir)["stages"]
    # rewriting (idempotent) keeps one entry per stage, not appended
    write_front_half_run(run_dir, pass_report=None, provider="mock", model="m")
    after = _read(run_dir)["stages"]
    assert len(after) == len({s["stage"] for s in after})
    assert len(before) == len({s["stage"] for s in before})


def test_invalid_stage_name_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report = _read(run_dir)
    report["stages"].append(
        {"stage": "not_a_real_stage", "status": "ok", "validation": "ok", "errors": [], "unsafe_language": []}
    )
    write_yaml(run_dir / "front_half_run.yaml", report)
    with pytest.raises(KernelValidationError):
        validate_front_half_run(run_dir)


def test_artifact_path_escape_rejected() -> None:
    bad = {
        "schema_version": 1,
        "claim_id": "claim_001",
        "run_id": "run_x",
        "generated_at": "2026-06-07T00:00:00+00:00",
        "provider": "stub",
        "model": "m",
        "stages": [
            {
                "stage": "understanding_claim",
                "status": "ok",
                "validation": "ok",
                "output_artifacts": ["../escape.yaml"],
                "errors": [],
                "unsafe_language": [],
            }
        ],
    }
    with pytest.raises(Exception):
        FrontHalfRunReport.model_validate(bad)


@pytest.mark.parametrize("field", _FORBIDDEN_FIELDS)
def test_status_like_injected_field_rejected(field: str) -> None:
    base = {
        "schema_version": 1,
        "claim_id": "claim_001",
        "run_id": "run_x",
        "generated_at": "2026-06-07T00:00:00+00:00",
        "provider": "stub",
        "model": "m",
        "stages": [{"stage": "understanding_claim", "status": "ok", "validation": "ok"}],
        field: True,  # status-upgrade smuggling at the top level
    }
    with pytest.raises(Exception):
        FrontHalfRunReport.model_validate(base)


def test_old_run_without_artifact_still_validates(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    # simulate an old run that predates this optional artifact
    (run_dir / "front_half_run.yaml").unlink()
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    del manifest["optional_artifacts"]["front_half_run"]
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    validate_run(run_dir)
    validate_front_half_run(run_dir)  # no-op when unregistered
