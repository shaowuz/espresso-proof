"""Phase 05 — source-grounding stage (prepare_source_grounding).

The single auditable grounding stage earns source_backed only with exact locator
+ attribution + hypothesis match, and fails closed on a bad source/metadata
without entering prover continuation or upgrading source/proof/grade status.
(Deeper applicability/hypothesis/locator validator rules are also covered by
tests/test_real_source_grounding.py and tests/test_kernel.py.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.literature.source_grounding_stage import prepare_source_grounding
from espresso_proof.kernel.stage_runner import StageError
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")
THM = (
    "Theorem 1.2. Exact literature statement supporting the Hodge-theoretic step "
    "over base field C for cubic fourfolds."
)


def _hodge_run(tmp_path: Path) -> Path:
    run_dir = run_mock_critique("hodge_atoms", FIXTURES / "hodge_all_cubic_fourfolds" / "input.md", tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    return run_dir


def _src(run_dir: Path, text: str) -> Path:
    p = run_dir / "provided.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _sg(run_dir: Path) -> dict:
    return read_yaml(run_dir / "source_grounding.yaml")


def test_stage_earns_source_backed_with_attribution(tmp_path: Path) -> None:
    run_dir = _hodge_run(tmp_path)
    prepare_source_grounding(
        run_dir=run_dir,
        ground_source=_src(run_dir, THM),
        source_id="src_real",
        title="Topics in Galois Theory",
        authors=["J.-P. Serre"],
    )
    validate_run(run_dir)
    sg = _sg(run_dir)
    assert any(s["status"] == "source_backed" for s in sg["steps"])
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["theorem_extraction"] == "theorem_extraction.yaml"


def test_stage_missing_author_stays_candidate(tmp_path: Path) -> None:
    run_dir = _hodge_run(tmp_path)
    prepare_source_grounding(
        run_dir=run_dir, ground_source=_src(run_dir, THM), title="No-author", authors=[]
    )
    validate_run(run_dir)
    sg = _sg(run_dir)
    assert all(s["status"] != "source_backed" for s in sg["steps"])
    assert all(r["status"] != "source_backed" for r in sg["source_refs"])


def test_stage_no_exact_locator_stays_candidate(tmp_path: Path) -> None:
    run_dir = _hodge_run(tmp_path)
    prepare_source_grounding(
        run_dir=run_dir,
        ground_source=_src(run_dir, "An informal remark with no theorem locator."),
        title="Informal note",
        authors=["A. Author"],
    )
    validate_run(run_dir)
    assert all(s["status"] != "source_backed" for s in _sg(run_dir)["steps"])


def test_stage_invalid_source_path_fails_closed(tmp_path: Path) -> None:
    run_dir = _hodge_run(tmp_path)
    sg_before = (run_dir / "source_grounding.yaml").read_bytes()
    with pytest.raises((StageError, FileNotFoundError, Exception)):
        prepare_source_grounding(
            run_dir=run_dir,
            ground_source=run_dir / "does_not_exist.txt",
            title="Missing",
            authors=["A. Author"],
        )
    # fail closed: no grounding artifacts left, source_grounding unchanged, run valid
    assert not (run_dir / "theorem_extraction.yaml").exists()
    assert (run_dir / "source_grounding.yaml").read_bytes() == sg_before
    validate_run(run_dir)


def test_stage_does_not_change_idea_grade(tmp_path: Path) -> None:
    run_dir = _hodge_run(tmp_path)
    grade_before = read_yaml(run_dir / "idea_grade.yaml")
    prepare_source_grounding(
        run_dir=run_dir,
        ground_source=_src(run_dir, THM),
        title="Topics in Galois Theory",
        authors=["J.-P. Serre"],
    )
    assert read_yaml(run_dir / "idea_grade.yaml") == grade_before
