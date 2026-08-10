"""Real source-grounding: a step EARNS source_backed only with exact locator +
hypothesis match + attribution (author+title). No plagiarism, no grade upgrade."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.models import SourceGroundingReport
from espresso_proof.orchestration.orchestrator import orchestrate_run
from espresso_proof.literature.pdf_ingestion import ingest_source_for_theorems
from espresso_proof.verification.source_verification import ground_run
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")
# A statement that shares >=2 significant terms + the claim's hypothesis terms
# ("square matrix", "base field C") with the spectral run's source step, with an
# exact "Theorem 1.2" locator (mirrors the existing passing ingest test).
THM = (
    "Theorem 1.2. Exact literature statement supporting the spectral step "
    "over base field C for square matrices."
)


def _ingest(run_dir: Path, text: str, *, authors, title: str = "Real Source") -> Path:
    src = run_dir / "src.txt"
    src.write_text(text, encoding="utf-8")
    claim = read_yaml(run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["locked_claim"])
    report = ingest_source_for_theorems(
        claim_id=claim["id"],
        source=str(src),
        source_id="src_real",
        title=title,
        authors=authors,
        source_type="paper",
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = "theorem_extraction.yaml"
    write_yaml(run_dir / "theorem_extraction.yaml", report)
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return run_dir / "theorem_extraction.yaml"


def _matrix_run(tmp_path: Path) -> Path:
    run_dir = run_mock_critique("linear_algebra", FIXTURES / "all_matrices_diagonalizable" / "input.md", tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)
    return run_dir


def test_step_earns_source_backed_with_attribution(tmp_path: Path) -> None:
    run_dir = _matrix_run(tmp_path)
    grade_before = read_yaml(run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["idea_grade"])
    ext = _ingest(run_dir, THM, authors=["A. Mathematician"], title="A Note on Spectral Methods")
    ground_run(run_dir=run_dir, theorem_extraction_path=ext)
    validate_run(run_dir)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    backed = [r for r in sg["source_refs"] if r["status"] == "source_backed"]
    assert backed, sg
    for r in backed:
        assert [a for a in r["authors"] if a.strip()]  # attributed
        assert r["title"].strip()
        assert r["locator"]["kind"] != "unknown"  # exact locator
    assert any(s["status"] == "source_backed" for s in sg["steps"])
    # grade is NOT changed by grounding (no proof upgrade).
    grade_after = read_yaml(run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["idea_grade"])
    assert grade_after["proof_language_status"] == grade_before["proof_language_status"]
    assert grade_after["status"] == grade_before["status"]
    # memo cites the source as grounded support, not a proof.
    memo = CliRunner().invoke(app, ["memo", "--run", str(run_dir)]).output
    assert "Proof-route steps:" in memo
    assert "Reference supports this stated claim" in memo
    assert "A. Mathematician" in memo
    lower = memo.lower()
    for phrase in ("proof-close", "proof complete", "we proved", "espresso proved"):
        assert phrase not in lower


def test_missing_attribution_stays_candidate(tmp_path: Path) -> None:
    run_dir = _matrix_run(tmp_path)
    ext = _ingest(run_dir, THM, authors=[], title="No-author source")  # no author
    ground_run(run_dir=run_dir, theorem_extraction_path=ext)
    validate_run(run_dir)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    # writer fails closed -> never source_backed without attribution.
    assert all(r["status"] != "source_backed" for r in sg["source_refs"])
    assert all(s["status"] != "source_backed" for s in sg["steps"])


def test_source_conclusion_mismatch_stays_candidate_and_preserves_requested_step(
    tmp_path: Path,
) -> None:
    run_dir = _matrix_run(tmp_path)
    source_grounding_path = run_dir / "source_grounding.yaml"
    sg_before = read_yaml(source_grounding_path)
    requested = (
        "square matrices over base field C have distinct eigenvalues"
    )
    sg_before["steps"][0]["needed_theorem"] = requested
    write_yaml(source_grounding_path, sg_before)
    text = (
        "Theorem 1.2. Exact literature statement about Spectral structures over "
        "base field C for square matrices."
    )
    ext = _ingest(run_dir, text, authors=["A. Mathematician"], title="A Note on Spectral Methods")
    ground_run(run_dir=run_dir, theorem_extraction_path=ext)
    validate_run(run_dir)

    sg_after = read_yaml(source_grounding_path)
    assert sg_after["steps"][0]["needed_theorem"] == requested
    assert sg_after["steps"][0]["status"] == "candidate"
    assert "source conclusion does not cover" in sg_after["steps"][0]["note"]


def test_model_rejects_source_backed_without_attribution() -> None:
    base = {
        "claim_id": "claim_001",
        "source_refs": [
            {
                "id": "src_x",
                "type": "paper",
                "title": "Some source",
                "authors": [],  # missing attribution
                "locator": {"kind": "theorem", "value": "Theorem 1.1"},
                "statement_or_summary": "Exact statement.",
                "hypothesis_match": {"matches": True, "mismatches": []},
                "confidence": "high",
                "status": "source_backed",
            }
        ],
        "steps": [],
    }
    with pytest.raises(ValueError, match="attributed author"):
        SourceGroundingReport.model_validate(base)
    base["source_refs"][0]["authors"] = ["A. Author"]
    base["source_refs"][0]["title"] = ""  # missing title
    with pytest.raises(ValueError, match="source title"):
        SourceGroundingReport.model_validate(base)


def test_source_grounding_rejects_ambiguous_duplicate_bindings() -> None:
    ref = {
        "id": "src_x",
        "type": "paper",
        "title": "Some source",
        "authors": ["A. Author"],
        "locator": {"kind": "theorem", "value": "Theorem 1.1"},
        "statement_or_summary": "Exact statement.",
        "hypothesis_match": {"matches": True, "mismatches": []},
        "confidence": "high",
        "status": "source_backed",
    }
    step = {
        "proof_step_id": "step_001",
        "needed_theorem": "Exact statement.",
        "status": "source_backed",
        "source_refs": ["src_x"],
        "hypothesis_match": {"matches": True, "mismatches": []},
        "note": "Supported by the exact locator.",
    }
    with pytest.raises(ValueError, match="source_ref ids must be unique"):
        SourceGroundingReport.model_validate(
            {"claim_id": "claim_001", "source_refs": [ref, dict(ref)], "steps": [step]}
        )
    with pytest.raises(ValueError, match="proof_step_id values must be unique"):
        SourceGroundingReport.model_validate(
            {
                "claim_id": "claim_001",
                "source_refs": [ref],
                "steps": [step, {**step, "source_refs": ["src_x"]}],
            }
        )
    with pytest.raises(ValueError, match="source_refs must be unique"):
        SourceGroundingReport.model_validate(
            {
                "claim_id": "claim_001",
                "source_refs": [ref],
                "steps": [{**step, "source_refs": ["src_x", "src_x"]}],
            }
        )


def test_no_exact_locator_stays_candidate(tmp_path: Path) -> None:
    run_dir = _matrix_run(tmp_path)
    # statement with NO theorem label -> no exact locator extracted.
    text = "An informal remark about Spectral structures over base field C for square matrices."
    ext = _ingest(run_dir, text, authors=["A. Author"], title="Informal note")
    ground_run(run_dir=run_dir, theorem_extraction_path=ext)
    validate_run(run_dir)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    assert all(s["status"] != "source_backed" for s in sg["steps"])


def test_grounding_run_stays_kernel_valid_and_registers_audit(tmp_path: Path) -> None:
    run_dir = _matrix_run(tmp_path)
    ext = _ingest(run_dir, THM, authors=["A. Mathematician"], title="A Note on Spectral Methods")
    ground_run(run_dir=run_dir, theorem_extraction_path=ext)
    validate_run(run_dir)  # fail-closed: full kernel gate after grounding
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["theorem_applicability"] == "theorem_applicability.yaml"


def test_ground_source_failure_rolls_back_run(tmp_path: Path, monkeypatch) -> None:
    # Regression: prepare_source_grounding runs inside StageRunner with
    # a whole-run snapshot. If grounding fails AFTER partial writes (theorem
    # extraction registered, a partial source_grounding.yaml on disk), the run
    # directory must be restored byte-for-byte and stay kernel-valid.
    from espresso_proof.literature import source_grounding_stage
    from espresso_proof.kernel.stage_runner import StageError

    from espresso_proof.kernel.run_events import EVENTS_FILENAME

    run_dir = _matrix_run(tmp_path)
    src = tmp_path / "outside_src.txt"  # outside run_dir: not part of the snapshot
    src.write_text(THM, encoding="utf-8")

    # The diagnostic events.jsonl timeline is append-only BY DESIGN:
    # it records the failed stage and is intentionally never rolled back, so it
    # is excluded from the byte-identity comparison.
    def _files(root: Path) -> dict:
        return {
            p.relative_to(root): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file() and p.name != EVENTS_FILENAME
        }

    before = _files(run_dir)

    def boom(*, run_dir: Path, theorem_extraction_path: Path) -> None:
        # Simulate a mid-grounding crash that leaves a partial artifact behind.
        (run_dir / "source_grounding.yaml").write_text("partial: true\n", encoding="utf-8")
        raise RuntimeError("injected grounding failure")

    monkeypatch.setattr(source_grounding_stage, "ground_run", boom)
    with pytest.raises(StageError, match="source_grounding"):
        source_grounding_stage.prepare_source_grounding(
            run_dir=run_dir,
            ground_source=src,
            title="Injected failure source",
            authors=["A. Author"],
        )
    after = _files(run_dir)
    # Byte-identical restore: no partial source_grounding.yaml, no
    # theorem_extraction.yaml, no manifest pointer left behind.
    assert after == before
    validate_run(run_dir)


def test_orchestrate_ground_source_cli_earns_source_backed(tmp_path: Path) -> None:
    # End-to-end CLI: orchestrate (front-half mock keeps the mock source steps) to
    # the gate, then resume with --ground-source against a provided text source.
    runner = CliRunner()
    src = tmp_path / "provided.txt"
    src.write_text(THM, encoding="utf-8")
    fresh = runner.invoke(
        app,
        [
            "orchestrate", "--input", str(FIXTURES / "all_matrices_diagonalizable" / "input.md"),
            "--project", "rsg", "--provider", "stub", "--front-half", "mock",
            "--output-root", str(tmp_path),
        ],
    )
    assert fresh.exit_code == 0, fresh.output
    run_dir = Path(fresh.output.splitlines()[0].split("Wrote run artifacts to ")[-1])
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)
    orchestrate_run(
        project="rsg",
        input_path=FIXTURES / "all_matrices_diagonalizable" / "input.md",
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
        run_dir=run_dir,
    )
    brief = read_yaml(run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["strategy_brief"])
    selected = brief["top_strategies"][0]["id"]
    effort = brief["allowed_human_choices"][0]["effort_options"][0]
    resumed = runner.invoke(
        app,
        [
            "orchestrate", "--resume", str(run_dir), "--select-strategy", selected,
            "--effort", effort, "--provider", "stub",
            "--ground-source", str(src), "--source-author", "A. Mathematician",
            "--source-title", "A Note on Spectral Methods",
        ],
    )
    assert resumed.exit_code == 0, resumed.output
    validate_run(run_dir)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    assert any(s["status"] == "source_backed" for s in sg["steps"]), sg
    assert "Reference supports this stated claim" in resumed.output


def test_tampered_hypothesis_match_blocked_by_independent_audit(tmp_path: Path) -> None:
    # De-circularization: the applicability audit recomputes hypothesis
    # coverage from the claim hypotheses and the CITED STATEMENT itself. A
    # hand-edited hypothesis_match=True over a statement that does not cover
    # the claim hypotheses must fail closed: the audit blocks and the run no
    # longer validates with the step still source_backed.
    from espresso_proof.verification.theorem_applicability import audit_theorem_applicability
    from espresso_proof.kernel.validators import KernelValidationError

    run_dir = _matrix_run(tmp_path)
    ext = _ingest(run_dir, THM, authors=["A. Mathematician"], title="A Note on Spectral Methods")
    ground_run(run_dir=run_dir, theorem_extraction_path=ext)
    validate_run(run_dir)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    backed_ref_ids = {
        ref["id"] for ref in sg["source_refs"] if ref["status"] == "source_backed"
    }
    assert backed_ref_ids, "precondition: grounding earned source_backed"

    # Tamper: keep matches=True but swap the cited statement for unrelated text.
    for ref in sg["source_refs"]:
        if ref["id"] in backed_ref_ids:
            ref["statement_or_summary"] = (
                "Theorem 9.9. Every elliptic curve over Q is modular."
            )
    write_yaml(run_dir / "source_grounding.yaml", sg)

    audit_theorem_applicability(run_dir)
    report = read_yaml(run_dir / "theorem_applicability.yaml")
    backed_usages = [u for u in report["usages"] if u["source_status"] == "source_backed"]
    assert backed_usages
    for usage in backed_usages:
        assert usage["blocks_source_backed"] is True
        assert usage["applicability_status"] != "applicable"
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_independent_audit_confirms_honest_grounding(tmp_path: Path) -> None:
    # The de-circularized audit must NOT break honest grounding: recomputing
    # coverage from the real cited statement reproduces the applicable verdict.
    from espresso_proof.verification.theorem_applicability import audit_theorem_applicability

    run_dir = _matrix_run(tmp_path)
    ext = _ingest(run_dir, THM, authors=["A. Mathematician"], title="A Note on Spectral Methods")
    ground_run(run_dir=run_dir, theorem_extraction_path=ext)
    audit_theorem_applicability(run_dir)  # re-run audit standalone
    validate_run(run_dir)
    report = read_yaml(run_dir / "theorem_applicability.yaml")
    backed = [u for u in report["usages"] if u["source_status"] == "source_backed"]
    assert backed
    for usage in backed:
        assert usage["applicability_status"] == "applicable"
        assert all(c["status"] == "satisfied" for c in usage["conditions"])
        assert any("Independent audit recheck" in (c["evidence"] or "") for c in usage["conditions"])
