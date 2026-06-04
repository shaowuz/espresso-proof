"""Front-half live-agent pass.

The end-to-end ``run-flow`` pipeline currently fills the front-half artifacts
(locked claim, hypothesis ledger, examples, counterexamples, strategy brief, …)
from ``mock_pipeline``. This module lets those artifacts be (re)produced by a
real reasoning client (substrate A: Claude subagents via the ``external``
adapter, or the deterministic stub) while the Python kernel stays the trusted
validator.

Design (see docs/goals/AGENT_INTEGRATION_BUILD_PLAN.md):

* One ordered list of front-half stages, run in Core-Flow order.
* A registry mapping a stage to a *normalizer* ``callable(run_dir, client,
  context) -> dict``. A normalizer asks the client for content, then converts
  the raw model JSON into the kernel artifact using the existing safety helpers
  — it must NEVER trust model-supplied status fields (no upgrade to
  proved / source_backed / checked / survived).
* Stages with no registered normalizer are reported ``skipped`` and the existing
  (mock) artifact is left untouched, so enabling the pass before every stage is
  implemented can never corrupt a run.
* After every stage the run is re-validated with the kernel; on any validation
  error the pass fails closed (``strict=True``).

Slices S1–S8 each register one (or a small group of) stage normalizers.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .io import write_yaml
from .llm_agents import LLMClient, load_agent_context, safe_list, safe_text
from .models import Claim, HypothesisLedger
from .validators import validate_run

# Valid hypothesis statuses (kernel enum). A model-supplied status outside this
# set is coerced to "unclear" — never trusted verbatim.
_HYPOTHESIS_STATUSES = {"explicit", "inferred", "missing", "unclear"}

# Front-half stages in Core-Flow order. Names are stable identifiers used by the
# registry, the CLI ``--front-half`` plumbing, and the orchestrator.
FRONT_HALF_STAGES: list[str] = [
    "understanding_claim",  # S1: locked_claim + hypothesis_ledger
    "literature_review",    # S2: candidate references only
    "examples",             # S3: examples + computation_checks
    "counterexamples",      # S4: counterexamples + admissibility tests
    "strategy",             # S5: strategy generator + grader + brief
    "attacker",             # S6: example/counterexample attacker
    "source_theorem",       # S7: source matching + theorem applicability
    "grade_memory_memo",    # S8: grade + memory + concise answer
]

# stage name -> normalizer(run_dir=..., client=..., context=...) -> dict
# A normalizer returns an outcome dict; recognized keys: "unsafe_language" (list)
# and "notes" (list). Slices populate this registry via ``register_stage``.
StageNormalizer = Callable[..., dict]
_STAGE_REGISTRY: dict[str, StageNormalizer] = {}


def register_stage(name: str) -> Callable[[StageNormalizer], StageNormalizer]:
    """Decorator: register a normalizer for a front-half stage."""

    if name not in FRONT_HALF_STAGES:
        raise ValueError(f"unknown front-half stage: {name}")

    def _decorator(fn: StageNormalizer) -> StageNormalizer:
        _STAGE_REGISTRY[name] = fn
        return fn

    return _decorator


def registered_stages() -> list[str]:
    """Stages that currently have a normalizer, in Core-Flow order."""

    return [stage for stage in FRONT_HALF_STAGES if stage in _STAGE_REGISTRY]


def run_front_half_agent_pass(
    *,
    run_dir: Path,
    client: LLMClient,
    stages: list[str] | None = None,
    strict: bool = True,
) -> dict:
    """Run the front-half stages, validating the run after each.

    Unimplemented stages are skipped (mock artifact untouched). With
    ``strict=True`` (default) any normalizer error or post-stage validation
    error aborts the pass — the kernel is the gate, so we fail closed rather
    than leave a run in an unvalidated state.
    """

    stage_list = list(stages) if stages is not None else list(FRONT_HALF_STAGES)
    generated_at = datetime.now(UTC).isoformat()
    results: list[dict] = []

    for stage in stage_list:
        normalizer = _STAGE_REGISTRY.get(stage)
        if normalizer is None:
            results.append(
                {"stage": stage, "status": "skipped", "errors": [], "unsafe_language": []}
            )
            continue
        try:
            context = load_agent_context(run_dir)
            outcome = normalizer(run_dir=run_dir, client=client, context=context) or {}
            validate_run(run_dir)
        except Exception as exc:  # noqa: BLE001 - fail closed, record, optionally re-raise
            results.append(
                {"stage": stage, "status": "error", "errors": [str(exc)], "unsafe_language": []}
            )
            if strict:
                raise
            continue
        results.append(
            {
                "stage": stage,
                "status": "ok",
                "errors": [],
                "unsafe_language": list(outcome.get("unsafe_language", [])),
            }
        )

    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "provider": getattr(client, "provider", "unknown"),
        "model": getattr(client, "model", "unknown"),
        "stages": results,
        "notes": [
            "Front-half agent pass; outputs are candidate planning artifacts only.",
            "This pass must not upgrade source_backed, checked evidence, or proof status.",
            "Every stage is re-validated by the Python kernel before being accepted.",
        ],
    }


# ---------------------------------------------------------------------------
# S1 — 01 Understanding Claim
# ---------------------------------------------------------------------------

_UNDERSTANDING_SYSTEM = (
    "You are the Espresso Proof Understanding-Claim agent. Turn the user's "
    "mathematical claim into a precise locked claim plus a hypothesis ledger. "
    "Preserve quantifiers EXACTLY (all / exists / generic / very general / "
    "special); never silently narrow 'all' to 'very general'. List every "
    "ambiguous term and every missing or unclear hypothesis instead of guessing. "
    "You may PROPOSE a precise statement, but you must NOT mark it "
    "human-confirmed and must NOT infer any proof, source, or evidence status. "
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


@register_stage("understanding_claim")
def _understanding_claim(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    current = dict(context["claim"])

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
    hypotheses = _normalize_hypotheses(
        raw.get("hypotheses"), fallback=list(current.get("hypotheses") or [])
    )
    ambiguous_terms = safe_list(raw.get("ambiguous_terms")) or list(
        current.get("ambiguous_terms") or []
    )
    missing_or_unclear = safe_list(raw.get("missing_or_unclear"))
    notes = safe_list(raw.get("notes"))

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
    }
    HypothesisLedger.model_validate(ledger)

    write_yaml(run_dir / artifacts["locked_claim"], claim)
    write_yaml(run_dir / artifacts["hypothesis_ledger"], ledger)
    return {
        "unsafe_language": [],
        "notes": ["understanding_claim refreshed locked_claim + hypothesis_ledger"],
    }
