"""Front-half stage: literature (Phase 04 physical split)."""

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


_LITERATURE_SYSTEM = (
    "You are the Espresso Proof Literature-Review agent. Propose CANDIDATE "
    "references relevant to the locked claim. You may ONLY propose candidates: "
    "never mark anything source_backed or misapplied — Referencing/Source "
    "Grounding owns that. Include an exact locator when you genuinely know it, "
    "otherwise mark the locator kind 'unknown'. Distinguish a theorem's "
    "statement from your inference about it. Respond with ONLY a JSON object."
)

_LITERATURE_OUTPUT_SCHEMA = {
    "query_summary": "string",
    "candidate_references": [
        {
            "title": "string",
            "authors": ["string"],
            "locator": {"kind": "theorem|section|page|arxiv_id|url|unknown", "value": "string"},
            "relevance": "core|background|example_source|counterexample_source|possible_theorem_source",
            "status": "candidate|unverified|not_found",
            "why_relevant": "string",
        }
    ],
    "known_examples": ["string"],
    "known_counterexample_families": ["string"],
    "notes": ["string"],
}


def _candidate_status(value: object) -> str:
    """Force any model source status into a candidate-only value."""

    if isinstance(value, str) and value in _CANDIDATE_SOURCE_STATUSES:
        return value
    # source_backed / misapplied / anything unknown -> downgraded to candidate.
    return "candidate"


def _normalize_literature_items(raw_items: object) -> list[dict]:
    items: list[dict] = []
    if not isinstance(raw_items, list):
        return items
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            continue
        title = safe_text(raw.get("title", "")).strip()
        if not title or title == "No usable agent text returned.":
            continue
        relevance = raw.get("relevance")
        if relevance not in _LITERATURE_RELEVANCE:
            relevance = "background"
        items.append(
            {
                "id": f"lit_{index:03d}",
                "title": title,
                "authors": safe_list(raw.get("authors")),
                "locator": _normalize_locator(raw.get("locator")),
                "relevance": relevance,
                "status": _candidate_status(raw.get("status")),
                "why_relevant": safe_text(raw.get("why_relevant", "")) or "candidate reference",
                "found_by": "literature_review",  # agent attribution; never user/source
            }
        )
    return items


@register_stage("literature_review")
def _literature_review(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    claim = context["claim"]
    current = dict(context.get("literature_review") or {})

    payload = {
        "task": "propose_candidate_references",
        "claim": {
            "normalized_statement": claim.get("normalized_statement", ""),
            "objects": claim.get("objects", []),
            "conclusion": claim.get("conclusion", ""),
        },
        "required_json_schema": _LITERATURE_OUTPUT_SCHEMA,
        "rules": [
            "candidate references only; never source_backed or misapplied",
            "exact locator only when genuinely known, else kind 'unknown'",
        ],
    }
    raw = client.complete_json(
        system=_LITERATURE_SYSTEM, prompt=json.dumps(payload, indent=2)
    ) or {}

    items = _normalize_literature_items(raw.get("candidate_references"))
    report = {
        "claim_id": claim["id"],
        "query_summary": safe_text(
            raw.get("query_summary") or current.get("query_summary") or "Candidate literature triage."
        ),
        "candidate_references": items,
        "known_examples": safe_list(raw.get("known_examples")),
        "known_counterexample_families": safe_list(raw.get("known_counterexample_families")),
        "notes": safe_list(raw.get("notes"))
        or ["Literature Review proposes candidates only; Source Grounding decides source_backed."],
    }
    LiteratureReviewReport.model_validate(report)
    write_yaml(run_dir / artifacts["literature_review"], report)

    downgraded = sum(
        1
        for raw_item in (raw.get("candidate_references") or [])
        if isinstance(raw_item, dict)
        and raw_item.get("status") not in _CANDIDATE_SOURCE_STATUSES
    )
    notes = ["literature_review wrote candidate references only"]
    if downgraded:
        notes.append(f"downgraded {downgraded} non-candidate status(es) to candidate")
    return {"unsafe_language": [], "notes": notes}


# ---------------------------------------------------------------------------
# S3 — 03 Examples (+ computation_checks)
# ---------------------------------------------------------------------------

