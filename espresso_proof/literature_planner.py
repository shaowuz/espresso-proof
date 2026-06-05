"""LLM-guided literature search PLANNER (V0).

Given a locked claim (and optional strategy routes), ask an LLM what theorem /
source should be looked for and which search queries to run. The planner
proposes search terms ONLY:

* it writes ``literature_search_plan.yaml`` with search questions, generated
  queries, expected theorem shapes, and risk terms / forbidden upgrades;
* it has NO source/evidence/proof status field and can never earn reference
  support — only Source Grounding (exact locator + hypothesis match +
  attribution) can;
* it always has a deterministic fallback derived from the claim, so the pipeline
  produces usable queries even with a stub client or empty LLM output.

The model output is normalized to plain strings; any status-like field an LLM
might emit is dropped on the floor.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from .io import read_yaml, write_yaml
from .llm_agents import LLMClient, safe_list, safe_text
from .models import LiteratureSearchPlan
from .research_bank import bankable_artifact_path, load_bankable_dict

_PLANNER_SYSTEM = (
    "You are Espresso Proof's literature-search planner. Given a locked "
    "mathematical claim and (optionally) candidate proof routes, propose what "
    "theorem(s)/source(s) should be searched for and concrete search queries. "
    "You ONLY propose search terms: you never assert that any source is found, "
    "checked, or source_backed, and you never invent a theorem locator. Respond "
    "with ONLY a JSON object."
)

_PLANNER_OUTPUT_SCHEMA = {
    "search_questions": ["string: what reference would settle a step"],
    "generated_queries": ["string: concrete search query"],
    "expected_theorem_shapes": ["string: the shape of a theorem that would help"],
    "risk_terms": ["string: wording that must NOT be over-read as support"],
    "notes": ["string"],
}

# Forbidden upgrades surfaced in every plan so the rest of the pipeline (and the
# reader) is reminded retrieval is candidate-only.
_DEFAULT_RISK_TERMS = [
    "a matching title or abstract is not theorem support",
    "an arXiv hit is a candidate, never source_backed",
    "no exact locator means the step stays 'Needs a source'",
]

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "every", "all", "are", "is",
    "of", "a", "an", "to", "be", "where", "which", "then", "such", "from",
}


def _claim_terms(text: str) -> list[str]:
    seen: list[str] = []
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9'^|/()=_-]{2,}", text):
        low = token.lower()
        if low not in _STOPWORDS and token not in seen:
            seen.append(token)
    return seen


def _fallback_queries(claim: dict) -> list[str]:
    """Deterministic queries derived from the claim when the LLM gives nothing."""

    normalized = safe_text(claim.get("normalized_statement") or claim.get("raw_text") or "")
    conclusion = safe_text(claim.get("conclusion") or "")
    queries: list[str] = []
    if normalized:
        queries.append(normalized.strip().rstrip(".")[:120])
    # An objects + conclusion query, then a compact keyword query.
    objects = " ".join(str(o) for o in (claim.get("objects") or []))[:80]
    if objects and conclusion:
        queries.append(f"{objects} {conclusion}".strip()[:120])
    terms = _claim_terms(f"{normalized} {objects} {conclusion}")[:6]
    if terms:
        queries.append(" ".join(terms))
    # De-duplicate, drop empties.
    out: list[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in out:
            out.append(q)
    return out or ["mathematical reference for the locked claim"]


def _route_titles(run_dir: Path) -> list[str]:
    # Resolve via the manifest + bank-aware loader so a materialized
    # (bank-directory) run still contributes strategy route titles to the plan.
    path = bankable_artifact_path(run_dir, "strategy_synthesis")
    if not path.exists():
        return []
    data = load_bankable_dict(run_dir, "strategy_synthesis")
    return [safe_text(r.get("title")) for r in data.get("routes", []) if r.get("title")]


def plan_literature_search(*, run_dir: Path, client: LLMClient) -> dict:
    """Build, normalize, persist, and register a ``literature_search_plan.yaml``.

    Returns the plan dict. Never sets any source/evidence status; the LLM only
    proposes search terms and a deterministic fallback guarantees usable queries.
    """

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])

    payload = {
        "task": "plan_literature_search",
        "claim": {
            "normalized_statement": claim.get("normalized_statement"),
            "objects": claim.get("objects"),
            "hypotheses": [h.get("name") for h in claim.get("hypotheses", [])],
            "conclusion": claim.get("conclusion"),
        },
        "candidate_routes": _route_titles(run_dir),
        "required_json_schema": _PLANNER_OUTPUT_SCHEMA,
        "rules": [
            "propose search terms only; never claim a source is found or checked",
            "never invent a theorem locator; locators come from grounding",
            "list risk terms that must not be over-read as support",
        ],
    }
    raw: dict = {}
    try:
        raw = client.complete_json(system=_PLANNER_SYSTEM, prompt=json.dumps(payload, indent=2)) or {}
    except Exception:  # noqa: BLE001 — planner must fail closed to the fallback
        raw = {}

    queries = [safe_text(q).strip() for q in safe_list(raw.get("generated_queries")) if safe_text(q).strip()]
    if not queries:
        queries = _fallback_queries(claim)
    questions = [safe_text(q).strip() for q in safe_list(raw.get("search_questions")) if safe_text(q).strip()]
    if not questions:
        questions = [f"Which reference states: {q}?" for q in queries[:3]]
    shapes = [safe_text(s).strip() for s in safe_list(raw.get("expected_theorem_shapes")) if safe_text(s).strip()]
    risk_terms = [safe_text(t).strip() for t in safe_list(raw.get("risk_terms")) if safe_text(t).strip()]
    # Always include the forbidden-upgrade reminders (dedup, plan order stable).
    for term in _DEFAULT_RISK_TERMS:
        if term not in risk_terms:
            risk_terms.append(term)
    notes = [safe_text(n).strip() for n in safe_list(raw.get("notes")) if safe_text(n).strip()]

    plan = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "search_questions": questions[:12],
        "generated_queries": queries[:12],
        "expected_theorem_shapes": shapes[:12],
        "risk_terms": risk_terms[:12],
        "notes": notes
        or ["Literature search plan proposes candidate queries only; it earns no reference support."],
    }
    LiteratureSearchPlan.model_validate(plan)
    write_yaml(run_dir / "literature_search_plan.yaml", plan)
    manifest.setdefault("optional_artifacts", {})["literature_search_plan"] = "literature_search_plan.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return plan
