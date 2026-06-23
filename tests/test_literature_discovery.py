from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.claim.claim_clear import ClaimClearBlockedError
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.literature.literature_discovery import discover_local_literature
from espresso_proof.literature.live_search import parse_ads_json, parse_arxiv_atom, parse_crossref_json
from espresso_proof.literature.literature_review_agent import ReviewSourceSpec, run_literature_review_agent
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.kernel.models import (
    LiteratureReviewAgentReport,
    LiteratureReviewReport,
    TheoremApplicabilityReport,
    TheoremApplicabilityUsage,
)
from espresso_proof.literature.pdf_ingestion import extract_text_from_pdf_bytes, extract_theorems
from espresso_proof.kernel.validators import KernelValidationError, validate_run


FIXTURES = Path("tests/fixtures")


def _mark_claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)


def test_local_literature_discovery_finds_hodge_candidates(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )

    report = discover_local_literature(
        project_dir=Path("projects/hodge_atoms"),
        locked_claim_path=run_dir / "locked_claim.yaml",
        hypothesis_ledger_path=run_dir / "hypothesis_ledger.yaml",
        max_references=5,
    )

    parsed = LiteratureReviewReport.model_validate(report)
    statuses = {item.status.value for item in parsed.candidate_references}
    assert parsed.claim_id == "claim_001"
    assert parsed.candidate_references
    assert "source_backed" not in statuses
    assert "misapplied" not in statuses
    assert any("cubic" in item.title.lower() or "hodge" in item.title.lower() for item in parsed.candidate_references)
    assert parsed.known_examples


def test_discover_literature_cli_writes_schema_valid_run_artifact(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    _mark_claim_clear(run_dir)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "discover-literature",
            "--run",
            str(run_dir),
            "--mode",
            "local",
            "--max-references",
            "4",
        ],
    )

    assert result.exit_code == 0, result.output
    validate_run(run_dir)
    report = LiteratureReviewReport.model_validate(read_yaml(run_dir / "literature_review.yaml"))
    assert len(report.candidate_references) <= 4
    assert all(item.found_by == "local_notes" for item in report.candidate_references)
    assert all(item.status.value in {"candidate", "unverified", "not_found"} for item in report.candidate_references)


def test_local_literature_discovery_uses_query_hint(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "coherent_base_change",
        FIXTURES / "vague_claim_unclear" / "input.md",
        tmp_path,
    )

    report = discover_local_literature(
        project_dir=Path("projects/coherent_base_change"),
        locked_claim_path=run_dir / "locked_claim.yaml",
        hypothesis_ledger_path=run_dir / "hypothesis_ledger.yaml",
        query="flat base change Stacks Project tag",
        max_references=3,
    )

    parsed = LiteratureReviewReport.model_validate(report)
    titles = " ".join(item.title for item in parsed.candidate_references).lower()
    assert "stacks project" in titles
    assert "User query was used as an additional ranking hint." in parsed.notes


