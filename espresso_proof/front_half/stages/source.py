"""Front-half stage: source (Phase 04 physical split)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.claim.hypothesis_normalization import normalize_ambient_object_hypotheses
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.agents.llm_agents import LLMClient, load_agent_context, safe_list, safe_text
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.memory.memory import write_run_memory
from espresso_proof.kernel.models import (
    Claim,
    ComputationChecks,
    CounterexampleReport,
    ExampleSelection,
    FrontHalfRunReport,
    HypothesisLedger,
    IdeaGrade,
    LiteratureReviewReport,
    Review,
    SourceGroundingReport,
    StrategyBrief,
    StrategySynthesis,
    TestReport,
)
from espresso_proof.kernel.strategy_rules import (
    HIGH_SCORE_THRESHOLD,
    STANDARD_SCORE_LIFT_LIMIT,
    collect_strategy_rule_violations,
)
from espresso_proof.kernel.validators import validate_run
from ..registry import register_stage
from ..safety import (
    _CANDIDATE_SOURCE_STATUSES,
    _EXPLORATION_MODES,
    _HYPOTHESIS_STATUSES,
    _IDEA_STATUSES,
    _LITERATURE_RELEVANCE,
    _LOCATOR_KINDS,
    _NONTRIVIALITY,
    _coerce_satisfies,
    _default_investigation_route,
    _neutralize_review_if_failed,
    _normalize_locator,
    _safe_lock_route,
)


_SOURCE_TYPES = {"paper", "book", "local_note", "theorem_database", "mathlib", "web_page"}

_SOURCE_THEOREM_SYSTEM = (
    "You are the Espresso Proof Source-Matching agent. For the theorems the "
    "strategy needs, identify CANDIDATE sources and assess whether the cited "
    "theorem's hypotheses would match the locked claim. CRITICAL: you may NOT "
    "mark anything source_backed — source_backed requires an exact locator AND a "
    "verified hypothesis match, which only the verification adapter can confirm. "
    "Never silently treat a 'very general' theorem as covering 'all' objects. "
    "Respond with ONLY a JSON object."
)

_SOURCE_THEOREM_OUTPUT_SCHEMA = {
    "source_refs": [
        {
            "type": "paper|book|local_note|theorem_database|mathlib|web_page",
            "title": "string",
            "authors": ["string"],
            "locator": {"kind": "theorem|section|page|arxiv_id|url|unknown", "value": "string"},
            "statement_or_summary": "string",
            "hypothesis_match_note": "string",
        }
    ],
    "steps": [
        {
            "proof_step_id": "string",
            "needed_theorem": "string",
            "uses_source_index": "int (1-based index into source_refs)",
            "hypothesis_match_note": "string",
            "note": "string",
        }
    ],
}


def _normalize_source_refs(raw_refs: object) -> list[dict]:
    refs: list[dict] = []
    if not isinstance(raw_refs, list):
        return refs
    for index, raw in enumerate(raw_refs, start=1):
        if not isinstance(raw, dict):
            continue
        title = safe_text(raw.get("title", "")).strip()
        if not title or title == "No usable agent text returned.":
            continue
        ref_type = raw.get("type")
        if ref_type not in _SOURCE_TYPES:
            ref_type = "paper"
        refs.append(
            {
                "id": f"src_{index:03d}",
                "type": ref_type,
                "title": title,
                "authors": safe_list(raw.get("authors")),
                "locator": _normalize_locator(raw.get("locator")),
                "statement_or_summary": safe_text(raw.get("statement_or_summary", ""))
                or "candidate source; not yet verified",
                # SAFETY: hypotheses are NOT verified by the front half.
                "hypothesis_match": {"matches": "unclear", "mismatches": []},
                "confidence": "low",
                # SAFETY: never source_backed; only the verifier can set that.
                "status": "candidate",
            }
        )
    return refs


def _normalize_source_steps(raw_steps: object, *, ref_ids: list[str]) -> list[dict]:
    steps: list[dict] = []
    if not isinstance(raw_steps, list):
        return steps
    for index, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, dict):
            continue
        needed = safe_text(raw.get("needed_theorem", "")).strip()
        if not needed or needed == "No usable agent text returned.":
            continue
        # Resolve the cited source by 1-based index, if valid.
        used_refs: list[str] = []
        idx = raw.get("uses_source_index")
        if isinstance(idx, int) and 1 <= idx <= len(ref_ids):
            used_refs = [ref_ids[idx - 1]]
        proof_step_id = safe_text(raw.get("proof_step_id", "")).strip() or f"step_{index:03d}"
        steps.append(
            {
                "proof_step_id": proof_step_id,
                "needed_theorem": needed,
                # SAFETY: candidate only; source_backed requires verification.
                "status": "candidate",
                "source_refs": used_refs,
                "hypothesis_match": {"matches": "unclear", "mismatches": []},
                "note": safe_text(raw.get("note", ""))
                or "candidate source match; hypotheses not yet verified",
            }
        )
    return steps


@register_stage("source_theorem")
def _source_theorem(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    claim = context["claim"]
    claim_id = claim["id"]
    literature = (context.get("literature_review") or {}).get("candidate_references", [])
    strategy = (context.get("strategy_brief") or {}).get("top_strategies", [])

    payload = {
        "task": "match_sources_and_assess_theorem_applicability",
        "claim": {
            "normalized_statement": claim.get("normalized_statement", ""),
            "objects": claim.get("objects", []),
        },
        "candidate_literature": [
            {"title": r.get("title", ""), "why_relevant": r.get("why_relevant", "")}
            for r in literature
        ],
        "strategy_routes": [s.get("title", "") for s in strategy],
        "required_json_schema": _SOURCE_THEOREM_OUTPUT_SCHEMA,
        "rules": [
            "never mark anything source_backed (verification is separate)",
            "do not treat a very-general theorem as covering all objects",
        ],
    }
    raw = client.complete_json(
        system=_SOURCE_THEOREM_SYSTEM, prompt=json.dumps(payload, indent=2)
    ) or {}

    refs = _normalize_source_refs(raw.get("source_refs"))
    ref_ids = [r["id"] for r in refs]
    steps = _normalize_source_steps(raw.get("steps"), ref_ids=ref_ids)

    report = {"claim_id": claim_id, "source_refs": refs, "steps": steps}
    SourceGroundingReport.model_validate(report)
    write_yaml(run_dir / artifacts["source_grounding"], report)
    return {
        "unsafe_language": [],
        "notes": [
            f"source_theorem wrote {len(refs)} candidate source(s), {len(steps)} step "
            "match(es) (all candidate; no source_backed; no theorem_applicability "
            "audit needed since no step is source_backed)"
        ],
    }


# ---------------------------------------------------------------------------
# S8 — 20 Grade + 21 Memory + 22 Concise Answer
# ---------------------------------------------------------------------------

