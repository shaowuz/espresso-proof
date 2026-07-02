"""Real Literature Retrieval V0 (Six-Hour goal, Phase 3).

Retrieval surfaces CANDIDATE sources only. It never sets source_backed, never
skips exact locator / hypothesis match / attribution, never treats a snippet as a
checked source, never hides a failed search, and never fabricates metadata. Only
Source Grounding earns source_backed.

The grounding tests use the ``promising_but_unproved`` fixture, which has exactly
ONE candidate source-grounding step (asserted non-vacuous with len(steps) >= 1),
and include a POSITIVE control proving retrieve->ground can earn source_backed
when locator + hypothesis match + attribution all line up.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.literature.literature_retrieval import (
    retrieve_and_ground,
    retrieve_local_corpus,
    retrieve_sources,
)
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.models import LiteratureRetrievalReport, RetrievedSourceStatus
from espresso_proof.kernel.validators import KernelValidationError, validate_run

FIXTURES = Path("tests/fixtures")

# This fixture has one candidate source-grounding step (hyps: smooth/projective/
# base field C), so grounding assertions below are non-vacuous.
_STEP_FIXTURE = "promising_but_unproved"

# A theorem matching the step's needed terms AND all claim hypothesis terms.
_MATCHING_THEOREM = (
    "Theorem 1.1: For a smooth projective variety over the base field C, the exact "
    "literature statement supporting the Hodge-theoretic step holds."
)
# Overlaps the step's needed terms but lacks the claim hypothesis terms.
_MISMATCHED_THEOREM = (
    "Theorem 3.1: Over a field, the exact literature statement supporting the "
    "Hodge-theoretic step holds in general."
)


def _build(tmp_path: Path) -> Path:
    run_dir = run_mock_critique("lr", FIXTURES / _STEP_FIXTURE / "input.md", tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)
    return run_dir


def _corpus(tmp_path: Path, name: str = "corpus") -> Path:
    d = tmp_path / name
    d.mkdir()
    return d


def _write_source(corpus: Path, text: str, meta: dict, stem: str = "s") -> None:
    (corpus / f"{stem}.txt").write_text(text, encoding="utf-8")
    write_yaml(corpus / f"{stem}.meta.yaml", meta)


def _grounding_steps(run_dir: Path) -> list[dict]:
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return read_yaml(run_dir / artifacts["source_grounding"]).get("steps", [])


def test_retrieval_status_enum_has_no_source_backed() -> None:
    values = {s.value for s in RetrievedSourceStatus}
    assert "source_backed" not in values
    assert values == {"candidate", "unverified", "not_found"}


def test_report_model_rejects_status_and_long_snippet() -> None:
    base = {
        "schema_version": 1,
        "claim_id": "c1",
        "run_id": "r1",
        "generated_at": "2026-06-04T00:00:00+00:00",
        "query": "q",
        "providers": ["local_corpus"],
        "hits": [],
        "notes": [],
    }
    LiteratureRetrievalReport.model_validate(base)
    bad_hit = {
        "id": "h1", "title": "t", "authors": ["A"], "identifier": "",
        "locator": {"kind": "theorem", "value": "Theorem 1"}, "provider": "local_corpus",
        "relevance": 1.0, "status": "source_backed", "snippet": "x", "local_path": "",
    }
    with pytest.raises(ValidationError):  # enum has no source_backed
        LiteratureRetrievalReport.model_validate({**base, "hits": [bad_hit]})
    long_hit = {**bad_hit, "status": "candidate", "snippet": "x" * 241}
    with pytest.raises(ValidationError):  # snippet must be a short excerpt
        LiteratureRetrievalReport.model_validate({**base, "hits": [long_hit]})


def test_failed_search_fails_closed(tmp_path: Path) -> None:
    """An empty corpus records a not_found note and fabricates no hit; run valid."""
    run_dir = _build(tmp_path)
    report = retrieve_sources(run_dir, corpus_dir=_corpus(tmp_path), query="anything")
    validate_run(run_dir)
    assert report["hits"] == []
    assert any("no matching" in n.lower() or "no candidate" in n.lower() for n in report["notes"])


def test_positive_retrieve_then_ground_earns_source_backed(tmp_path: Path) -> None:
    """End-to-end control: a retrieved, attributed source with an exact locator and
    matching hypotheses DOES earn source_backed (via the existing grounding gate).

    This is the positive baseline that makes the negative tests meaningful.
    """
    run_dir = _build(tmp_path)
    corpus = _corpus(tmp_path)
    _write_source(
        corpus,
        _MATCHING_THEOREM,
        {"title": "Hodge Theory Reference", "authors": ["A. Hodge"], "identifier": "h:1",
         "locator": {"kind": "theorem", "value": "Theorem 1.1"}},
    )
    result = retrieve_and_ground(run_dir, corpus_dir=corpus, query="hodge smooth projective")
    validate_run(run_dir)
    assert result["grounded"] is True
    steps = _grounding_steps(run_dir)
    assert len(steps) >= 1
    assert any(step.get("status") == "source_backed" for step in steps)


def test_source_without_attribution_stays_candidate(tmp_path: Path) -> None:
    """A matching theorem with no author/title cannot earn source_backed."""
    run_dir = _build(tmp_path)
    corpus = _corpus(tmp_path)
    _write_source(
        corpus,
        _MATCHING_THEOREM,
        {"title": "", "authors": [], "identifier": "",
         "locator": {"kind": "theorem", "value": "Theorem 1.1"}},
    )
    # the retrieval hit itself is 'unverified' (attribution gap), never attributed
    hits = retrieve_local_corpus("hodge smooth projective", corpus)
    assert hits and hits[0]["status"] == "unverified"
    retrieve_and_ground(run_dir, corpus_dir=corpus, query="hodge smooth projective")
    validate_run(run_dir)
    steps = _grounding_steps(run_dir)
    assert len(steps) >= 1
    assert all(step.get("status") != "source_backed" for step in steps)


def test_unknown_locator_prose_only_stays_candidate(tmp_path: Path) -> None:
    """A prose-only source (no labelled theorem -> no exact locator) stays candidate."""
    run_dir = _build(tmp_path)
    corpus = _corpus(tmp_path)
    _write_source(
        corpus,
        "Some prose about smooth projective varieties over C and Hodge theory, no labelled theorem.",
        {"title": "Prose Note", "authors": ["C. Prose"], "identifier": "p:1",
         "locator": {"kind": "unknown", "value": "s.txt"}},
    )
    retrieve_and_ground(run_dir, corpus_dir=corpus, query="hodge smooth projective")
    validate_run(run_dir)
    steps = _grounding_steps(run_dir)
    assert len(steps) >= 1
    assert all(step.get("status") != "source_backed" for step in steps)


def test_snippet_only_source_cannot_become_source_backed(tmp_path: Path) -> None:
    """A keyword-only snippet (no extractable theorem) cannot earn source_backed."""
    run_dir = _build(tmp_path)
    corpus = _corpus(tmp_path)
    _write_source(
        corpus,
        "smooth projective base field hodge exact literature statement supporting step",
        {"title": "Snippet", "authors": ["A. Author"], "identifier": "x",
         "locator": {"kind": "theorem", "value": "Theorem 1"}},
    )
    retrieve_and_ground(run_dir, corpus_dir=corpus, query="hodge smooth projective")
    validate_run(run_dir)
    steps = _grounding_steps(run_dir)
    assert len(steps) >= 1
    assert all(step.get("status") != "source_backed" for step in steps)


def test_mismatched_hypotheses_cannot_support_step(tmp_path: Path) -> None:
    """A theorem matched on topic but missing the claim hypotheses stays candidate."""
    run_dir = _build(tmp_path)
    corpus = _corpus(tmp_path)
    _write_source(
        corpus,
        _MISMATCHED_THEOREM,
        {"title": "Vague Ref", "authors": ["B. Vague"], "identifier": "v:1",
         "locator": {"kind": "theorem", "value": "Theorem 3.1"}},
    )
    retrieve_and_ground(run_dir, corpus_dir=corpus, query="hodge")
    validate_run(run_dir)
    steps = _grounding_steps(run_dir)
    assert len(steps) >= 1
    assert all(step.get("status") != "source_backed" for step in steps)


def test_retrieval_claim_id_and_run_id_binding(tmp_path: Path) -> None:
    run_dir = _build(tmp_path)
    retrieve_sources(run_dir, corpus_dir=_corpus(tmp_path))
    validate_run(run_dir)
    path = run_dir / "literature_retrieval.yaml"
    doc = read_yaml(path)
    doc["claim_id"] = "other"
    write_yaml(path, doc)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)
    doc = read_yaml(path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    doc["claim_id"] = read_yaml(run_dir / artifacts["locked_claim"])["id"]
    doc["run_id"] = "not-this-run"
    write_yaml(path, doc)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)
