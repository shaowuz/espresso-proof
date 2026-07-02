from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validation.front_half import validate_front_half_run
from espresso_proof.kernel.validators import validate_run


FIXTURES = Path("tests/fixtures")


def _mock_run(tmp_path: Path, fixture_name: str = "promising_but_unproved") -> Path:
    return run_mock_critique(
        "hodge_atoms",
        FIXTURES / fixture_name / "input.md",
        tmp_path,
    )


def test_manifest_rejects_posix_parent_traversal(tmp_path: Path) -> None:
    run_dir = _mock_run(tmp_path)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest["artifacts"]["locked_claim"] = "../locked_claim.yaml"
    write_yaml(manifest_path, manifest)

    with pytest.raises(KernelValidationError, match="cannot contain"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "promising_but_unproved",
        "hodge_all_cubic_fourfolds",
    ],
)
def test_manifest_rejects_windows_parent_traversal_on_posix(
    tmp_path: Path,
    fixture_name: str,
) -> None:
    run_dir = _mock_run(tmp_path, fixture_name)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    raw_path = r"..\locked_claim.yaml"
    (run_dir / raw_path).write_text(
        (run_dir / "locked_claim.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest["artifacts"]["locked_claim"] = raw_path
    write_yaml(manifest_path, manifest)

    with pytest.raises(KernelValidationError, match="cannot contain"):
        validate_run(run_dir)


def test_manifest_rejects_windows_absolute_and_drive_paths(tmp_path: Path) -> None:
    run_dir = _mock_run(tmp_path)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest["artifacts"]["locked_claim"] = r"C:\espresso\locked_claim.yaml"
    write_yaml(manifest_path, manifest)

    with pytest.raises(KernelValidationError, match="relative"):
        validate_run(run_dir)


def test_manifest_rejects_windows_optional_artifact_path_on_posix(tmp_path: Path) -> None:
    run_dir = _mock_run(tmp_path)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    raw_path = r"..\optional_probe.yaml"
    (run_dir / raw_path).write_text("schema_version: 1\n", encoding="utf-8")
    manifest.setdefault("optional_artifacts", {})["optional_probe"] = raw_path
    write_yaml(manifest_path, manifest)

    with pytest.raises(KernelValidationError, match="cannot contain"):
        validate_run(run_dir)


def _run_llm_agents(run_dir: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-llm-agents",
            "--run",
            str(run_dir),
            "--provider",
            "stub",
            "--reviewer-set",
            "espresso",
        ],
    )
    assert result.exit_code == 0, result.output


def test_prover_round_index_rejects_windows_parent_traversal_on_posix(tmp_path: Path) -> None:
    run_dir = _mock_run(tmp_path)
    _run_llm_agents(run_dir)

    index_path = run_dir / "prover_rounds" / "index.yaml"
    index = read_yaml(index_path)
    first_path = run_dir / index["rounds"][0]["artifact_path"]
    raw_path = r"prover_rounds\..\round_copy.yaml"
    (run_dir / raw_path).write_text(first_path.read_text(encoding="utf-8"), encoding="utf-8")
    index["rounds"][0]["artifact_path"] = raw_path
    write_yaml(index_path, index)

    with pytest.raises(KernelValidationError, match="cannot contain"):
        validate_run(run_dir)


def test_prover_round_input_artifacts_reject_windows_backslash_path(tmp_path: Path) -> None:
    run_dir = _mock_run(tmp_path)
    _run_llm_agents(run_dir)

    index = read_yaml(run_dir / "prover_rounds" / "index.yaml")
    first_path = run_dir / index["rounds"][0]["artifact_path"]
    round_report = read_yaml(first_path)
    round_report["input_artifacts"] = ["locked_claim.yaml", r"nested\artifact.yaml"]
    write_yaml(first_path, round_report)

    with pytest.raises(KernelValidationError, match="separators"):
        validate_run(run_dir)


def test_front_half_run_rejects_windows_parent_traversal_on_posix(tmp_path: Path) -> None:
    run_dir = _mock_run(tmp_path)
    report = {
        "schema_version": 1,
        "claim_id": read_yaml(run_dir / "locked_claim.yaml")["id"],
        "run_id": read_yaml(run_dir / "run_manifest.yaml")["run_id"],
        "generated_at": "2026-06-14T00:00:00+00:00",
        "provider": "stub",
        "model": "stub",
        "stages": [
            {
                "stage": "understanding_claim",
                "status": "ok",
                "validation": "ok",
                "input_artifacts": [r"front_half\..\locked_claim.yaml"],
                "output_artifacts": [],
                "errors": [],
                "unsafe_language": [],
            }
        ],
    }
    write_yaml(run_dir / "front_half_run.yaml", report)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["front_half_run"] = "front_half_run.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    with pytest.raises(KernelValidationError, match="cannot contain"):
        validate_front_half_run(run_dir)
