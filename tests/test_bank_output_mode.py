"""Bank Output Mode V0 (opt-in).

Espresso can optionally PRODUCE examples / counterexamples / strategies as bank
directories (via `materialize_run_banks` / `espresso materialize-banks`). The
default single-file output is unchanged; conversion changes no item content or
status and the aggregated bank matches the old model shape.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.io import read_yaml
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.research_bank import (
    is_bank_dir,
    load_counterexamples_artifact,
    load_examples_artifact,
    load_strategy_synthesis_artifact,
    materialize_run_banks,
    write_bank,
)
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validators import validate_run

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path) -> Path:
    return run_mock_critique(
        "hodge_atoms", FIXTURES / "hodge_all_cubic_fourfolds" / "input.md", tmp_path
    )


# ---------------------------------------------------------------------------
# 1) Default run still writes old single YAML (unchanged).
# ---------------------------------------------------------------------------


def test_default_run_writes_single_yaml(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    for name in ("examples.yaml", "counterexamples.yaml", "strategy_synthesis.yaml"):
        assert (run_dir / name).is_file()
    for dir_name in ("examples", "counterexamples", "strategies"):
        assert not (run_dir / dir_name).exists()
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["artifacts"]["examples"] == "examples.yaml"
    validate_run(run_dir)


# ---------------------------------------------------------------------------
# 2-5) Opt-in run writes bank directories; validates; contracts pass; shape
#      matches.
# ---------------------------------------------------------------------------


def test_opt_in_run_writes_bank_directories_and_validates(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    before = {
        "examples": load_examples_artifact(run_dir / "examples.yaml").model_dump(),
        "counterexamples": load_counterexamples_artifact(run_dir / "counterexamples.yaml").model_dump(),
        "strategy_synthesis": load_strategy_synthesis_artifact(run_dir / "strategy_synthesis.yaml").model_dump(),
    }

    converted = materialize_run_banks(run_dir)
    assert sorted(converted) == ["counterexamples", "examples", "strategy_synthesis"]

    # bank directories exist; old single files removed; manifest points at dirs.
    for dir_name in ("examples", "counterexamples", "strategies"):
        assert is_bank_dir(run_dir / dir_name)
    for name in ("examples.yaml", "counterexamples.yaml", "strategy_synthesis.yaml"):
        assert not (run_dir / name).exists()
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["artifacts"]["examples"] == "examples"
    assert manifest["artifacts"]["counterexamples"] == "counterexamples"
    assert manifest["artifacts"]["strategy_synthesis"] == "strategies"

    # (3) + (4) bank output validates, including agent-contract path checks.
    validate_run(run_dir)

    # (5) aggregated bank output matches the old single-file model shape exactly.
    assert load_examples_artifact(run_dir / "examples").model_dump() == before["examples"]
    assert load_counterexamples_artifact(run_dir / "counterexamples").model_dump() == before["counterexamples"]
    assert load_strategy_synthesis_artifact(run_dir / "strategies").model_dump() == before["strategy_synthesis"]


def test_materialize_is_idempotent(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    assert materialize_run_banks(run_dir)  # first converts
    assert materialize_run_banks(run_dir) == []  # second is a no-op
    validate_run(run_dir)


def test_materialize_failure_leaves_original_run_valid(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    manifest_before = read_yaml(run_dir / "run_manifest.yaml")
    # Corrupt the second requested artifact so planning fails before any
    # destructive change to the first.
    (run_dir / "counterexamples.yaml").write_text("not: [a, valid, report\n", encoding="utf-8")
    with pytest.raises(Exception):
        materialize_run_banks(run_dir, keys=["examples", "counterexamples"])
    # the first artifact's single file is still present and the manifest unchanged.
    assert (run_dir / "examples.yaml").is_file()
    assert read_yaml(run_dir / "run_manifest.yaml")["artifacts"] == manifest_before["artifacts"]


def test_partial_materialize_one_artifact(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    converted = materialize_run_banks(run_dir, keys=["examples"])
    assert converted == ["examples"]
    assert is_bank_dir(run_dir / "examples")
    # the others stay single files.
    assert (run_dir / "counterexamples.yaml").is_file()
    assert (run_dir / "strategy_synthesis.yaml").is_file()
    validate_run(run_dir)


# ---------------------------------------------------------------------------
# 6) No status upgrades from conversion.
# ---------------------------------------------------------------------------


def test_conversion_does_not_upgrade_status(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    grade_before = read_yaml(run_dir / "idea_grade.yaml")
    sg_before = read_yaml(run_dir / "source_grounding.yaml")
    materialize_run_banks(run_dir)
    validate_run(run_dir)
    grade_after = read_yaml(run_dir / "idea_grade.yaml")
    assert grade_after["status"] == grade_before["status"]
    assert grade_after["proof_language_status"] == grade_before["proof_language_status"]
    # source-grounding artifact (a different artifact) is untouched by conversion.
    assert read_yaml(run_dir / "source_grounding.yaml") == sg_before


# ---------------------------------------------------------------------------
# write_bank safety: refuses a non-empty target; round-trips.
# ---------------------------------------------------------------------------


def test_write_bank_refuses_non_empty_directory(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    ex = load_examples_artifact(run_dir / "examples.yaml")
    bank = run_dir / "examples_bank"
    write_bank(bank, ex, kind="examples")
    with pytest.raises(KernelValidationError, match="non-empty bank directory"):
        write_bank(bank, ex, kind="examples")


def test_write_bank_round_trips_to_same_model(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    ce = load_counterexamples_artifact(run_dir / "counterexamples.yaml")
    bank = run_dir / "ce_bank"
    write_bank(bank, ce, kind="counterexamples")
    assert load_counterexamples_artifact(bank).model_dump() == ce.model_dump()
