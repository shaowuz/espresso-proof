"""Front-half stage: understanding (Phase 04 physical split)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ...claim_normalization import normalize_simple_claim
from ...hypothesis_normalization import normalize_ambient_object_hypotheses
from ...io import read_yaml, write_yaml
from ...llm_agents import LLMClient, load_agent_context, safe_list, safe_text
from ...memo import build_concise_memo
from ...memory import write_run_memory
from ...models import (
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
from ...strategy_rules import (
    HIGH_SCORE_THRESHOLD,
    STANDARD_SCORE_LIFT_LIMIT,
    collect_strategy_rule_violations,
)
from ...validators import validate_run
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


_UNDERSTANDING_SYSTEM = (
    "You are the Espresso Proof Understanding-Claim agent. Turn the user's "
    "mathematical claim into a precise locked claim plus a hypothesis ledger. "
    "Preserve quantifiers EXACTLY (all / exists / generic / very general / "
    "special); never silently narrow 'all' to 'very general'. List every "
    "ambiguous term and every missing or unclear hypothesis instead of guessing. "
    "You may PROPOSE a precise statement, but you must NOT mark it "
    "human-confirmed and must NOT infer any proof, source, or evidence status. "
    "Also judge claim_precision: 'precise' if the objects, hypotheses, and "
    "conclusion are well-defined enough to start studying, else 'imprecise'. "
    "Respond with ONLY a JSON object."
)

_UNDERSTANDING_OUTPUT_SCHEMA = {
    "normalized_statement": "string: precise restatement preserving quantifiers",
    "objects": ["string: the mathematical objects in scope"],
    "hypotheses": [
        {"name": "string", "status": "explicit|inferred|missing|unclear"}
    ],
    "conclusion": "string: the precise conclusion",
    "ambiguous_terms": ["string: ambiguous or undefined terms"],
    "missing_or_unclear": ["string: parts not yet precise"],
    "claim_precision": "precise|imprecise (is the claim precise enough to study?)",
    "notes": ["string"],
}


def _normalize_hypotheses(raw_hyps: object, *, fallback: list) -> list[dict]:
    """Coerce model hypotheses into the kernel shape.

    Safety: status is forced into the kernel enum (unknown -> "unclear") and
    ``source`` is ALWAYS "agent" — the Understanding-Claim agent identifies
    hypotheses; only Source Grounding (06) may attribute them to a source.
    """

    result: list[dict] = []
    if isinstance(raw_hyps, list):
        for item in raw_hyps:
            if not isinstance(item, dict):
                continue
            name = safe_text(item.get("name", "")).strip()
            if not name or name == "No usable agent text returned.":
                continue
            status = item.get("status")
            if status not in _HYPOTHESIS_STATUSES:
                status = "unclear"
            result.append({"name": name, "status": status, "source": "agent"})
    if not result:
        for item in fallback:
            if not isinstance(item, dict):
                continue
            status = item.get("status")
            if status not in _HYPOTHESIS_STATUSES:
                status = "unclear"
            result.append(
                {"name": item.get("name", "hypothesis"), "status": status, "source": "agent"}
            )
    if not result:
        result = [{"name": "claim hypotheses", "status": "unclear", "source": "agent"}]
    return result


_IMPRECISE_CONCLUSION_MARKERS = (
    "unclear",
    "requires clarification",
    "not precise",
    "to be determined",
)
_IMPRECISE_OBJECT_MARKERS = ("unspecified", "does something", "nice", "mirror")
_CORE_GAP_TERMS = {
    "objects",
    "main objects",
    "object",
    "hypotheses",
    "hypothesis",
    "conclusion",
    "claim",
    "statement",
}


def _looks_imprecise(
    *, conclusion: str, objects: list[str], missing_or_unclear: list[str]
) -> bool:
    """Conservative safety floor for precision (cannot be over-ridden upward)."""

    text = (conclusion or "").strip().lower()
    if not text or any(marker in text for marker in _IMPRECISE_CONCLUSION_MARKERS):
        return True
    joined_objects = " ".join(str(o) for o in (objects or [])).lower()
    if not joined_objects or any(
        marker in joined_objects for marker in _IMPRECISE_OBJECT_MARKERS
    ):
        return True
    if {str(item).strip().lower() for item in (missing_or_unclear or [])} & _CORE_GAP_TERMS:
        return True
    return False


@register_stage("understanding_claim")
def _understanding_claim(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    current = dict(context["claim"])
    current_ledger = dict(context.get("hypothesis_ledger") or {})

    raw_text = str(current["raw_text"])
    payload = {
        "task": "understand_and_lock_claim",
        "raw_claim": raw_text,
        "current_normalized": current.get("normalized_statement", ""),
        "required_json_schema": _UNDERSTANDING_OUTPUT_SCHEMA,
        "rules": [
            "preserve quantifiers; never silently narrow 'all' to 'very general'",
            "list ambiguous terms and missing hypotheses instead of guessing",
            "do not mark human_confirmed; do not infer proof/source/evidence status",
        ],
    }
    raw = client.complete_json(
        system=_UNDERSTANDING_SYSTEM, prompt=json.dumps(payload, indent=2)
    ) or {}

    normalized = safe_text(
        raw.get("normalized_statement")
        or current.get("normalized_statement")
        or raw_text
    )[:240] or "Unclear claim."
    objects = safe_list(raw.get("objects")) or list(current.get("objects") or []) or [
        "unspecified objects"
    ]
    conclusion = safe_text(
        raw.get("conclusion")
        or current.get("conclusion")
        or "Claim conclusion requires clarification."
    )
    hypotheses = normalize_ambient_object_hypotheses(
        _normalize_hypotheses(
            raw.get("hypotheses"), fallback=list(current.get("hypotheses") or [])
        )
    )
    ambiguous_terms = safe_list(raw.get("ambiguous_terms")) or list(
        current.get("ambiguous_terms") or []
    )
    if "missing_or_unclear" in raw:
        missing_or_unclear = safe_list(raw.get("missing_or_unclear"))
    else:
        missing_or_unclear = list(current_ledger.get("missing_or_unclear") or [])
    notes = safe_list(raw.get("notes"))

    # Agent-supplied precision judgement, with a conservative safety floor: even
    # if the agent says "precise", a placeholder conclusion / unspecified objects
    # / a core missing piece forces "imprecise". Advisory only — gates activation,
    # never upgrades any proof/source/evidence status.
    claim_precision = raw.get("claim_precision")
    if claim_precision not in {"precise", "imprecise"}:
        claim_precision = current_ledger.get("claim_precision")
    if claim_precision not in {"precise", "imprecise", "unclear"}:
        claim_precision = "unclear"
    if claim_precision == "precise" and _looks_imprecise(
        conclusion=conclusion, objects=objects, missing_or_unclear=missing_or_unclear
    ):
        claim_precision = "imprecise"

    # Goal 66: conservative fast-lock for recognized simple elementary claims.
    # If the (stub/weak) understanding left the claim imprecise but the raw text
    # is a high-confidence recognized pattern (a prime/concrete-order group is
    # cyclic), lock a precise claim so the claim-clear gate is not brittle for
    # standard phrasings. Advisory only: proposes a locked claim (still NOT
    # human_confirmed) and upgrades NO proof/source/evidence/refutation status.
    # The _looks_imprecise safety floor stays authoritative — the normalizer can
    # never push through something the floor would reject.
    if claim_precision != "precise":
        norm = normalize_simple_claim(raw_text)
        if norm is not None and not _looks_imprecise(
            conclusion=norm["conclusion"],
            objects=norm["objects"],
            missing_or_unclear=norm["missing_or_unclear"],
        ):
            normalized = norm["normalized_statement"][:240]
            objects = norm["objects"]
            conclusion = norm["conclusion"]
            hypotheses = normalize_ambient_object_hypotheses(
                _normalize_hypotheses(norm["hypotheses"], fallback=[])
            )
            missing_or_unclear = norm["missing_or_unclear"]
            claim_precision = "precise"
            notes = notes + [
                "claim normalized by the Goal-66 simple-claim normalizer "
                "(advisory; locks a precise claim only; no proof/source/evidence "
                "status implied)"
            ]

    claim = {
        "id": current["id"],          # preserve id so downstream artifacts still resolve
        "raw_text": raw_text,         # never overwrite the user's original text
        "normalized_statement": normalized,
        "objects": objects,
        "hypotheses": hypotheses,
        "conclusion": conclusion,
        "ambiguous_terms": ambiguous_terms,
        "locked": True,               # the kernel requires a locked claim
        "repaired_from": current.get("repaired_from"),
        "human_confirmed": False,     # an agent can NEVER self-confirm a claim
    }
    Claim.model_validate(claim)

    ledger = {
        "claim_id": current["id"],
        "hypotheses": hypotheses,
        "missing_or_unclear": missing_or_unclear,
        "notes": notes
        or [
            "Locked claim proposed by the Understanding-Claim agent; not human-confirmed."
        ],
        "claim_precision": claim_precision,
    }
    HypothesisLedger.model_validate(ledger)

    write_yaml(run_dir / artifacts["locked_claim"], claim)
    write_yaml(run_dir / artifacts["hypothesis_ledger"], ledger)
    return {
        "unsafe_language": [],
        "notes": ["understanding_claim refreshed locked_claim + hypothesis_ledger"],
    }


# ---------------------------------------------------------------------------
# S2 — 02 Literature Review (candidate references only)
# ---------------------------------------------------------------------------
