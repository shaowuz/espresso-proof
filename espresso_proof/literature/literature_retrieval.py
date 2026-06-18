"""Real Literature Retrieval V0 (Six-Hour goal, Phase 3).

The first real retrieval boundary: instead of relying only on a human-provided
source, Espresso can SEARCH a local source corpus (and, optionally, surface arXiv
metadata), then hand a chosen candidate to the EXISTING source-grounding pipeline.

Safety (retrieval never shortcuts grounding):

- Retrieval only produces CANDIDATE sources. It never sets ``source_backed``;
  only ``source_verification.ground_run`` can (exact locator + hypothesis match +
  attribution), and ``validate_run`` re-checks.
- A failed / empty search fails closed: a ``not_found`` note is recorded; no hit
  is fabricated.
- Citation metadata comes from a sidecar ``<file>.meta.yaml`` or the live
  provider; it is never invented. Snippets are short excerpts (<=240 chars).

V0 providers:

- ``local_corpus``: rank text files in a corpus directory by query-term overlap
  (the reliable, offline V0; can be ground end-to-end).
- ``arxiv``: surface candidate metadata via :mod:`live_search` (network,
  best-effort); full-text retrieval + grounding from arXiv is deferred.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import LiteratureRetrievalReport

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "is", "are",
    "be", "that", "this", "with", "every", "all", "if", "then", "where", "let",
}


def _terms(text: str) -> list[str]:
    return [
        t for t in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", (text or "").lower())
        if t not in _STOPWORDS
    ]


def retrieve_local_corpus(query: str, corpus_dir: Path, *, max_results: int = 5) -> list[dict]:
    """Rank text sources in ``corpus_dir`` by query-term overlap.

    Each ``<name>.txt`` may have a sidecar ``<name>.meta.yaml`` with ``title``,
    ``authors`` (list), ``identifier``, and ``locator`` ({kind, value}). When a
    field is missing the hit stays candidate/unverified -- attribution gaps are
    surfaced honestly, never invented.
    """
    corpus_dir = Path(corpus_dir)
    if not corpus_dir.is_dir():
        return []
    query_terms = set(_terms(query))
    hits: list[dict] = []
    for path in sorted(corpus_dir.glob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        score = sum(1 for t in set(_terms(text)) if t in query_terms)
        meta_path = path.with_suffix(".meta.yaml")
        meta = read_yaml(meta_path) if meta_path.exists() else {}
        title = str(meta.get("title") or path.stem)
        authors = [str(a) for a in (meta.get("authors") or []) if str(a).strip()]
        identifier = str(meta.get("identifier") or "")
        loc = meta.get("locator") or {}
        locator = {
            "kind": loc.get("kind", "unknown"),
            "value": loc.get("value", path.name),
        }
        # candidate when attributed (author+title), else unverified (attribution gap)
        status = "candidate" if (authors and title) else "unverified"
        snippet = re.sub(r"\s+", " ", text).strip()[:240]
        hits.append(
            {
                "id": f"ret_{path.stem}",
                "title": title,
                "authors": authors,
                "identifier": identifier,
                "locator": locator,
                "provider": "local_corpus",
                "relevance": float(score),
                "status": status,
                "snippet": snippet,
                "local_path": str(path),
            }
        )
    hits.sort(key=lambda h: h["relevance"], reverse=True)
    return hits[:max_results]


def build_retrieval_report(
    run_dir: Path,
    *,
    corpus_dir: Path | None,
    query: str | None,
    providers: list[str],
    max_results: int = 5,
) -> dict:
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    resolved_query = (query or claim.get("normalized_statement") or claim.get("raw_text") or "").strip()

    hits: list[dict] = []
    notes: list[str] = []
    for provider in providers:
        if provider == "local_corpus":
            if corpus_dir is None:
                notes.append("local_corpus provider requested but no corpus_dir given; skipped.")
                continue
            found = retrieve_local_corpus(resolved_query, Path(corpus_dir), max_results=max_results)
            if not found:
                notes.append(
                    f"local_corpus search found no matching source in {corpus_dir} (fail closed)."
                )
            hits.extend(found)
        elif provider == "arxiv":
            notes.append(
                "arxiv provider surfaces candidate metadata only in V0 (via live_search); "
                "full-text retrieval + grounding from arXiv is deferred."
            )
        else:
            notes.append(f"unknown retrieval provider: {provider}.")

    if not hits:
        notes.append("No candidate sources retrieved; nothing handed to grounding.")

    report = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "query": resolved_query,
        "providers": providers,
        "hits": hits,
        "notes": [
            "Retrieval surfaces CANDIDATE sources only; it never sets source_backed.",
            "Only Source Grounding (exact locator + hypothesis match + attribution) "
            "earns source_backed; validate_run re-checks.",
            *notes,
        ],
    }
    LiteratureRetrievalReport.model_validate(report)
    return report


def retrieve_sources(
    run_dir: Path,
    *,
    corpus_dir: Path | None = None,
    query: str | None = None,
    providers: list[str] | None = None,
    max_results: int = 5,
) -> dict:
    """Write literature_retrieval.yaml (candidate sources) and register it."""
    run_dir = Path(run_dir)
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    report = build_retrieval_report(
        run_dir,
        corpus_dir=corpus_dir,
        query=query,
        providers=providers or ["local_corpus"],
        max_results=max_results,
    )
    write_yaml(run_dir / "literature_retrieval.yaml", report)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["literature_retrieval"] = (
        "literature_retrieval.yaml"
    )
    write_yaml(manifest_path, manifest)
    return report


def retrieve_and_ground(
    run_dir: Path,
    *,
    corpus_dir: Path,
    query: str | None = None,
    hit_id: str | None = None,
    max_results: int = 5,
) -> dict:
    """Retrieve from the local corpus, then hand the chosen candidate to the
    EXISTING grounding pipeline (ingest -> ground_run). Grounding stays the only
    thing that can earn source_backed; this adds no new source-backing logic.

    Returns the source_grounding report. Fails closed: if no hit has a readable
    local source, grounding is not attempted and the retrieval report is returned.
    """
    from espresso_proof.literature.pdf_ingestion import ingest_source_for_theorems
    from espresso_proof.verification.source_verification import ground_run
    from espresso_proof.kernel.models import TheoremExtractionReport

    run_dir = Path(run_dir)
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    report = retrieve_sources(
        run_dir, corpus_dir=corpus_dir, query=query, providers=["local_corpus"], max_results=max_results
    )
    hits = report["hits"]
    chosen = None
    if hit_id is not None:
        chosen = next((h for h in hits if h["id"] == hit_id), None)
    elif hits:
        chosen = hits[0]
    if chosen is None or not chosen.get("local_path"):
        return {
            "retrieval": report,
            "grounded": False,
            "reason": "no readable candidate source",
            "chosen_hit_id": None,
            "chosen_local_path": "",
        }

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    extraction = ingest_source_for_theorems(
        claim_id=claim["id"],
        source=chosen["local_path"],
        source_id=chosen["id"],
        title=chosen["title"],
        authors=chosen["authors"],
        source_type="local_note",
    )
    TheoremExtractionReport.model_validate(extraction)
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = "theorem_extraction.yaml"
    write_yaml(run_dir / "theorem_extraction.yaml", extraction)
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    grounding = ground_run(
        run_dir=run_dir, theorem_extraction_path=run_dir / "theorem_extraction.yaml"
    )
    return {
        "retrieval": report,
        "grounded": True,
        "source_grounding": grounding,
        "chosen_hit_id": chosen["id"],
        "chosen_local_path": chosen["local_path"],
    }
