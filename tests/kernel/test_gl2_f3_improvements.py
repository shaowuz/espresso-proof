from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.cli import app
from espresso_proof.evaluation.real_math_benchmark import real_math_target_cases
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.validators import validate_run
from espresso_proof.literature.pdf_ingestion import ingest_source_for_theorems
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.verification.finite_group_verifier import gl2_f3_solvability_witness
from espresso_proof.verification.proof_verification import verify_gl2_f3_solvable_in_run
from espresso_proof.verification.theorem_applicability import (
    review_theorem_applicability_with_llm,
)

FIXTURES = Path("tests/fixtures")


def _claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)


def _basic_run(tmp_path: Path) -> Path:
    run_dir = run_mock_critique(
        "gl2_goal",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    _claim_clear(run_dir)
    return run_dir


def test_old_arxiv_postscript_gzip_extracts_clean_theorem_locator(tmp_path: Path) -> None:
    ps = b"""%!PS-Adobe-2.0
(Safarevic:) (Theorem) (1) (Let) (k) (b)q(e) (a) (global) (\\014eld)
(and) (let) (G) (b)q(e) (a) (\\014nite) (solv)m(able) (group.)
(Then) (there) (exists) (a) (\\014nite) (Galois) (extension) (K) (j) (k)
(with) (Galois) (group) (G) (K) (j) (k) (G)
"""
    src = tmp_path / "safarevic_source.ps.gz"
    src.write_bytes(gzip.compress(ps))

    report = ingest_source_for_theorems(
        claim_id="claim_001",
        source=str(src),
        source_id="src_sw",
        title="Safarevic's theorem on solvable groups as Galois groups",
        authors=["Alexander Schmidt", "Kay Wingberg"],
    )

    first = report["theorems"][0]
    assert first["locator"] == {"kind": "theorem", "value": "Theorem 1"}
    assert "global field" in first["statement"]
    assert "finite solvable group" in first["statement"]
    assert "finite Galois extension K/k" in first["statement"]
    assert first["statement_sha256"]
    assert first["source_locator"]["title"].startswith("Safarevic")
    assert first["source_locator"]["authors"] == ["Alexander Schmidt", "Kay Wingberg"]
    assert first["source_locator"]["theorem_locator"] == {"kind": "theorem", "value": "Theorem 1"}
    assert first["source_locator"]["paragraph_index"] == 1
    assert first["source_locator"]["excerpt"]
    assert first["source_locator"]["source_sha256"]
    assert any("decompressed gzip" in note for note in report["notes"])


def test_arxiv_tar_gz_source_package_extracts_tex_theorem(tmp_path: Path) -> None:
    tar_buffer = io.BytesIO()
    tex = (
        "Theorem 2. Let k be a global field and let G be a finite solvable group. "
        "Then there exists a finite Galois extension K/k with Galois group G(K/k) "
        "is isomorphic to G."
    ).encode("utf-8")
    with tarfile.open(fileobj=tar_buffer, mode="w") as archive:
        info = tarfile.TarInfo("paper/main.tex")
        info.size = len(tex)
        archive.addfile(info, io.BytesIO(tex))
    src = tmp_path / "source.tar.gz"
    src.write_bytes(gzip.compress(tar_buffer.getvalue()))

    report = ingest_source_for_theorems(
        claim_id="claim_001",
        source=str(src),
        source_id="src_tar",
        title="Synthetic arXiv source package",
        authors=["A. Author"],
    )

    first = report["theorems"][0]
    assert first["locator"] == {"kind": "theorem", "value": "Theorem 2"}
    assert "finite solvable group" in first["statement"]
    assert any("source tar text" in note for note in report["notes"])


def test_gl2_f3_witness_records_derived_series_and_scoped_entry(tmp_path: Path) -> None:
    witness = gl2_f3_solvability_witness()
    assert witness["order"] == 48
    assert witness["derived_series_orders"] == [48, 24, 8, 2, 1]
    assert witness["is_solvable"] is True

    run_dir = _basic_run(tmp_path)
    entry = verify_gl2_f3_solvable_in_run(run_dir)
    validate_run(run_dir)

    assert entry["earned_status"] == "instance_checked"
    assert "48 -> 24 -> 8 -> 2 -> 1" in entry["scope_note"]
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["finite_object_witness"] == "finite_object_witness.yaml"
    assert (run_dir / "finite_object_witness.yaml").exists()


def test_gl2_f3_duplicate_witness_rolls_back_without_manifest_drift(tmp_path: Path) -> None:
    run_dir = _basic_run(tmp_path)
    verify_gl2_f3_solvable_in_run(run_dir)
    before_manifest = (run_dir / "run_manifest.yaml").read_bytes()
    before_witness = (run_dir / "finite_object_witness.yaml").read_bytes()
    before_proof_verification = (run_dir / "proof_verification.yaml").read_bytes()

    with pytest.raises(ValueError, match="duplicate proof_verification entry id"):
        verify_gl2_f3_solvable_in_run(run_dir)

    assert (run_dir / "run_manifest.yaml").read_bytes() == before_manifest
    assert (run_dir / "finite_object_witness.yaml").read_bytes() == before_witness
    assert (run_dir / "proof_verification.yaml").read_bytes() == before_proof_verification
    validate_run(run_dir)


def test_seal_failure_generates_gap_repair_artifacts(tmp_path: Path) -> None:
    from tests.kernel.test_line_numbered_proof import _add_single_gap, _run_with_proof

    run_dir = _run_with_proof(tmp_path)
    _add_single_gap(run_dir)

    result = CliRunner().invoke(app, ["seal-consensus", "--run", str(run_dir)])

    assert result.exit_code == 1
    assert "Generated proof-gap repair plan" in result.output
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    optional = manifest["optional_artifacts"]
    assert optional["proof_gap_resolution"] == "proof_gap_resolution.yaml"
    assert optional["repair_target"] == "repair_target.yaml"
    assert optional["gap_repair_rounds_index"] == "gap_repair_rounds/index.yaml"
    gap_report = read_yaml(run_dir / "proof_gap_resolution.yaml")
    assert gap_report["repair_plan"]
    assert read_yaml(run_dir / "repair_target.yaml")["selected_target"]["line_ids"] == ["L1"]
    stage_status = read_yaml(run_dir / "stage_status.yaml")
    repair_stage = next(s for s in stage_status["stages"] if s["stage_id"] == "repair_target")
    assert repair_stage["status"] == "not_started"
    assert "withheld until hard dependencies are done" in repair_stage["status_reason"]
    validate_run(run_dir)


def test_gap_repair_artifacts_roll_back_when_validation_fails(tmp_path: Path, monkeypatch) -> None:
    from tests.kernel.test_line_numbered_proof import _add_single_gap, _run_with_proof
    import espresso_proof.orchestration.proof_gap_resolver as resolver

    run_dir = _run_with_proof(tmp_path)
    _add_single_gap(run_dir)
    before_manifest = (run_dir / "run_manifest.yaml").read_bytes()
    before_proof = (run_dir / "line_numbered_proof.yaml").read_bytes()

    def fail_validation(path: Path) -> None:
        raise RuntimeError("forced validation failure")

    monkeypatch.setattr(resolver, "validate_run", fail_validation)
    with pytest.raises(RuntimeError, match="forced validation failure"):
        resolver.resolve_proof_gaps(run_dir)

    assert (run_dir / "run_manifest.yaml").read_bytes() == before_manifest
    assert (run_dir / "line_numbered_proof.yaml").read_bytes() == before_proof
    assert not (run_dir / "proof_gap_resolution.yaml").exists()
    assert not (run_dir / "repair_target.yaml").exists()
    assert not (run_dir / "gap_repair_rounds").exists()


class _ApplicabilityReviewClient:
    def complete_json(self, *, system: str, prompt: str) -> dict:
        assert "source_backed" in system
        if "source-match reviewer" in system:
            return {
                "verdict": "wrong_source",
                "conclusion_status": "unclear",
                "notes": ["source statement does not say the needed conclusion"],
            }
        return {
            "verdict": "needs_human_assumption",
            "conclusion_status": "unclear",
            "notes": ["a standard theorem would need explicit human allowance"],
        }


class _ApplicabilityAllOkClient:
    def complete_json(self, *, system: str, prompt: str) -> dict:
        assert "source_backed" in system
        return {
            "verdict": "applicable",
            "conclusion_status": "compatible",
            "conditions": [
                {
                    "text": "reviewer says theorem statement and hypotheses match",
                    "status": "satisfied",
                    "evidence": "LLM reviewer judgment",
                }
            ],
            "notes": ["reviewer found no theorem-level mismatch"],
        }


def test_llm_applicability_review_writes_structured_blocking_status(tmp_path: Path) -> None:
    run_dir = _basic_run(tmp_path)
    review = review_theorem_applicability_with_llm(
        run_dir,
        client=_ApplicabilityReviewClient(),
    )
    validate_run(run_dir)

    theorem = read_yaml(run_dir / "theorem_applicability.yaml")
    assert len(review["reviews"]) == 2 * len(theorem["usages"])
    assert theorem["overall_status"] == "wrong_source"
    assert all(usage["applicability_status"] == "wrong_source" for usage in theorem["usages"])
    source_grounding = read_yaml(run_dir / "source_grounding.yaml")
    assert all(step["status"] != "source_backed" for step in source_grounding["steps"])


def test_llm_applicability_review_cannot_make_unbacked_source_applicable(tmp_path: Path) -> None:
    run_dir = _basic_run(tmp_path)
    review_theorem_applicability_with_llm(
        run_dir,
        client=_ApplicabilityAllOkClient(),
    )
    validate_run(run_dir)

    theorem = read_yaml(run_dir / "theorem_applicability.yaml")
    assert theorem["overall_status"] == "blocked"
    assert theorem["usages"]
    assert all(usage["source_status"] != "source_backed" for usage in theorem["usages"])
    assert all(usage["applicability_status"] == "blocked" for usage in theorem["usages"])
    assert all(usage["blocks_source_backed"] is True for usage in theorem["usages"])


def test_gl2_f3_case_is_in_real_math_benchmark_catalog() -> None:
    case = next(c for c in real_math_target_cases() if c["case_id"] == "gl2_f3_galois_over_q")
    criteria = " ".join(case["acceptance_criteria"]).lower()
    assert "shafarevich" in criteria
    assert "theorem 1" in criteria
    assert "gl_2(f_3)" in criteria
    assert "overclaim_count" in criteria
