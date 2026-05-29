from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.io import read_yaml
from espresso_proof.literature_discovery import discover_local_literature
from espresso_proof.live_search import parse_ads_json, parse_arxiv_atom, parse_crossref_json
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.models import LiteratureReviewReport
from espresso_proof.pdf_ingestion import extract_text_from_pdf_bytes, extract_theorems
from espresso_proof.validators import validate_run


FIXTURES = Path("tests/fixtures")


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
    assert any(ref["status"] == "source_backed" for ref in grounding["source_refs"])
    assert grounding["steps"][0]["status"] == "source_backed"
    assert grounding["steps"][0]["hypothesis_match"]["matches"] is True


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
