"""Autonomous LLM-guided literature retrieval (V0).

Pipeline: claim/route -> LLM planner -> queries -> provider retrieval ->
candidate ranking -> optional ingestion -> theorem extraction -> source
grounding -> memo.

Hard safety rule (unchanged): retrieval may PROPOSE sources only. It never sets
source_backed, never treats a title/abstract as theorem support, never invents a
locator, and never claims a theorem is proved. Only ``source_verification.
ground_run`` (exact locator + hypothesis match + attribution) can earn reference
support. The ``AutonomousLiteratureRetrievalReport`` model has no source/
evidence/proof status field and pins every candidate to ``status="candidate"``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .io import read_yaml, write_yaml
from .literature_planner import plan_literature_search
from .literature_retrieval import retrieve_and_ground, retrieve_local_corpus
from .live_search import live_literature_search
from .llm_agents import LLMClient, safe_list, safe_text
from .models import AutonomousLiteratureRetrievalReport
from .validators import validate_run

_RANK_SYSTEM = (
    "You are Espresso Proof's literature-ranking assistant. Given a claim and a "
    "list of candidate sources (id, title, snippet), say how relevant each is and "
    "why. You ONLY rank and explain candidates: you never assert a source is "
    "checked, source_backed, or proves the claim, and you never invent a locator. "
    "Respond with ONLY a JSON object."
)
_RANK_OUTPUT_SCHEMA = {
    "ranked": [{"id": "string", "relevance": "number 0..1", "why_relevant": "string"}]
}


def _local_candidates(query: str, corpus_dir: Path | None, max_results: int) -> list[dict]:
    if corpus_dir is None:
        return []
    out = []
    for hit in retrieve_local_corpus(query, Path(corpus_dir), max_results=max_results):
        out.append(
            {
                "provider": "local_corpus",
                "query": query,
                "title": hit.get("title", ""),
                "authors": list(hit.get("authors", [])),
                "identifier": hit.get("identifier", ""),
                "snippet": (hit.get("snippet", "") or "")[:600],
                "relevance": float(hit.get("relevance", 0.0) or 0.0),
                "local_path": hit.get("local_path", ""),
                "why_relevant": f"local_corpus matched query '{query}'.",
            }
        )
    return out


def _arxiv_candidates(query: str, max_results: int) -> tuple[list[dict], list[str]]:
    candidates: list[dict] = []
    notes: list[str] = []
    try:
        responses = live_literature_search(query=query, providers=["arxiv"], max_results=max_results)
    except Exception as exc:  # noqa: BLE001 — network best-effort; fail closed to a note
        return [], [f"arXiv search failed for '{query}': {exc}."]
    for response in responses:
        notes.extend(response.notes)
        for result in response.results:
            candidates.append(
                {
                    "provider": "arxiv",
                    "query": query,
                    "title": result.title,
                    "authors": list(result.authors),
                    "identifier": result.locator_value,
                    "snippet": "",
                    "relevance": 0.5 if result.relevance == "high" else 0.3,
                    "local_path": "",
                    "why_relevant": result.why_relevant,
                }
            )
    return candidates, notes


def _rank_candidates(client: LLMClient, claim: dict, candidates: list[dict]) -> None:
    """Optionally enrich why_relevant / relevance via the LLM. Status is never
    touched (candidates stay candidate); unknown ids are ignored; relevance is
    clamped to [0, 1]."""

    if not candidates:
        return
    payload = {
        "claim": claim.get("normalized_statement"),
        "candidates": [
            {"id": c["id"], "title": c["title"], "snippet": c["snippet"]} for c in candidates
        ],
        "required_json_schema": _RANK_OUTPUT_SCHEMA,
        "rules": ["rank and explain only; never assert source_backed or a proof"],
    }
    try:
        raw = client.complete_json(system=_RANK_SYSTEM, prompt=json.dumps(payload, indent=2)) or {}
    except Exception:  # noqa: BLE001 — ranking is advisory; keep deterministic values
        return
    by_id = {c["id"]: c for c in candidates}
    for row in safe_list(raw.get("ranked")):
        if not isinstance(row, dict):
            continue
        cand = by_id.get(safe_text(row.get("id")).strip())
        if cand is None:
            continue
        why = safe_text(row.get("why_relevant")).strip()
        if why:
            cand["why_relevant"] = why[:300]
        try:
            rel = float(row.get("relevance"))
            cand["relevance"] = max(0.0, min(1.0, rel))
        except (TypeError, ValueError):
            pass


def run_autonomous_retrieval(
    run_dir: Path,
    *,
    client: LLMClient,
    corpus_dir: Path | None = None,
    providers: list[str] | None = None,
    ground: bool = True,
    max_results: int = 5,
) -> dict:
    """Plan queries with the LLM, retrieve candidates from providers, rank them,
    write ``autonomous_literature_retrieval.yaml`` (candidate-only), and -- when a
    readable local candidate exists and ``ground`` is set -- hand it to the
    EXISTING grounding pipeline (which alone decides source_backed).

    Returns the report dict augmented with ``grounding`` info. Fails closed: if no
    readable candidate is found, grounding is not attempted and everything stays
    candidate.
    """

    run_dir = Path(run_dir)
    providers = providers or ["local_corpus"]
    plan = plan_literature_search(run_dir=run_dir, client=client)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])

    raw_candidates: list[dict] = []
    notes: list[str] = []
    for query in plan["generated_queries"]:
        if "local_corpus" in providers:
            raw_candidates.extend(_local_candidates(query, corpus_dir, max_results))
        if "arxiv" in providers:
            arxiv_hits, arxiv_notes = _arxiv_candidates(query, max_results)
            raw_candidates.extend(arxiv_hits)
            notes.extend(arxiv_notes)

    # De-duplicate by (provider, identifier-or-title); assign stable ids.
    seen: set[tuple[str, str]] = set()
    candidates: list[dict] = []
    for cand in raw_candidates:
        key = (cand["provider"], cand.get("identifier") or cand.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        cand["id"] = f"auto_{cand['provider']}_{len(candidates):03d}"
        cand["status"] = "candidate"  # pinned; ranking can never change it
        candidates.append(cand)

    _rank_candidates(client, claim, candidates)
    candidates.sort(key=lambda c: c.get("relevance", 0.0), reverse=True)

    if not candidates:
        notes.append("No candidate sources retrieved; nothing handed to grounding (fail closed).")

    # Optional grounding: hand the best READABLE local candidate to grounding.
    grounded = False
    grounding_reason = "grounding not attempted"
    best_local = next((c for c in candidates if c["provider"] == "local_corpus" and c.get("local_path")), None)
    if ground and best_local is not None and corpus_dir is not None:
        result = retrieve_and_ground(run_dir, corpus_dir=Path(corpus_dir), query=best_local["query"])
        grounded = bool(result.get("grounded"))
        grounding_reason = "handed top local candidate to source grounding" if grounded else (
            f"grounding not earned ({result.get('reason', 'see source_grounding')})"
        )
    elif ground and best_local is None:
        grounding_reason = "no readable local candidate to ground (candidates stay candidate)"

    # Read the ACTUAL grounding outcome back from source_grounding; retrieval
    # never decides source_backed itself.
    earned_source_backed = False
    sg_path = run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["source_grounding"]
    if sg_path.exists():
        sg = read_yaml(sg_path)
        earned_source_backed = any(
            step.get("status") == "source_backed" for step in sg.get("steps", [])
        )

    report = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "providers": providers,
        "queries": plan["generated_queries"],
        "candidates": [
            {
                "id": c["id"],
                "provider": c["provider"],
                "query": c["query"],
                "title": c["title"],
                "authors": c.get("authors", []),
                "identifier": c.get("identifier", ""),
                "snippet": c.get("snippet", ""),
                "why_relevant": c.get("why_relevant", ""),
                "relevance": c.get("relevance", 0.0),
                "status": "candidate",
                "local_path": c.get("local_path", ""),
            }
            for c in candidates
        ],
        "grounded": grounded,
        "earned_source_backed": earned_source_backed,
        "notes": notes + [grounding_reason],
    }
    AutonomousLiteratureRetrievalReport.model_validate(report)
    write_yaml(run_dir / "autonomous_literature_retrieval.yaml", report)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["autonomous_literature_retrieval"] = (
        "autonomous_literature_retrieval.yaml"
    )
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    validate_run(run_dir)
    return report