def test_live_search_parsers_return_candidate_references() -> None:
    arxiv_payload = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2508.05105v1</id>
        <title>Birational invariants from Hodge structures</title>
        <summary>A theorem about cubic fourfold examples.</summary>
        <author><name>Ada Example</name></author>
      </entry>
    </feed>
    """
    ads_payload = b"""{
      "response": {"docs": [{
        "bibcode": "2026arXiv260304518G",
        "title": ["On the irrationality of cubic fourfolds"],
        "author": ["Guere, Jeremy"],
        "abstract": "Theorem statements for cubic fourfolds."
      }]}
    }"""
    crossref_payload = b"""{
      "message": {"items": [{
        "DOI": "10.1000/example",
        "title": ["Coherent cohomology and base change"],
        "author": [{"given": "A.", "family": "Grothendieck"}],
        "abstract": "A base change theorem."
      }]}
    }"""

    arxiv = parse_arxiv_atom(arxiv_payload, "hodge atoms")
    ads = parse_ads_json(ads_payload, "cubic fourfolds")
    crossref = parse_crossref_json(crossref_payload, "base change")

    assert arxiv[0].status == "candidate"
    assert arxiv[0].locator_kind == "arxiv_id"
    assert ads[0].status == "candidate"
    assert ads[0].locator_kind == "url"
    assert crossref[0].status == "candidate"
    assert crossref[0].locator_value == "https://doi.org/10.1000/example"


def test_live_discovery_cli_merges_fake_arxiv_and_web_results(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    _mark_claim_clear(run_dir)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "discover-literature",
            "--run",
            str(run_dir),
            "--mode",
            "live",
            "--provider",
            "ads",
            "--max-references",
            "3",
        ],
    )

    assert result.exit_code == 0, result.output
    report = LiteratureReviewReport.model_validate(read_yaml(run_dir / "literature_review.yaml"))
    assert all(item.status.value in {"candidate", "unverified", "not_found"} for item in report.candidate_references)
    assert any("ADS search skipped" in note for note in report.notes)


def test_pdf_ingestion_and_source_verification_can_source_back_exact_match(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    _mark_claim_clear(run_dir)
    pdf_path = tmp_path / "source.pdf"
    pdf_path.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj <<>> stream\n"
        b"BT (Theorem 1.2. Exact literature statement supporting the Hodge-theoretic step "
        b"over base field C for cubic fourfolds.) Tj ET\n"
        b"endstream\n%%EOF\n"
    )
    runner = CliRunner()

    ingest = runner.invoke(
        app,
        [
            "ingest-pdf",
            "--run",
            str(run_dir),
            "--pdf",
            str(pdf_path),
            "--source-id",
            "src_pdf_001",
            "--title",
            "Test source",
            "--author",
            "Test Author",
        ],
    )
    assert ingest.exit_code == 0, ingest.output

    verify = runner.invoke(app, ["verify-sources", "--run", str(run_dir)])
    assert verify.exit_code == 0, verify.output
    validate_run(run_dir)
    grounding = read_yaml(run_dir / "source_grounding.yaml")
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert any(ref["status"] == "source_backed" for ref in grounding["source_refs"])
    assert grounding["steps"][0]["status"] == "source_backed"
    assert grounding["steps"][0]["hypothesis_match"]["matches"] is True
    assert manifest["optional_artifacts"]["theorem_applicability"] == "theorem_applicability.yaml"


def test_pdf_fallback_decodes_hex_strings_for_theorem_extraction() -> None:
    raw = (
        b"%PDF-1.4\nBT "
        b"<5468656f72656d20322e312e20457861637420686578207468656f72656d206f7665722062617365206669656c6420432e>"
        b" Tj ET\n%%EOF"
    )

    text = extract_text_from_pdf_bytes(raw)
    theorems = extract_theorems(text, source_ref_id="src_hex")

    assert theorems
    assert theorems[0]["label"] == "2.1"
    assert "base field C" in theorems[0]["statement"]


def test_literature_review_agent_reads_source_and_compares_theorem(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    _mark_claim_clear(run_dir)
    source_path = tmp_path / "hodge_source.txt"
    source_path.write_text(
        "Theorem 1.2. For all cubic fourfolds over base field C, "
        "the Hodge atom obstruction proves irrationality.\n",
        encoding="utf-8",
    )

    report = run_literature_review_agent(
        run_dir=run_dir,
        source_specs=[
            ReviewSourceSpec(
                source=str(source_path),
                source_id="src_hodge_text",
                title="Exact Hodge obstruction statement",
            )
        ],
    )

    parsed = LiteratureReviewAgentReport.model_validate(report)
    assert parsed.input_sources[0].access.status == "full_text_read"
    assert parsed.extracted_theorems[0].statement_is_verbatim is True
    assert "For all cubic fourfolds" in parsed.extracted_theorems[0].statement_exact
    assert parsed.theorem_comparisons[0].comparison_status == "applicable"
    assert parsed.theorem_comparisons[0].relevance.role == "key_theorem"
    assert parsed.relevance_summary.overall_status == "supports_route"
    assert parsed.uncertainty.overall_confidence in {"high", "medium"}


def test_literature_review_agent_flags_very_general_theorem_for_universal_claim(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    _mark_claim_clear(run_dir)
    source_path = tmp_path / "very_general_source.txt"
    source_path.write_text(
        "Theorem 3.4. For a very general smooth cubic fourfold over base field C, "
        "the Hodge atom obstruction proves irrationality.\n",
        encoding="utf-8",
    )

    report = run_literature_review_agent(
        run_dir=run_dir,
        source_specs=[
            ReviewSourceSpec(
                source=str(source_path),
                source_id="src_very_general",
                title="Very general cubic fourfold theorem",
            )
        ],
    )

    parsed = LiteratureReviewAgentReport.model_validate(report)
    comparison = parsed.theorem_comparisons[0]
    assert comparison.comparison_status == "weaker_than_needed"
    assert any(match.relation == "incompatible" for match in comparison.hypothesis_matches)
    assert "hypothesis_mismatch" in set(comparison.uncertainty.failure_modes)
    assert parsed.relevance_summary.overall_status in {"unclear", "background_only"}


def test_literature_review_agent_cannot_emit_source_backed_source(tmp_path: Path) -> None:
    # Literature Review finds candidate references only; it cannot decide
    # source_backed or misapplied (KERNEL_SAFETY_RULES Literature And Source
    # Rules). Those remain Referencing / Source Grounding decisions.
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    _mark_claim_clear(run_dir)
    source_path = tmp_path / "agent_source.txt"
    source_path.write_text(
        "Theorem 1.2. For all cubic fourfolds over base field C, "
        "the Hodge atom obstruction proves irrationality.\n",
        encoding="utf-8",
    )
    report = run_literature_review_agent(
        run_dir=run_dir,
        source_specs=[
            ReviewSourceSpec(
                source=str(source_path),
                source_id="src_agent",
                title="Agent source",
            )
        ],
    )

    report["input_sources"][0]["source_ref"]["status"] = "misapplied"
    with pytest.raises(ValueError, match="may not emit source_backed or misapplied"):
        LiteratureReviewAgentReport.model_validate(report)


def test_literature_review_agent_cli_writes_optional_artifact(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    _mark_claim_clear(run_dir)
    source_path = tmp_path / "cli_source.txt"
    source_path.write_text(
        "Theorem 2.1. For all cubic fourfolds over base field C, "
        "the Hodge atom obstruction proves irrationality.\n",
        encoding="utf-8",
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "review-literature-agent",
            "--run",
            str(run_dir),
            "--source",
            str(source_path),
            "--source-id",
            "src_cli",
            "--title",
            "CLI source",
        ],
    )

    assert result.exit_code == 0, result.output
    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["literature_review_agent"] == "literature_review_agent.yaml"
    report = LiteratureReviewAgentReport.model_validate(read_yaml(run_dir / "literature_review_agent.yaml"))
    assert report.extracted_theorems


def test_literature_review_agent_blocks_unclear_claim_before_source_read(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )

    with pytest.raises(ClaimClearBlockedError, match="claim-clear gate"):
        run_literature_review_agent(
            run_dir=run_dir,
            source_specs=[ReviewSourceSpec(source=str(tmp_path / "missing.txt"))],
        )

    assert not (run_dir / "literature_review_agent.yaml").exists()
    validate_run(run_dir)


def test_audit_theorem_applicability_cli_writes_condition_ledger(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    _mark_claim_clear(run_dir)
    source_path = tmp_path / "very_general_source.txt"
    source_path.write_text(
        "Theorem 3.4. For a very general smooth cubic fourfold over base field C, "
        "the Hodge atom obstruction proves irrationality.\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    review = runner.invoke(
        app,
        [
            "review-literature-agent",
            "--run",
            str(run_dir),
            "--source",
            str(source_path),
            "--source-id",
            "src_very_general",
            "--title",
            "Very general theorem",
        ],
    )
    assert review.exit_code == 0, review.output

    result = runner.invoke(app, ["audit-theorem-applicability", "--run", str(run_dir)])

    assert result.exit_code == 0, result.output
    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["theorem_applicability"] == "theorem_applicability.yaml"
    report = TheoremApplicabilityReport.model_validate(
        read_yaml(run_dir / "theorem_applicability.yaml")
    )
    assert report.literature_review_agent_artifact == "literature_review_agent.yaml"
    assert report.usages
    assert report.overall_status in {"misapplied", "blocked", "unclear"}
    assert any(usage.blocks_source_backed for usage in report.usages)
    assert any(
        condition.status in {"missing", "mismatched", "unclear", "not_checked"}
        for usage in report.usages
        for condition in usage.conditions
    )


def test_audit_theorem_applicability_cli_falls_back_without_literature_agent(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    runner = CliRunner()

    result = runner.invoke(app, ["audit-theorem-applicability", "--run", str(run_dir)])

    assert result.exit_code == 0, result.output
    validate_run(run_dir)
    report = TheoremApplicabilityReport.model_validate(
        read_yaml(run_dir / "theorem_applicability.yaml")
    )
    assert report.literature_review_agent_artifact is None
    assert report.usages
    assert "fallback" in " ".join(report.usages[0].notes).lower()


def test_source_backed_step_requires_theorem_applicability_artifact(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    source_grounding = read_yaml(run_dir / "source_grounding.yaml")
    ref_id = source_grounding["source_refs"][0]["id"]
    source_grounding["source_refs"][0]["status"] = "source_backed"
    source_grounding["source_refs"][0]["authors"] = ["A. Author"]
    source_grounding["source_refs"][0]["title"] = "Attributed exact source"
    source_grounding["source_refs"][0]["locator"] = {"kind": "theorem", "value": "Theorem 1.2"}
    source_grounding["source_refs"][0]["statement_or_summary"] = (
        "For a very general smooth cubic fourfold, the obstruction applies."
    )
    source_grounding["source_refs"][0]["hypothesis_match"] = {"matches": True, "mismatches": []}
    source_grounding["steps"][0]["status"] = "source_backed"
    source_grounding["steps"][0]["source_refs"] = [ref_id]
    source_grounding["steps"][0]["hypothesis_match"] = {"matches": True, "mismatches": []}
    write_yaml(run_dir / "source_grounding.yaml", source_grounding)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "require theorem_applicability audit" in str(exc)
    else:
        raise AssertionError("validate_run should require theorem_applicability for source_backed steps")


def test_source_backed_step_requires_applicable_theorem_conditions(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    source_grounding = read_yaml(run_dir / "source_grounding.yaml")
    ref_id = source_grounding["source_refs"][0]["id"]
    source_grounding["source_refs"][0]["status"] = "source_backed"
    source_grounding["source_refs"][0]["authors"] = ["A. Author"]
    source_grounding["source_refs"][0]["title"] = "Attributed exact source"
    source_grounding["source_refs"][0]["locator"] = {"kind": "theorem", "value": "Theorem 1.2"}
    source_grounding["source_refs"][0]["statement_or_summary"] = (
        "For a very general smooth cubic fourfold, the obstruction applies."
    )
    source_grounding["source_refs"][0]["hypothesis_match"] = {"matches": True, "mismatches": []}
    source_grounding["steps"][0]["status"] = "source_backed"
    source_grounding["steps"][0]["source_refs"] = [ref_id]
    source_grounding["steps"][0]["hypothesis_match"] = {"matches": True, "mismatches": []}
    write_yaml(run_dir / "source_grounding.yaml", source_grounding)
    applicability = {
        "schema_version": 1,
        "claim_id": source_grounding["claim_id"],
        "run_id": manifest["run_id"],
        "generated_at": "2026-06-02T00:00:00+00:00",
        "source_grounding_artifact": "source_grounding.yaml",
        "literature_review_agent_artifact": None,
        "usages": [
            {
                "proof_step_id": source_grounding["steps"][0]["proof_step_id"],
                "source_ref_id": ref_id,
                "theorem_id": None,
                "theorem_label": "Theorem 1.2",
                "theorem_statement": "For a very general smooth cubic fourfold, the obstruction applies.",
                "locator": {"kind": "theorem", "value": "Theorem 1.2"},
                "source_status": "source_backed",
                "conclusion_status": "compatible",
                "conditions": [
                    {
                        "id": "cond_001",
                        "text": "very general hypothesis not present in locked claim",
                        "condition_type": "genericity",
                        "status": "missing",
                        "evidence": "The theorem has an extra very general condition.",
                        "definition_locator": None,
                    }
                ],
                "applicability_status": "blocked",
                "blocks_source_backed": True,
                "notes": ["This theorem usage is blocked by an unsatisfied condition."],
            }
        ],
        "overall_status": "blocked",
        "notes": ["Test fixture for source-backed condition blocking."],
    }
    write_yaml(run_dir / "theorem_applicability.yaml", applicability)
    manifest.setdefault("optional_artifacts", {})["theorem_applicability"] = "theorem_applicability.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "theorem applicability" in str(exc)
    else:
        raise AssertionError("validate_run should reject source_backed step with blocked theorem conditions")


def _setup_source_backed_run(tmp_path: Path):
    """Mock run mutated so step[0] and ref[0] are source_backed with exact locator."""
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    source_grounding = read_yaml(run_dir / "source_grounding.yaml")
    ref_id = source_grounding["source_refs"][0]["id"]
    source_grounding["source_refs"][0]["status"] = "source_backed"
    source_grounding["source_refs"][0]["authors"] = ["A. Author"]
    source_grounding["source_refs"][0]["title"] = "Attributed exact source"
    source_grounding["source_refs"][0]["locator"] = {"kind": "theorem", "value": "Theorem 1.2"}
    source_grounding["source_refs"][0]["statement_or_summary"] = (
        "For a smooth cubic fourfold, the obstruction applies."
    )
    source_grounding["source_refs"][0]["hypothesis_match"] = {"matches": True, "mismatches": []}
    source_grounding["steps"][0]["status"] = "source_backed"
    source_grounding["steps"][0]["source_refs"] = [ref_id]
    source_grounding["steps"][0]["hypothesis_match"] = {"matches": True, "mismatches": []}
    write_yaml(run_dir / "source_grounding.yaml", source_grounding)
    step_id = source_grounding["steps"][0]["proof_step_id"]
    return run_dir, manifest, source_grounding, ref_id, step_id


def _write_applicability(
    run_dir: Path, manifest: dict, claim_id: str, usages: list[dict], **report_overrides
) -> None:
    applicability = {
        "schema_version": 1,
        "claim_id": claim_id,
        "run_id": manifest["run_id"],
        "generated_at": "2026-06-02T00:00:00+00:00",
        "source_grounding_artifact": "source_grounding.yaml",
        "literature_review_agent_artifact": None,
        "usages": usages,
        "overall_status": "unclear",
        "notes": ["test applicability fixture"],
    }
    applicability.update(report_overrides)
    write_yaml(run_dir / "theorem_applicability.yaml", applicability)
    manifest.setdefault("optional_artifacts", {})["theorem_applicability"] = "theorem_applicability.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)


def test_theorem_applicability_rejects_claim_id_mismatch(tmp_path: Path) -> None:
    run_dir, manifest, source_grounding, _ref, _step = _setup_source_backed_run(tmp_path)
    _write_applicability(run_dir, manifest, "claim_does_not_exist", usages=[])
    with pytest.raises(KernelValidationError, match="claim_id does not match locked claim"):
        validate_run(run_dir)


def test_theorem_applicability_rejects_run_id_mismatch(tmp_path: Path) -> None:
    run_dir, manifest, source_grounding, _ref, _step = _setup_source_backed_run(tmp_path)
    _write_applicability(
        run_dir, manifest, source_grounding["claim_id"], usages=[], run_id="run_does_not_exist"
    )
    with pytest.raises(KernelValidationError, match="run_id does not match manifest"):
        validate_run(run_dir)


def test_theorem_applicability_rejects_source_grounding_artifact_mismatch(tmp_path: Path) -> None:
    run_dir, manifest, source_grounding, _ref, _step = _setup_source_backed_run(tmp_path)
    _write_applicability(
        run_dir,
        manifest,
        source_grounding["claim_id"],
        usages=[],
        source_grounding_artifact="not_source_grounding.yaml",
    )
    with pytest.raises(KernelValidationError, match="must cite the source_grounding artifact"):
        validate_run(run_dir)


def test_theorem_applicability_rejects_unknown_proof_step(tmp_path: Path) -> None:
    run_dir, manifest, source_grounding, ref_id, _step = _setup_source_backed_run(tmp_path)
    usage = _theorem_usage(proof_step_id="ghost_step", source_ref_id=ref_id)
    _write_applicability(
        run_dir,
        manifest,
        source_grounding["claim_id"],
        usages=[usage],
        overall_status="blocked",
    )
    with pytest.raises(KernelValidationError, match="references unknown proof step"):
        validate_run(run_dir)


def test_theorem_applicability_rejects_unknown_source_ref(tmp_path: Path) -> None:
    run_dir, manifest, source_grounding, _ref, step_id = _setup_source_backed_run(tmp_path)
    usage = _theorem_usage(proof_step_id=step_id, source_ref_id="ghost_ref")
    _write_applicability(
        run_dir,
        manifest,
        source_grounding["claim_id"],
        usages=[usage],
        overall_status="blocked",
    )
    with pytest.raises(KernelValidationError, match="references unknown source ref"):
        validate_run(run_dir)


def test_source_backed_step_rejects_very_general_for_universal_claim(tmp_path: Path) -> None:
    # A very-general / generic theorem cannot back a universal ("all") claim
    # without an explicit scope justification (KERNEL_SAFETY_RULES "very general
    # cannot support all unless explicitly justified"), now structurally gated.
    run_dir, manifest, source_grounding, ref_id, step_id = _setup_source_backed_run(tmp_path)
    usage = _theorem_usage(
        proof_step_id=step_id,
        source_ref_id=ref_id,
        theorem_scope="very_general",
        claim_scope="universal",
    )
    _write_applicability(run_dir, manifest, source_grounding["claim_id"], usages=[usage])
    with pytest.raises(KernelValidationError, match="without an explicit scope_justification"):
        validate_run(run_dir)


def test_source_backed_step_allows_very_general_with_justification(tmp_path: Path) -> None:
    # With an explicit scope_justification, the very-general/universal gate does
    # not fire (the kernel requires the justification, it does not judge the math).
    run_dir, manifest, source_grounding, ref_id, step_id = _setup_source_backed_run(tmp_path)
    usage = _theorem_usage(
        proof_step_id=step_id,
        source_ref_id=ref_id,
        theorem_scope="very_general",
        claim_scope="universal",
        scope_justification="The locked claim is itself restricted to the very-general locus.",
    )
    _write_applicability(run_dir, manifest, source_grounding["claim_id"], usages=[usage])
    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "scope_justification" not in str(exc), f"scope gate wrongly fired: {exc}"


def test_source_backed_step_lacks_applicability_audit_is_rejected(tmp_path: Path) -> None:
    # A source_backed step with NO matching applicability usage must be rejected
    # (a tampered/incomplete audit cannot let a source_backed step through).
    run_dir, manifest, source_grounding, _ref_id, _step_id = _setup_source_backed_run(tmp_path)
    _write_applicability(run_dir, manifest, source_grounding["claim_id"], usages=[])
    with pytest.raises(KernelValidationError, match="lacks applicability audit"):
        validate_run(run_dir)


def test_theorem_applicability_source_status_must_match_step(tmp_path: Path) -> None:
    # A usage whose source_status disagrees with its source_grounding step is
    # rejected (the audit cannot quietly downgrade/relabel the step's status).
    run_dir, manifest, source_grounding, ref_id, step_id = _setup_source_backed_run(tmp_path)
    usage = _theorem_usage(
        proof_step_id=step_id,
        source_ref_id=ref_id,
        source_status="candidate",
        applicability_status="blocked",
        blocks_source_backed=True,
    )
    _write_applicability(
        run_dir,
        manifest,
        source_grounding["claim_id"],
        usages=[usage],
        overall_status="blocked",
    )
    with pytest.raises(KernelValidationError, match="source_status does not match source_grounding step"):
        validate_run(run_dir)


def _theorem_usage(**overrides) -> dict:
    """A valid applicable theorem-applicability usage; override fields to break it."""
    usage = {
        "proof_step_id": "step_1",
        "source_ref_id": "src_1",
        "theorem_id": None,
        "theorem_label": "Theorem 1.2",
        "theorem_statement": "For a smooth cubic fourfold, the obstruction applies.",
        "locator": {"kind": "theorem", "value": "Theorem 1.2"},
        "source_status": "source_backed",
        "conclusion_status": "compatible",
        "conditions": [
            {
                "id": "cond_1",
                "text": "object is a smooth cubic fourfold",
                "condition_type": "smoothness",
                "status": "satisfied",
                "evidence": "claim object matches the theorem hypothesis",
                "definition_locator": None,
            }
        ],
        "applicability_status": "applicable",
        "blocks_source_backed": False,
        "notes": [],
    }
    usage.update(overrides)
    return usage


def test_theorem_usage_rejects_applicable_with_mismatched_condition() -> None:
    # A contradicted/mismatched theorem hypothesis cannot be called applicable
    # (KERNEL_SAFETY_RULES Literature And Source Rules).
    usage = _theorem_usage(
        conditions=[
            {
                "id": "cond_1",
                "text": "object is smooth",
                "condition_type": "smoothness",
                "status": "mismatched",
                "evidence": "the claim object is singular, not smooth",
                "definition_locator": None,
            }
        ]
    )
    with pytest.raises(ValueError, match="all conditions satisfied"):
        TheoremApplicabilityUsage.model_validate(usage)


def test_theorem_usage_rejects_applicable_with_contradicting_conclusion() -> None:
    # A theorem whose conclusion does not match the supported step cannot be
    # applicable (KERNEL_SAFETY_RULES: theorem conclusion must match the step).
    usage = _theorem_usage(conclusion_status="contradicts")
    with pytest.raises(ValueError, match="compatible conclusion"):
        TheoremApplicabilityUsage.model_validate(usage)


def test_source_backed_step_requires_exact_applicability_locator(tmp_path: Path) -> None:
    # Even with satisfied conditions and a compatible conclusion, a source_backed
    # theorem use must carry an exact applicability locator.
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    source_grounding = read_yaml(run_dir / "source_grounding.yaml")
    ref_id = source_grounding["source_refs"][0]["id"]
    source_grounding["source_refs"][0]["status"] = "source_backed"
    source_grounding["source_refs"][0]["authors"] = ["A. Author"]
    source_grounding["source_refs"][0]["title"] = "Attributed exact source"
    source_grounding["source_refs"][0]["locator"] = {"kind": "theorem", "value": "Theorem 1.2"}
    source_grounding["source_refs"][0]["statement_or_summary"] = (
        "For a smooth cubic fourfold, the obstruction applies."
    )
    source_grounding["source_refs"][0]["hypothesis_match"] = {"matches": True, "mismatches": []}
    source_grounding["steps"][0]["status"] = "source_backed"
    source_grounding["steps"][0]["source_refs"] = [ref_id]
    source_grounding["steps"][0]["hypothesis_match"] = {"matches": True, "mismatches": []}
    write_yaml(run_dir / "source_grounding.yaml", source_grounding)
    applicability = {
        "schema_version": 1,
        "claim_id": source_grounding["claim_id"],
        "run_id": manifest["run_id"],
        "generated_at": "2026-06-02T00:00:00+00:00",
        "source_grounding_artifact": "source_grounding.yaml",
        "literature_review_agent_artifact": None,
        "usages": [
            _theorem_usage(
                proof_step_id=source_grounding["steps"][0]["proof_step_id"],
                source_ref_id=ref_id,
                locator={"kind": "unknown", "value": "unknown"},
            )
        ],
        "overall_status": "unclear",
        "notes": ["Test fixture for inexact applicability locator."],
    }
    write_yaml(run_dir / "theorem_applicability.yaml", applicability)
    manifest.setdefault("optional_artifacts", {})["theorem_applicability"] = "theorem_applicability.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    with pytest.raises(KernelValidationError, match="locator does not match"):
        validate_run(run_dir)


def test_theorem_applicability_rejects_duplicate_usage_for_same_step(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    runner = CliRunner()
    result = runner.invoke(app, ["audit-theorem-applicability", "--run", str(run_dir)])
    assert result.exit_code == 0, result.output
    report = read_yaml(run_dir / "theorem_applicability.yaml")
    report["usages"].append(report["usages"][0])
    write_yaml(run_dir / "theorem_applicability.yaml", report)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "unique proof_step_id" in str(exc)
    else:
        raise AssertionError("validate_run should reject duplicate theorem applicability usages")


def test_source_backed_step_applicability_usage_must_match_step_source_ref(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    source_grounding = read_yaml(run_dir / "source_grounding.yaml")
    good_ref = source_grounding["source_refs"][0]
    wrong_ref = {**good_ref, "id": "src_wrong_but_known"}
    for ref in (good_ref, wrong_ref):
        ref["status"] = "source_backed"
        ref["authors"] = ["A. Author"]
        ref["title"] = "Attributed exact source"
        ref["locator"] = {"kind": "theorem", "value": "Theorem 1.2"}
        ref["hypothesis_match"] = {"matches": True, "mismatches": []}
    source_grounding["source_refs"] = [good_ref, wrong_ref]
    source_grounding["steps"][0]["status"] = "source_backed"
    source_grounding["steps"][0]["source_refs"] = [good_ref["id"]]
    source_grounding["steps"][0]["hypothesis_match"] = {"matches": True, "mismatches": []}
    write_yaml(run_dir / "source_grounding.yaml", source_grounding)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    applicability = {
        "schema_version": 1,
        "claim_id": source_grounding["claim_id"],
        "run_id": manifest["run_id"],
        "generated_at": "2026-06-02T00:00:00+00:00",
        "source_grounding_artifact": "source_grounding.yaml",
        "literature_review_agent_artifact": None,
        "usages": [
            {
                "proof_step_id": source_grounding["steps"][0]["proof_step_id"],
                "source_ref_id": wrong_ref["id"],
                "theorem_id": None,
                "theorem_label": "Theorem 1.2",
                "theorem_statement": "For all cubic fourfolds over C, the obstruction applies.",
                "locator": {"kind": "theorem", "value": "Theorem 1.2"},
                "source_status": "source_backed",
                "conclusion_status": "compatible",
                "conditions": [
                    {
                        "id": "cond_001",
                        "text": "all hypotheses match",
                        "condition_type": "other",
                        "status": "satisfied",
                        "evidence": "Tampered usage cites the wrong source ref.",
                        "definition_locator": None,
                    }
                ],
                "applicability_status": "applicable",
                "blocks_source_backed": False,
                "notes": ["This usage should be rejected because it cites the wrong source ref."],
            }
        ],
        "overall_status": "all_applicable",
        "notes": ["Tampered source ref binding."],
    }
    write_yaml(run_dir / "theorem_applicability.yaml", applicability)
    manifest.setdefault("optional_artifacts", {})["theorem_applicability"] = "theorem_applicability.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "not used by proof step" in str(exc)
    else:
        raise AssertionError("validate_run should reject applicability usage for a different source ref")


# --- Goal 37: live positive hits must validate (found_by schema fix) ---------
def test_live_positive_hits_validate_in_literature_report() -> None:
    from espresso_proof.literature.literature_discovery import live_responses_to_literature_items
    from espresso_proof.literature.live_search import LiveSearchResponse, LiveSearchResult
    from espresso_proof.kernel.models import LiteratureItem

    responses = [
        LiveSearchResponse(
            provider="arxiv",
            results=[
                LiveSearchResult(
                    title="Hodge atoms on cubic fourfolds",
                    authors=["A. Author"],
                    locator_kind="arxiv_id",
                    locator_value="2401.01234",
                    relevance="possible_theorem_source",
                    status="candidate",
                    why_relevant="Matches the claim's Hodge-theoretic terms.",
                    provider="arxiv",
                )
            ],
            notes=[],
        ),
        LiveSearchResponse(
            provider="crossref",
            results=[
                LiveSearchResult(
                    title="Irrationality of very general cubic fourfolds",
                    authors=["B. Author"],
                    locator_kind="url",
                    locator_value="https://doi.org/10.1000/xyz",
                    relevance="core",
                    status="candidate",
                    why_relevant="Survey of the obstruction.",
                    provider="crossref",
                )
            ],
            notes=[],
        ),
    ]
    items = live_responses_to_literature_items(responses)
    assert len(items) == 2
    for item in items:
        validated = LiteratureItem.model_validate(item)  # was a ValidationError before the fix
        assert validated.found_by == "web_search"
        assert validated.status.value == "candidate"
    # the specific provider stays auditable (id + why_relevant prefix).
    assert items[0]["id"].startswith("live_arxiv_")
    assert "[live arxiv]" in items[0]["why_relevant"]
    assert items[1]["id"].startswith("live_crossref_")


# --- Goal 37: typed near-miss source discipline ------------------------------
def test_smooth_hypothesis_not_covered_by_singular_statement() -> None:
    from espresso_proof.verification.source_verification import check_hypothesis_applicability

    hypotheses = [{"name": "X is smooth", "status": "explicit", "source": "agent"}]
    result = check_hypothesis_applicability(
        "Theorem 2.1. Every singular cubic threefold X admits a resolution.",
        hypotheses,
        [],
    )
    assert result["matches"] == "unclear"
    assert any("smooth" in m for m in result["mismatches"])


def test_projective_hypothesis_needs_proper_and_ample_not_proper_alone() -> None:
    from espresso_proof.verification.source_verification import check_hypothesis_applicability

    hypotheses = [{"name": "X is projective", "status": "explicit", "source": "agent"}]
    proper_only = check_hypothesis_applicability(
        "Theorem 3.2. Let X be a proper variety over a field.", hypotheses, []
    )
    assert proper_only["matches"] == "unclear"  # proper alone is NOT projective
    proper_ample = check_hypothesis_applicability(
        "Theorem 3.3. Let X be a proper variety carrying an ample line bundle.",
        hypotheses,
        [],
    )
    assert proper_ample["matches"] is True


def test_characteristic_zero_not_covered_by_positive_characteristic() -> None:
    from espresso_proof.verification.source_verification import check_hypothesis_applicability

    hypotheses = [{"name": "characteristic zero", "status": "explicit", "source": "agent"}]
    result = check_hypothesis_applicability(
        "Theorem 4.1. Over fields of positive characteristic the statement holds.",
        hypotheses,
        [],
    )
    assert result["matches"] == "unclear"


def test_extra_theorem_hypothesis_blocks_applicability() -> None:
    from espresso_proof.verification.theorem_applicability import (
        _applicability_status,
        _condition_status_from_relation,
    )

    # A theorem hypothesis the claim does not have (extra_in_theorem) must
    # surface as a missing condition and block applicability.
    status = _condition_status_from_relation("extra_in_theorem")
    assert status == "missing"
    condition = {
        "id": "cond_001",
        "text": "theorem additionally requires X to be smooth",
        "condition_type": "smoothness",
        "status": status,
        "evidence": "claim does not state smoothness",
        "definition_locator": None,
    }
    assert _applicability_status([condition], "compatible", "candidate") == "blocked"
