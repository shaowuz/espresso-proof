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

from .claim_clear import ensure_claim_clear_for_downstream
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


def _earned_grounding_bindings(
    run_dir: Path,
    retrieval_source_id: str | None,
) -> tuple[list[str], list[str]]:
    if not retrieval_source_id:
        return [], []
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    extraction_rel = manifest.get("optional_artifacts", {}).get("theorem_extraction")
    if not extraction_rel:
        return [], []
    extraction_path = run_dir / extraction_rel
    grounding_path = run_dir / manifest["artifacts"]["source_grounding"]
    if not extraction_path.exists() or not grounding_path.exists():
        return [], []
    extraction = read_yaml(extraction_path) or {}
    grounding = read_yaml(grounding_path) or {}
    candidate_ref_ids = {
        f"verified_{theorem.get('id')}"
        for theorem in extraction.get("theorems", [])
        if theorem.get("source_ref_id") == retrieval_source_id and theorem.get("id")
    }
    if not candidate_ref_ids:
        return [], []
    refs_by_id = {ref.get("id"): ref for ref in grounding.get("source_refs", [])}
    earned_refs = {
        ref_id
        for ref_id in candidate_ref_ids
        if refs_by_id.get(ref_id, {}).get("status") == "source_backed"
    }
    earned_steps: list[str] = []
    for step in grounding.get("steps", []):
        if step.get("status") != "source_backed":
            continue
        if not any(ref_id in earned_refs for ref_id in step.get("source_refs", [])):
            continue
        step_id = step.get("proof_step_id")
        if step_id:
            earned_steps.append(str(step_id))
    return sorted(earned_refs), list(dict.fromkeys(earned_steps))


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
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
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
    grounded_candidate_id: str | None = None
    grounded_retrieval_source_id: str | None = None
    earned_source_ref_ids: list[str] = []
    earned_proof_step_ids: list[str] = []
    best_local = next((c for c in candidates if c["provider"] == "local_corpus" and c.get("local_path")), None)
    if ground and best_local is not None and corpus_dir is not None:
        expected_hit_id = f"ret_{Path(best_local['local_path']).stem}"
        result = retrieve_and_ground(
            run_dir,
            corpus_dir=Path(corpus_dir),
            query=best_local["query"],
            hit_id=expected_hit_id,
        )
        grounded = bool(result.get("grounded"))
        if grounded:
            grounded_candidate_id = str(best_local["id"])
            grounded_retrieval_source_id = str(result.get("chosen_hit_id") or expected_hit_id)
            earned_source_ref_ids, earned_proof_step_ids = _earned_grounding_bindings(
                run_dir,
                grounded_retrieval_source_id,
            )
        grounding_reason = "handed top local candidate to source grounding" if grounded else (
            f"grounding not earned ({result.get('reason', 'see source_grounding')})"
        )
    elif ground and best_local is None:
        grounding_reason = "no readable local candidate to ground (candidates stay candidate)"

    # Read the ACTUAL grounding outcome back from source_grounding, but only for
    # the candidate/source that this retrieval handed to grounding. Retrieval
    # never decides source_backed itself, and unrelated existing grounding cannot
    # satisfy this report-level flag.
    earned_source_backed = bool(earned_source_ref_ids and earned_proof_step_ids)

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
        "grounded_candidate_id": grounded_candidate_id,
        "grounded_retrieval_source_id": grounded_retrieval_source_id,
        "earned_source_ref_ids": earned_source_ref_ids,
        "earned_proof_step_ids": earned_proof_step_ids,
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
    from .surface_warnings import refresh_final_report_disclosures

    refresh_final_report_disclosures(run_dir)
    validate_run(run_dir)
    return report
