"""Front-half stage: understanding (Phase 04 physical split)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.claim.claim_clarity import CORE_GAP_TERMS as _CORE_GAP_TERMS
from espresso_proof.claim.claim_normalization import normalize_simple_claim
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


_UNDERSTANDING_SYSTEM = (
    "You are the Espresso Proof Understanding-Claim agent. Turn the user's "
    "mathematical claim into a precise locked claim plus a hypothesis ledger. "
    "Do not solve the problem. Build an auditable claim card: identify the task "
    "type, separate objects from hypotheses, separate the conclusion from any "
    "possible route, and preserve quantifiers EXACTLY (all / exists / generic / "
    "very general / special). Never silently narrow 'all' to 'very general', "
    "change the base field/category, add an explicit-construction requirement, "
    "or replace an abstract isomorphism claim by a representation claim unless "
    "the user said so. Expand compressed standard mathematical English only "
    "when the expansion is forced by the words used, and record that choice in "
    "notes. If two readings are materially possible, list the ambiguity and keep "
    "the claim conservative. Use missing_or_unclear only for real blockers to "
    "starting literature/examples/strategy work, not for routine source needs. "
    "You may PROPOSE a precise statement, but you must NOT mark it "
    "human-confirmed and must NOT infer any proof, source, evidence, theorem, "
    "grade, or refutation status. Also judge claim_precision: 'precise' if the "
    "objects, hypotheses, and conclusion are well-defined enough to start "
    "studying, else 'imprecise'. "
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
# _CORE_GAP_TERMS is imported from claim_clarity (single source of truth
# shared with the claim-clear gate) — see the import at the top of this module.


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


_CLARIFIER_SCHEMA = {
    "type": "object",
    "required": ["normalized_statement", "objects", "hypotheses", "conclusion", "resolutions"],
    "properties": {
        "normalized_statement": {"type": "string"},
        "objects": {"type": "array", "items": {"type": "string"}},
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "status": {"type": "string"}},
            },
        },
        "conclusion": {"type": "string"},
        "resolutions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["gap", "assumption"],
                "properties": {"gap": {"type": "string"}, "assumption": {"type": "string"}},
            },
        },
    },
}


def _clarify_claim(
    *,
    client: LLMClient,
    raw_text: str,
    normalized: str,
    missing_or_unclear: list,
    ambiguous_terms: list,
) -> dict | None:
    """One completion attempt for an unclear claim, or None (fail closed).

    The contract mirrors the simple-claim normalizer's safety posture: this can
    only propose a PRECISE locked claim — it upgrades no status, and "precise"
    does not mean "true" (an open or false statement clarified to a definite
    reading is exactly as studyable as before). Each listed gap must be resolved
    by an explicit assumption, and the (gap, assumption) pairs are returned so
    the ledger records what was decided on the human's behalf.
    """

    payload = {
        "task": "clarify_and_complete_claim",
        "raw_claim": raw_text,
        "current_normalized": normalized,
        "gaps_to_resolve": [str(item) for item in (missing_or_unclear or [])][:8],
        "ambiguous_terms": [str(item) for item in (ambiguous_terms or [])][:8],
        "required_json_schema": _CLARIFIER_SCHEMA,
        "rules": [
            "produce ONE fully precise mathematical statement completing the claim",
            "resolve every listed gap by choosing the STANDARD reading and stating it as an explicit assumption",
            "'the truth value is unknown / the problem is open' is NOT a gap: precision is not truth; drop such items with assumption 'none needed: openness is not imprecision'",
            "never weaken the claim to a triviality and never change what the user asked",
            "do not mark human_confirmed; do not infer proof/source/evidence status",
        ],
    }
    try:
        raw = client.complete_json(
            system=(
                "You are Espresso Proof's claim clarifier. The understanding pass "
                "found gaps in a claim; your job is to complete it into one precise "
                "statement, resolving each gap with the standard reading, stated as "
                "an explicit assumption. Return JSON only."
            ),
            prompt=json.dumps(payload, indent=2),
        ) or {}
    except Exception:  # noqa: BLE001 - clarification is best-effort; parking is the fallback
        return None
    statement = safe_text(raw.get("normalized_statement", ""))[:240].strip()
    conclusion = safe_text(raw.get("conclusion", ""))[:240].strip()
    objects = [safe_text(item)[:160] for item in safe_list(raw.get("objects")) if str(item).strip()]
    hypotheses = raw.get("hypotheses") or []
    resolutions: list[tuple[str, str]] = []
    for item in raw.get("resolutions") or []:
        if isinstance(item, dict):
            gap = safe_text(item.get("gap", ""))[:200].strip()
            assumption = safe_text(item.get("assumption", ""))[:200].strip()
            if gap and assumption:
                resolutions.append((gap, assumption))
    # Fail closed on anything the safety floor would reject, on an empty
    # completion, and on a reply that resolved nothing it was asked to resolve.
    if not statement or not conclusion or not objects:
        return None
    if missing_or_unclear and not resolutions:
        return None
    if _looks_imprecise(
        conclusion=conclusion, objects=objects, missing_or_unclear=[]
    ):
        return None
    return {
        "normalized_statement": statement,
        "objects": objects,
        "hypotheses": hypotheses,
        "conclusion": conclusion,
        "resolutions": resolutions,
    }


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
            "identify task type before rewriting: theorem, existence, construction, counterexample, computation, classification, equivalence, implication, or exploration",
            "separate objects, hypotheses, and conclusion; do not put a proof route or source theorem into the conclusion",
            "expand compressed standard mathematical English only when forced by the wording, and explain the expansion in notes",
            "list ambiguous terms and real missing hypotheses instead of guessing",
            "do not block on routine future source needs; source grounding happens later",
            "do not mark human_confirmed; do not infer proof/source/evidence/theorem/grade/refutation status",
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

    # Conservative fast-lock for recognized simple elementary claims.
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
                "claim normalized by the simple-claim normalizer "
                "(advisory; locks a precise claim only; no proof/source/evidence "
                "status implied)"
            ]

    # Auto-clarification: an unclear claim used to park the whole run at
    # WAITING_CLAIM_CLARIFICATION and ask the human to rewrite it — observed
    # live on "the Jacobian determinant claim", where one listed gap was a real
    # ambiguity (polynomial inverse vs set-theoretic bijection) and the other
    # was not a precision issue at all ("the conjecture is open": precision is
    # not truth). The agent that FOUND the gaps can resolve them: one more call
    # proposes the completed precise statement, resolving each gap with the
    # standard reading recorded as an explicit assumption. The run proceeds and
    # the completed statement is shown to the human, who can revise it; no
    # feedback means continue. Fail closed: if the completion still trips the
    # safety floor, the old parking behavior stands. Provenance is honest —
    # raw_text is never overwritten, human_confirmed stays False, and every
    # resolved gap is recorded next to the assumption that resolved it.
    if claim_precision != "precise" or missing_or_unclear:
        clarified = _clarify_claim(
            client=client,
            raw_text=raw_text,
            normalized=normalized,
            missing_or_unclear=missing_or_unclear,
            ambiguous_terms=ambiguous_terms,
        )
        if clarified is not None:
            normalized = clarified["normalized_statement"][:240]
            objects = clarified["objects"]
            conclusion = clarified["conclusion"]
            hypotheses = normalize_ambient_object_hypotheses(
                _normalize_hypotheses(clarified["hypotheses"], fallback=hypotheses)
            )
            notes = notes + [
                "claim auto-clarified by the understanding agent "
                "(agent-proposed completion; not human-confirmed; revise the run "
                "to override)"
            ] + [
                f"auto-resolved: {gap} -> assumed: {assumption}"
                for gap, assumption in clarified["resolutions"]
            ]
            missing_or_unclear = []
            claim_precision = "precise"

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
