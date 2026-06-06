"""Retrieval -> Source Grounding (Goal 03).

Close the loop: candidate source -> resolve source text/bytes -> theorem
extraction (reused) -> existing source grounding (reused) -> validate run. The
retrieved material stays candidate-only until the existing grounding gate earns
support through exact locator + attribution + hypothesis match. A
``retrieval_resolution.yaml`` report records the attempt; it carries no trusted
status. Everything fails closed: an unresolved candidate, a disabled network, or
a download/extraction error writes a failure/skip report and leaves the run valid
without granting any source support.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from .io import read_yaml, write_yaml
from .models import RetrievalResolutionReport

_TEXT_SUFFIXES = {".txt", ".md", ".tex", ".pdf", ".text"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _find_candidate(run_dir: Path, manifest: dict, candidate_id: str) -> dict | None:
    name = manifest.get("optional_artifacts", {}).get("literature_candidates")
    if not name:
        return None
    art = read_yaml(run_dir / name) or {}
    for step in art.get("proof_step_candidates", []):
        for cand in step.get("candidates", []):
            if cand.get("id") == candidate_id:
                return cand
    return None


def resolve_candidate_source(
    *,
    local_corpus: Path | None = None,
    local_source: Path | None = None,
    url: str | None = None,
    candidate: dict | None = None,
) -> dict:
    """Resolve a concrete source string for the ingestion path. Returns
    ``{source_kind, source, notes}`` where ``source`` is None when nothing safe
    resolves (the caller then fails closed)."""
    notes: list[str] = []
    if local_source is not None:
        p = Path(local_source)
        if p.is_file():
            return {"source_kind": "local_file", "source": str(p), "notes": notes}
        return {"source_kind": "local_file", "source": None,
                "notes": [f"local source not found: {p}"]}

    if url:
        kind = "arxiv" if "arxiv.org" in url else "url"
        return {"source_kind": kind, "source": url, "notes": notes}

    if local_corpus is not None:
        corpus = Path(local_corpus)
        if not corpus.is_dir():
            return {"source_kind": "local_corpus", "source": None,
                    "notes": [f"local corpus dir not found: {corpus}"]}
        files = sorted(
            f for f in corpus.iterdir() if f.is_file() and f.suffix.lower() in _TEXT_SUFFIXES
        )
        if not files:
            return {"source_kind": "local_corpus", "source": None,
                    "notes": ["local corpus has no text/PDF files"]}
        cand = candidate or {}
        # 1) match by identifier/filename; 2) match by title slug; else require a
        # single unambiguous file (conservative: never guess among many).
        ident = _slug(cand.get("url_or_identifier", ""))
        title = _slug(cand.get("title", ""))
        for f in files:
            if ident and ident in _slug(f.name):
                return {"source_kind": "local_corpus", "source": str(f), "notes": notes}
        for f in files:
            if title and (title in _slug(f.stem) or _slug(f.stem) in title) and len(title) >= 4:
                return {"source_kind": "local_corpus", "source": str(f), "notes": notes}
        if len(files) == 1:
            return {"source_kind": "local_corpus", "source": str(files[0]), "notes": notes}
        return {"source_kind": "local_corpus", "source": None,
                "notes": ["candidate did not match a unique corpus file (ambiguous; not guessing)"]}

    return {"source_kind": "none", "source": None, "notes": ["no source input provided"]}


def _write_report(run_dir: Path, report: dict) -> dict:
    RetrievalResolutionReport.model_validate(report)
    write_yaml(run_dir / "retrieval_resolution.yaml", report)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["retrieval_resolution"] = "retrieval_resolution.yaml"
    write_yaml(manifest_path, manifest)
    from .validators import validate_run

    validate_run(run_dir)
    return report


def retrieve_and_ground(
    *,
    run_dir: Path,
    candidate_id: str | None = None,
    local_corpus: Path | None = None,
    local_source: Path | None = None,
    url: str | None = None,
    source_id: str = "retrieved_src",
    title: str = "",
    authors: list[str] | None = None,
    source_type: str = "paper",
    network_enabled: bool = False,
    generated_at: str | None = None,
) -> dict:
    """Resolve a candidate source, extract theorems, and hand to the existing
    grounding pipeline. Fail closed at every step; never grants source support
    on its own."""
    from .pdf_ingestion import ingest_source_for_theorems
    from .source_verification import ground_run

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    claim_id = claim["id"]
    ts = generated_at or datetime.now(UTC).isoformat()

    candidate = _find_candidate(run_dir, manifest, candidate_id) if candidate_id else None
    # Attribution/metadata defaults: explicit args win, else the candidate's.
    eff_title = title or (candidate or {}).get("title", "")
    eff_authors = list(authors or (candidate or {}).get("authors", []) or [])
    eff_identifier = (candidate or {}).get("url_or_identifier", "") if candidate else (url or "")

    resolved = resolve_candidate_source(
        local_corpus=local_corpus, local_source=local_source, url=url, candidate=candidate
    )
    source_kind = resolved["source_kind"]
    source = resolved["source"]

    def base_report(status: str, *, attempted: bool, extra_notes: list[str]) -> dict:
        return {
            "schema_version": 1,
            "claim_id": claim_id,
            "generated_at": ts,
            "candidate_id": candidate_id or "",
            "resolution_status": status,
            "source_kind": source_kind,
            "source_path_or_url": source or (url or ""),
            "metadata": {
                "title": eff_title,
                "authors": [a for a in eff_authors if str(a).strip()],
                "identifier": eff_identifier,
            },
            "extraction_artifact": "",
            "grounding_attempted": attempted,
            "notes": resolved["notes"] + extra_notes,
        }

    # Network gating: a url/arxiv source is only fetched when network is enabled.
    if source_kind in {"url", "arxiv"} and not network_enabled:
        return _write_report(
            run_dir,
            base_report("skipped", attempted=False,
                        extra_notes=["network disabled; url/arxiv not fetched (fail closed)"]),
        )

    # Unresolved candidate -> stays candidate, run remains valid.
    if not source:
        return _write_report(
            run_dir,
            base_report("failed", attempted=False,
                        extra_notes=["no resolvable source; candidate stays candidate"]),
        )

    # Resolve bytes -> text -> theorem extraction (reused). Fail closed on error.
    try:
        extraction = ingest_source_for_theorems(
            claim_id=claim_id,
            source=source,
            source_id=source_id,
            title=eff_title,
            authors=eff_authors,
            source_type=source_type,
        )
    except Exception as exc:  # noqa: BLE001 - download/extraction error fails closed
        return _write_report(
            run_dir,
            base_report("failed", attempted=False,
                        extra_notes=[f"retrieval/extraction error: {exc}"]),
        )

    ext_path = run_dir / "theorem_extraction.yaml"
    write_yaml(ext_path, extraction)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = "theorem_extraction.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    # Hand to the existing grounding pipeline (it validates the run internally).
    ground_run(run_dir=run_dir, theorem_extraction_path=ext_path)

    return _write_report(
        run_dir,
        base_report("resolved", attempted=True,
                    extra_notes=["handed to source grounding; support earned only on exact "
                                 "locator + attribution + hypothesis match"]) | {
            "extraction_artifact": "theorem_extraction.yaml",
        },
    )
