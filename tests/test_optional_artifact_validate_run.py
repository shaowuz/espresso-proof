"""Architecture-cleanup integration: optional audit artifacts are content-validated
by ``validate_run`` itself (not only at their write boundary), and a run without
them still validates (old-run compatible).

Covers the required fix: malformed front_half_run.yaml / memory_context.yaml must
fail validate_run; absence must pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.orchestrator import orchestrate_run
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validators import validate_run


def _run(tmp_path: Path) -> Path:
    claim = tmp_path / "claim.md"
    claim.write_text("Let p be a prime. Every group G of order p^2 is abelian.\n", encoding="utf-8")
    return orchestrate_run(
        project="optaudit", input_path=claim, output_root=tmp_path, provider="stub", front_half="stub"
    )["run_dir"]


def _unregister(run_dir: Path, key: str) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    opt = manifest.get("optional_artifacts", {})
    path = opt.pop(key, None)
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    if path and (run_dir / path).exists():
        (run_dir / path).unlink()


def test_baseline_run_with_optional_artifacts_validates(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    # the front-half stage registers the durable audit artifact at the gate
    assert "front_half_run" in manifest["optional_artifacts"]
    validate_run(run_dir)


def test_malformed_front_half_run_fails_validate_run(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    fh = read_yaml(run_dir / "front_half_run.yaml")
    fh["stages"].append(
        {"stage": "not_a_real_stage", "status": "ok", "validation": "ok", "errors": [], "unsafe_language": []}
    )
    write_yaml(run_dir / "front_half_run.yaml", fh)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_schema_broken_front_half_run_fails_validate_run(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    write_yaml(run_dir / "front_half_run.yaml", ["not", "a", "report"])
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_malformed_memory_context_fails_validate_run(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    # register a malformed memory_context so validate_run loads + rejects it
    write_yaml(run_dir / "memory_context.yaml", ["not", "a", "report"])
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["memory_context"] = "memory_context.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_run_without_front_half_run_still_validates(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _unregister(run_dir, "front_half_run")
    validate_run(run_dir)


def test_run_without_memory_context_still_validates(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _unregister(run_dir, "memory_context")
    validate_run(run_dir)
