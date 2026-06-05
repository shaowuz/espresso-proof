"""Autonomous Retrieval V1.

Full-text locator extraction: pull candidate theorem/lemma/proposition/corollary
blocks WITH precise locators, candidate-only, fail-closed. Only the existing
source-grounding gate (exact locator + hypothesis match + attribution) can earn
source_backed; extraction never upgrades grade or proof status.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.models import Locator, SourceGroundingReport
from espresso_proof.pdf_ingestion import (
    extract_candidate_locators,
    extract_source_text,
    ingest_source_for_theorems,
)
from espresso_proof.source_verification import ground_run
from espresso_proof.validators import validate_run

FIXTURES = Path("tests/fixtures")

_FULLTEXT = (
    "Theorem 1.2. Every group of order p^2 with p prime is abelian. "
    "Lemma 2.1. A nontrivial finite p-group has nontrivial center. "
    "Proposition 3.4. If G/Z(G) is cyclic then G is abelian. "
    "Corollary 3.5. A group of order p^2 is C_{p^2} or C_p x C_p."
)


def _by_kind(candidates: list[dict]) -> dict[str, dict]:
    return {c["kind"]: c for c in candidates}


# ---------------------------------------------------------------------------
# 1-2) Extract theorem / lemma / proposition locators from full text.
# ---------------------------------------------------------------------------


def test_extracts_theorem_locator_from_full_text() -> None:
    by_kind = _by_kind(extract_candidate_locators(_FULLTEXT))
    thm = by_kind["theorem"]
    assert thm["locator"] == {"kind": "theorem", "value": "Theorem 1.2"}
    assert thm["is_exact"] is True
    assert thm["status"] == "candidate"


def test_extracts_lemma_and_proposition_locators_from_full_text() -> None:
    by_kind = _by_kind(extract_candidate_locators(_FULLTEXT))
    assert by_kind["lemma"]["locator"] == {"kind": "lemma", "value": "Lemma 2.1"}
    assert by_kind["lemma"]["is_exact"] is True
    assert by_kind["proposition"]["locator"] == {"kind": "proposition", "value": "Proposition 3.4"}
    assert by_kind["proposition"]["is_exact"] is True
    # every extracted block is candidate-only (extraction earns nothing).
    assert all(c["status"] == "candidate" for c in extract_candidate_locators(_FULLTEXT))


# ---------------------------------------------------------------------------
# 3) A candidate without an exact locator cannot become reference-supported.
# ---------------------------------------------------------------------------


def test_candidate_without_exact_locator_cannot_be_source_backed() -> None:
    base = {
        "claim_id": "claim_001",
        "source_refs": [
            {
                "id": "src_x",
                "type": "paper",
                "title": "A Source",
                "authors": ["A. Mathematician"],
                "locator": {"kind": "unknown", "value": ""},  # not exact
                "statement_or_summary": "Some statement.",
                "hypothesis_match": {"matches": True, "mismatches": []},
                "confidence": "high",
                "status": "source_backed",
            }
        ],
        "steps": [],
    }
    with pytest.raises(ValueError, match="exact locator"):
        SourceGroundingReport.model_validate(base)


# ---------------------------------------------------------------------------
# 4) A candidate with locator + attribution can be handed to grounding.
# ---------------------------------------------------------------------------

_HODGE_THM = (
    "Theorem 1.2. Exact literature statement supporting the Hodge-theoretic step "
    "over base field C for cubic fourfolds."
)


def _hodge_run(tmp_path: Path) -> Path:
    return run_mock_critique(
        "hodge_atoms", FIXTURES / "hodge_all_cubic_fourfolds" / "input.md", tmp_path
    )


def test_candidate_with_locator_and_attribution_is_handed_to_grounding(tmp_path: Path) -> None:
    run_dir = _hodge_run(tmp_path)
    # extraction finds an exact locator in the full text...
    cands = extract_candidate_locators(_HODGE_THM)
    assert cands and cands[0]["is_exact"] is True
    # ...and the existing grounding pipeline earns source_backed only because the
    # source also has attribution + a hypothesis match.
    src = run_dir / "src.txt"
    src.write_text(_HODGE_THM, encoding="utf-8")
    claim = read_yaml(run_dir / "locked_claim.yaml")
    report = ingest_source_for_theorems(
        claim_id=claim["id"], source=str(src), source_id="src_real",
        title="Real Source", authors=["J.-P. Serre"], source_type="paper",
    )
    write_yaml(run_dir / "theorem_extraction.yaml", report)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = "theorem_extraction.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    ground_run(run_dir=run_dir, theorem_extraction_path=run_dir / "theorem_extraction.yaml")
    validate_run(run_dir)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    assert any(r["status"] == "source_backed" for r in sg["source_refs"])


# ---------------------------------------------------------------------------
# 5) PDF failure is recorded / fail-closed, not fabricated.
# ---------------------------------------------------------------------------


def test_malformed_pdf_fails_closed_without_fabricated_locators() -> None:
    raw = b"%PDF-1.4\nrandom bytes with no structured statement blocks at all\n%%EOF\n"
    text, notes = extract_source_text(raw, "broken.pdf")
    candidates = extract_candidate_locators(text)
    # no theorem/lemma markers -> no fabricated candidate locators.
    assert candidates == []
    # the parse attempt is recorded, not silently dropped.
    assert notes


# ---------------------------------------------------------------------------
# 6) Retrieval never upgrades idea grade or proof status.
# ---------------------------------------------------------------------------


def test_retrieval_does_not_upgrade_grade_or_proof_status(tmp_path: Path) -> None:
    run_dir = _hodge_run(tmp_path)
    grade_before = read_yaml(run_dir / "idea_grade.yaml")
    src = run_dir / "src.txt"
    src.write_text(_HODGE_THM, encoding="utf-8")
    claim = read_yaml(run_dir / "locked_claim.yaml")
    report = ingest_source_for_theorems(
        claim_id=claim["id"], source=str(src), source_id="src_real",
        title="Real Source", authors=["J.-P. Serre"], source_type="paper",
    )
    write_yaml(run_dir / "theorem_extraction.yaml", report)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = "theorem_extraction.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    ground_run(run_dir=run_dir, theorem_extraction_path=run_dir / "theorem_extraction.yaml")
    validate_run(run_dir)
    grade_after = read_yaml(run_dir / "idea_grade.yaml")
    assert grade_after["status"] == grade_before["status"]
    assert grade_after["proof_language_status"] == grade_before["proof_language_status"]
    assert grade_after["score"] == grade_before["score"]
