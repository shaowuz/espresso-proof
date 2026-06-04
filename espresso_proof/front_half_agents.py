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
from .models import (
    Claim,
    ComputationChecks,
    CounterexampleReport,
    ExampleSelection,
    HypothesisLedger,
    LiteratureReviewReport,
    SourceGroundingReport,
    StrategyBrief,
    StrategySynthesis,
    TestReport,
)
from .strategy_rules import (
    HIGH_SCORE_THRESHOLD,
    STANDARD_SCORE_LIFT_LIMIT,
    collect_strategy_rule_violations,
)
from .validators import validate_run

# Valid hypothesis statuses (kernel enum). A model-supplied status outside this
# set is coerced to "unclear" — never trusted verbatim.
_HYPOTHESIS_STATUSES = {"explicit", "inferred", "missing", "unclear"}

# Literature Review may ONLY propose candidates. Any model-supplied source
# status outside this set (notably source_backed / misapplied) is downgraded to
# "candidate" — only Source Grounding (06) may set source_backed.
_CANDIDATE_SOURCE_STATUSES = {"candidate", "unverified", "not_found"}
_LOCATOR_KINDS = {
    "theorem", "proposition", "lemma", "corollary", "definition",
    "section", "page", "arxiv_id", "url", "unknown",
}
_LITERATURE_RELEVANCE = {
    "core", "background", "example_source",
    "counterexample_source", "possible_theorem_source",
}
_NONTRIVIALITY = {"routine", "standard", "literature_counterexample", "research_level"}

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


# ---------------------------------------------------------------------------
# S2 — 02 Literature Review (candidate references only)
# ---------------------------------------------------------------------------

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


def _normalize_locator(raw: object) -> dict:
    if isinstance(raw, dict):
        kind = raw.get("kind")
        value = safe_text(raw.get("value", "")).strip()
        if kind not in _LOCATOR_KINDS:
            kind = "unknown"
        if not value:
            kind, value = "unknown", "not specified"
        return {"kind": kind, "value": value}
    return {"kind": "unknown", "value": "not specified"}


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

_EXAMPLES_SYSTEM = (
    "You are the Espresso Proof Examples agent. Propose CANDIDATE examples that "
    "test the locked claim. For each example state whether it satisfies ALL "
    "claim hypotheses, and what it tests. You may NOT declare any example a "
    "checked/survived result and you may NOT mark any computation as checked: "
    "computations you propose still need verification. Respond with ONLY a JSON "
    "object."
)

_EXAMPLES_OUTPUT_SCHEMA = {
    "examples": [
        {
            "name": "string",
            "satisfies_all_claim_hypotheses": "true|false|unclear",
            "hypothesis_mismatches": ["string"],
            "what_it_tests": ["string"],
            "expected_conclusion": "string",
            "observed_or_inferred_behavior": "string",
            "nontriviality": "routine|standard|literature_counterexample|research_level",
            "source_or_reason": "string",
        }
    ],
    "computation_checks": [
        {
            "example_name": "string (must match one example name)",
            "computed_quantity": "string",
            "input_data": ["string"],
            "result": "string",
            "notes": "string",
        }
    ],
}


def _coerce_satisfies(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "yes"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "no"}:
        return False
    return "unclear"


def _normalize_examples(raw_examples: object, *, claim_id: str) -> list[dict]:
    examples: list[dict] = []
    if not isinstance(raw_examples, list):
        return examples
    for index, raw in enumerate(raw_examples, start=1):
        if not isinstance(raw, dict):
            continue
        name = safe_text(raw.get("name", "")).strip()
        if not name or name == "No usable agent text returned.":
            continue
        nontriviality = raw.get("nontriviality")
        if nontriviality not in _NONTRIVIALITY:
            nontriviality = "standard"
        what_it_tests = safe_list(raw.get("what_it_tests")) or ["tests the locked claim"]
        examples.append(
            {
                "id": f"ex_{index:03d}",
                "template_id": "agent_example",
                "name": name,
                "satisfies_all_claim_hypotheses": _coerce_satisfies(
                    raw.get("satisfies_all_claim_hypotheses")
                ),
                "hypothesis_mismatches": safe_list(raw.get("hypothesis_mismatches")),
                "what_it_tests": what_it_tests,
                "expected_conclusion": safe_text(raw.get("expected_conclusion", ""))
                or "expected behavior not yet determined",
                "observed_or_inferred_behavior": safe_text(
                    raw.get("observed_or_inferred_behavior", "")
                )
                or "not yet observed",
                # SAFETY: a freshly proposed example carries no checked evidence.
                "result_for_current_idea": "not_tested",
                "source_or_reason": safe_text(raw.get("source_or_reason", ""))
                or "agent-proposed example",
                "nontriviality": nontriviality,
            }
        )
    return examples


def _normalize_computation_checks(
    raw_checks: object, *, claim_id: str, examples: list[dict]
) -> list[dict]:
    if not examples:
        return []  # nothing to attach a computation to
    by_name = {ex["name"]: ex["id"] for ex in examples}
    default_example_id = examples[0]["id"]
    checks: list[dict] = []
    if not isinstance(raw_checks, list):
        return checks
    for index, raw in enumerate(raw_checks, start=1):
        if not isinstance(raw, dict):
            continue
        computed = safe_text(raw.get("computed_quantity", "")).strip()
        if not computed or computed == "No usable agent text returned.":
            continue
        example_id = by_name.get(safe_text(raw.get("example_name", "")).strip(), default_example_id)
        checks.append(
            {
                "id": f"comp_{index:03d}",
                "example_id": example_id,
                "claim_id": claim_id,
                "computed_quantity": computed,
                "input_data": safe_list(raw.get("input_data")),
                # SAFETY: the agent did not run a verifier; this is a proposal.
                "method": "manual_review_needed",
                "tool_or_source": safe_text(raw.get("tool_or_source", "")) or "agent proposal",
                "result": safe_text(raw.get("result", "")) or "not computed",
                "cross_check": safe_list(raw.get("cross_check")),
                # SAFETY: agent computations are never self-certified as evidence.
                "status": "human_needed",
                "machine_check": None,
                "notes": safe_text(raw.get("notes", "")),
            }
        )
    return checks


@register_stage("examples")
def _examples(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    claim = context["claim"]
    claim_id = claim["id"]
    literature = context.get("literature_review") or {}

    payload = {
        "task": "propose_candidate_examples",
        "claim": {
            "normalized_statement": claim.get("normalized_statement", ""),
            "objects": claim.get("objects", []),
            "conclusion": claim.get("conclusion", ""),
        },
        "hypotheses": claim.get("hypotheses", []),
        "known_examples": literature.get("known_examples", []),
        "required_json_schema": _EXAMPLES_OUTPUT_SCHEMA,
        "rules": [
            "record whether each example satisfies ALL claim hypotheses",
            "do not declare any example checked/survived",
            "computations are proposals that still need verification",
        ],
    }
    raw = client.complete_json(
        system=_EXAMPLES_SYSTEM, prompt=json.dumps(payload, indent=2)
    ) or {}

    examples = _normalize_examples(raw.get("examples"), claim_id=claim_id)
    checks = _normalize_computation_checks(
        raw.get("computation_checks"), claim_id=claim_id, examples=examples
    )

    example_selection = {"claim_id": claim_id, "examples": examples}
    computation_checks = {"claim_id": claim_id, "checks": checks}
    ExampleSelection.model_validate(example_selection)
    ComputationChecks.model_validate(computation_checks)

    write_yaml(run_dir / artifacts["examples"], example_selection)
    write_yaml(run_dir / artifacts["computation_checks"], computation_checks)
    return {
        "unsafe_language": [],
        "notes": [
            f"examples wrote {len(examples)} candidate example(s), "
            f"{len(checks)} unverified computation proposal(s) (status human_needed)"
        ],
    }


# ---------------------------------------------------------------------------
# S4 — 04 Counterexamples + 05 Admissibility (tests)
# ---------------------------------------------------------------------------

_COUNTEREXAMPLE_TARGETS = {"theorem", "proof_step", "hypothesis", "analogy"}

_COUNTEREXAMPLES_SYSTEM = (
    "You are the Espresso Proof Counterexamples agent. Propose CANDIDATE "
    "counterexamples / pressure tests for the locked claim. For each, state "
    "whether it satisfies ALL claim hypotheses and what it targets (theorem / "
    "proof_step / hypothesis / analogy). CRITICAL: a counterexample that drops a "
    "hypothesis does NOT refute the original claim, and an attack on a proof "
    "step does NOT refute the claim. You may NOT declare the claim refuted: "
    "refutation requires an admissible all-hypotheses counterexample decided by "
    "the kernel. Respond with ONLY a JSON object."
)

_COUNTEREXAMPLES_OUTPUT_SCHEMA = {
    "counterexamples": [
        {
            "name": "string",
            "satisfies_all_claim_hypotheses": "true|false|unclear",
            "target": "theorem|proof_step|hypothesis|analogy",
            "missing_hypothesis": "string (if a hypothesis is dropped)",
            "support": "string",
            "explanation": "string",
            "repaired_claim_suggestion": "string",
        }
    ],
    "tests": [
        {
            "description": "string",
            "uses_counterexamples": ["counterexample name"],
            "main_observation": "string",
            "next_action": "string",
        }
    ],
    "summary": "string",
}


def _normalize_counterexamples(raw_items: object, *, examples: list[dict]) -> list[dict]:
    default_example_id = examples[0]["id"] if examples else "cex_seed_example"
    by_name = {ex["name"]: ex["id"] for ex in examples}
    tests: list[dict] = []
    if not isinstance(raw_items, list):
        return tests
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            continue
        name = safe_text(raw.get("name", "")).strip()
        if not name or name == "No usable agent text returned.":
            continue
        satisfies = _coerce_satisfies(raw.get("satisfies_all_claim_hypotheses"))
        target = raw.get("target")
        if target not in _COUNTEREXAMPLE_TARGETS:
            target = "theorem"
        # SAFETY: dropped-hypothesis => exposes a missing hypothesis.
        exposes_missing = satisfies is False or bool(raw.get("missing_hypothesis"))
        missing_hypothesis = safe_text(raw.get("missing_hypothesis", "")).strip() or None
        if exposes_missing and not missing_hypothesis:
            missing_hypothesis = "unspecified dropped hypothesis"
        tests.append(
            {
                "id": f"cex_{index:03d}",
                "operator_id": "agent_counterexample_op",
                "example_id": by_name.get(name, default_example_id),
                "satisfies_all_claim_hypotheses": satisfies,
                "target": target,
                # SAFETY: a proposed counterexample is unverified -> not_tested.
                "result": "not_tested",
                # SAFETY: an agent counterexample can NEVER auto-refute the claim;
                # refutation needs an admissible all-hypotheses counterexample
                # decided by the kernel/admissibility gate.
                "refutes_claim": False,
                "refutes_step_only": target == "proof_step",
                "exposes_missing_hypothesis": exposes_missing,
                "missing_hypothesis": missing_hypothesis,
                "support": safe_text(raw.get("support", "")).strip() or None,
                "explanation": safe_text(raw.get("explanation", ""))
                or "agent-proposed counterexample (candidate)",
                "repaired_claim_suggestion": safe_text(
                    raw.get("repaired_claim_suggestion", "")
                ).strip()
                or None,
            }
        )
    return tests


def _normalize_admissibility_tests(
    raw_tests: object, *, counterexamples: list[dict]
) -> list[dict]:
    cex_ids = {c["id"] for c in counterexamples}
    items: list[dict] = []
    if not isinstance(raw_tests, list):
        return items
    for index, raw in enumerate(raw_tests, start=1):
        if not isinstance(raw, dict):
            continue
        description = safe_text(raw.get("description", "")).strip()
        if not description or description == "No usable agent text returned.":
            continue
        # Only reference counterexamples we actually wrote (known ids).
        uses = [cid for cid in safe_list(raw.get("uses_counterexamples")) if cid in cex_ids]
        items.append(
            {
                "id": f"test_{index:03d}",
                "description": description,
                "uses_examples": [],  # avoid referencing unknown example ids
                "uses_counterexamples": uses,
                # SAFETY: admissibility selection is not a verified result.
                "result": "not_tested",
                "evidence_status": "human_needed",
                "main_observation": safe_text(raw.get("main_observation", ""))
                or "candidate test selected; not yet run",
                "next_action": safe_text(raw.get("next_action", ""))
                or "run the test under the admissibility gate",
            }
        )
    return items


@register_stage("counterexamples")
def _counterexamples(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    claim = context["claim"]
    claim_id = claim["id"]
    examples = (context.get("examples") or {}).get("examples", [])

    payload = {
        "task": "propose_candidate_counterexamples",
        "claim": {
            "normalized_statement": claim.get("normalized_statement", ""),
            "objects": claim.get("objects", []),
            "conclusion": claim.get("conclusion", ""),
        },
        "hypotheses": claim.get("hypotheses", []),
        "required_json_schema": _COUNTEREXAMPLES_OUTPUT_SCHEMA,
        "rules": [
            "dropping a hypothesis does NOT refute the original claim",
            "a proof-step attack is NOT a claim refutation",
            "do not declare the claim refuted; refutation needs an admissible "
            "all-hypotheses counterexample decided by the kernel",
        ],
    }
    raw = client.complete_json(
        system=_COUNTEREXAMPLES_SYSTEM, prompt=json.dumps(payload, indent=2)
    ) or {}

    counterexamples = _normalize_counterexamples(
        raw.get("counterexamples"), examples=examples
    )
    test_items = _normalize_admissibility_tests(
        raw.get("tests"), counterexamples=counterexamples
    )

    counterexample_report = {"claim_id": claim_id, "tests": counterexamples}
    test_report = {
        "claim_id": claim_id,
        "target": "claim",
        "tests": test_items,
        "summary": safe_text(raw.get("summary", ""))
        or "Candidate counterexamples proposed; none verified as a refutation.",
    }
    CounterexampleReport.model_validate(counterexample_report)
    TestReport.model_validate(test_report)

    write_yaml(run_dir / artifacts["counterexamples"], counterexample_report)
    write_yaml(run_dir / artifacts["tests"], test_report)
    return {
        "unsafe_language": [],
        "notes": [
            f"counterexamples wrote {len(counterexamples)} candidate(s) "
            f"(refutes_claim forced False), {len(test_items)} admissibility test(s)"
        ],
    }


# ---------------------------------------------------------------------------
# S5 — 09 Strategy Generator + 11 Grader + 12 Brief
# ---------------------------------------------------------------------------

_IDEA_STATUSES = {"promising", "fragile", "likely_false", "refuted", "unclear"}
_EXPLORATION_MODES = {"quick_test", "normal_pass", "deep_exploration", "long_watch"}

_STRATEGY_SYSTEM = (
    "You are the Espresso Proof Strategy agent (generator + grader + brief). "
    "Propose CANDIDATE proof routes for the locked claim and grade them "
    "CONSERVATIVELY. Scores are candidate confidence, NOT proof status: a high "
    "score never means the claim is proved. Source gaps must be named. If the "
    "claim is still unclear, the only honest route is to lock the claim first. "
    "Respond with ONLY a JSON object."
)

_STRATEGY_OUTPUT_SCHEMA = {
    "routes": [
        {
            "title": "string",
            "why_this_route": "string",
            "main_risk": "string",
            "estimated_effort": "quick_test|normal_pass|deep_exploration|long_watch",
            "next_loop_plan": "string",
        }
    ],
    "question_for_mathematician": "string",
    "synthesis_note": "string",
}


def _safe_lock_route(idea_grade: dict) -> dict:
    """A route that is valid under the strategy rules for ANY idea grade.

    Score is capped well below the high-score threshold and never lifts above the
    idea grade, the effort is cheap, and the id is the canonical claim-lock route
    (the only route an unclear claim may recommend).
    """

    idea_score = int(idea_grade.get("score", 0) or 0)
    idea_status = idea_grade.get("status", "unclear")
    if idea_status not in _IDEA_STATUSES:
        idea_status = "unclear"
    score = max(0, min(idea_score, 30))
    return {
        "id": "strategy_lock_claim",
        "title": "Lock and clarify the claim before deeper strategy work",
        "score": score,
        "status": idea_status,
        "why_this_route": "The claim must be precise before strategies can be graded.",
        "survived_tests": [],
        "failed_tests": [],
        "main_risk": "Proceeding without a locked claim creates false progress.",
        "source_status": "candidate",
        "estimated_effort": "quick_test",
        "next_loop_plan": "Lock the precise claim, then re-run strategy generation.",
    }


def _normalize_strategy_routes(raw_routes: object, *, idea_grade: dict) -> list[dict]:
    idea_score = int(idea_grade.get("score", 0) or 0)
    idea_status = idea_grade.get("status", "unclear")
    # Cap scores below the high-score threshold and within the standard lift, so
    # the unchecked-evidence / high-score / idea-status rules can never trip.
    score_cap = min(HIGH_SCORE_THRESHOLD - 1, idea_score + STANDARD_SCORE_LIFT_LIMIT)
    routes: list[dict] = []
    if not isinstance(raw_routes, list):
        return routes
    for index, raw in enumerate(raw_routes, start=1):
        if not isinstance(raw, dict):
            continue
        title = safe_text(raw.get("title", "")).strip()
        if not title or title == "No usable agent text returned.":
            continue
        effort = raw.get("estimated_effort")
        # deep_exploration would trigger unchecked-evidence checks; keep it modest.
        if effort not in _EXPLORATION_MODES or effort in {"deep_exploration", "long_watch"}:
            effort = "normal_pass"
        routes.append(
            {
                "id": f"strategy_route_{index:03d}",
                "title": title,
                "score": max(0, min(score_cap, 50)),
                # SAFETY: fragile is conservative; never promising from the front half.
                "status": "fragile",
                "why_this_route": safe_text(raw.get("why_this_route", "")) or "candidate route",
                "survived_tests": [],
                "failed_tests": [],
                "main_risk": safe_text(raw.get("main_risk", "")) or "route may depend on unproven steps",
                # SAFETY: front-half has done no source grounding -> candidate only.
                "source_status": "candidate",
                "estimated_effort": effort,
                "next_loop_plan": safe_text(raw.get("next_loop_plan", "")) or "pressure-test the route",
            }
        )
        if len(routes) >= 3:
            break
    return routes


@register_stage("strategy")
def _strategy(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    run_id = context["manifest"]["run_id"]
    claim = context["claim"]
    claim_id = claim["id"]
    idea_grade = context.get("idea_grade") or {"status": "unclear", "score": 0}
    idea_status = idea_grade.get("status", "unclear")

    payload = {
        "task": "generate_and_grade_strategies",
        "claim": {
            "normalized_statement": claim.get("normalized_statement", ""),
            "conclusion": claim.get("conclusion", ""),
        },
        "idea_status": idea_status,
        "required_json_schema": _STRATEGY_OUTPUT_SCHEMA,
        "rules": [
            "scores are candidate confidence, never proof status",
            "name source gaps; do not claim source_backed",
            "an unclear claim can only recommend locking the claim first",
        ],
    }
    raw = client.complete_json(
        system=_STRATEGY_SYSTEM, prompt=json.dumps(payload, indent=2)
    ) or {}

    # An unclear claim may ONLY recommend the claim-lock route (kernel rule).
    if idea_status == "unclear":
        routes = [_safe_lock_route(idea_grade)]
    else:
        routes = _normalize_strategy_routes(raw.get("routes"), idea_grade=idea_grade)
        # Pre-check against the kernel strategy rules; fall back if anything trips.
        if not routes or collect_strategy_rule_violations(
            routes,
            idea_grade,
            review=context.get("review"),
            computations=context.get("computation_checks"),
            counterexamples=context.get("counterexamples"),
            tests=context.get("tests"),
        ):
            routes = [_safe_lock_route(idea_grade)]

    allowed_choices = [
        {"strategy_id": r["id"], "effort_options": [r["estimated_effort"]]} for r in routes
    ]
    allowed_choices.append({"strategy_id": "none", "effort_options": ["quick_test"]})

    synthesis = {
        "claim_id": claim_id,
        "synthesized_from": [
            run_id,
            "literature_review.yaml",
            "tests.yaml",
            "counterexamples.yaml",
            "source_grounding.yaml",
            "review.yaml",
            "idea_grade.yaml",
            "loop_control.yaml",
        ],
        "routes": routes,
        "discarded_routes": [],
        "synthesis_note": safe_text(raw.get("synthesis_note", ""))
        or "Candidate routes proposed and graded conservatively; scores are not proof status.",
    }
    brief = {
        "claim_id": claim_id,
        "generated_after_runs": [run_id],
        "top_strategies": routes,
        "also_considered": [],
        "question_for_mathematician": safe_text(raw.get("question_for_mathematician", ""))
        or "Which route should receive time, if any?",
        "allowed_human_choices": allowed_choices,
    }
    StrategySynthesis.model_validate(synthesis)
    StrategyBrief.model_validate(brief)

    write_yaml(run_dir / artifacts["strategy_synthesis"], synthesis)
    write_yaml(run_dir / artifacts["strategy_brief"], brief)
    return {
        "unsafe_language": [],
        "notes": [
            f"strategy wrote {len(routes)} candidate route(s) "
            f"(idea_status={idea_status}; scores are candidate confidence, not proof status)"
        ],
    }


# ---------------------------------------------------------------------------
# S6 — 10 Example/Counterexample Attacker
# ---------------------------------------------------------------------------

_ATTACK_TARGETS = {"claim", "proof_step", "hypothesis", "analogy"}

_ATTACKER_SYSTEM = (
    "You are the Espresso Proof Example/Counterexample Attacker. Adversarially "
    "pressure the proposed examples and counterexamples and record which survive. "
    "CRITICAL: a proof-step attack does NOT refute the theorem; a dropped-"
    "hypothesis example does NOT refute the claim; and you may NOT mark any test "
    "as checked/survived/failed evidence — only verified computations/sources can "
    "do that. Classify each attack's target (claim / proof_step / hypothesis / "
    "analogy). Respond with ONLY a JSON object."
)

_ATTACKER_OUTPUT_SCHEMA = {
    "attacks": [
        {
            "target_id": "string (an existing example or counterexample id)",
            "attacks": "claim|proof_step|hypothesis|analogy",
            "survives": "true|false",
            "observation": "string",
            "next_action": "string",
        }
    ],
    "route_blockers": ["string"],
    "summary": "string",
}


def _normalize_attacks(
    raw_attacks: object, *, example_ids: set[str], counterexample_ids: set[str]
) -> list[dict]:
    items: list[dict] = []
    if not isinstance(raw_attacks, list):
        return items
    for index, raw in enumerate(raw_attacks, start=1):
        if not isinstance(raw, dict):
            continue
        observation = safe_text(raw.get("observation", "")).strip()
        if not observation or observation == "No usable agent text returned.":
            continue
        target_id = safe_text(raw.get("target_id", "")).strip()
        attack_target = raw.get("attacks")
        if attack_target not in _ATTACK_TARGETS:
            attack_target = "claim"
        uses_examples = [target_id] if target_id in example_ids else []
        uses_counterexamples = [target_id] if target_id in counterexample_ids else []
        survives = raw.get("survives")
        survives_text = "survives" if survives in (True, "true", "yes") else "unresolved"
        items.append(
            {
                "id": f"test_atk_{index:03d}",
                "description": f"Adversarial pressure on {target_id or 'a proposed item'}",
                "uses_examples": uses_examples,
                "uses_counterexamples": uses_counterexamples,
                # SAFETY: an attack observation is never checked evidence.
                "result": "not_tested",
                "evidence_status": "human_needed",
                "main_observation": (
                    f"{observation} [attack target: {attack_target}; status: {survives_text}; "
                    "a proof-step or dropped-hypothesis attack is not a refutation]"
                ),
                "next_action": safe_text(raw.get("next_action", ""))
                or "verify under the admissibility/evidence gate before drawing conclusions",
            }
        )
    return items


@register_stage("attacker")
def _attacker(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    claim = context["claim"]
    claim_id = claim["id"]
    existing = context.get("tests") or {}
    counterexamples = (context.get("counterexamples") or {}).get("tests", [])
    examples = (context.get("examples") or {}).get("examples", [])
    cex_ids = {c["id"] for c in counterexamples}
    ex_ids = {e["id"] for e in examples}

    payload = {
        "task": "attack_examples_and_counterexamples",
        "claim": {"normalized_statement": claim.get("normalized_statement", "")},
        "counterexamples": [
            {"id": c["id"], "explanation": c.get("explanation", "")} for c in counterexamples
        ],
        "examples": [{"id": e["id"], "name": e.get("name", "")} for e in examples],
        "required_json_schema": _ATTACKER_OUTPUT_SCHEMA,
        "rules": [
            "a proof-step attack is not a refutation",
            "a dropped-hypothesis example is not a refutation",
            "do not mark any test checked/survived/failed",
        ],
    }
    raw = client.complete_json(
        system=_ATTACKER_SYSTEM, prompt=json.dumps(payload, indent=2)
    ) or {}

    attack_items = _normalize_attacks(
        raw.get("attacks"), example_ids=ex_ids, counterexample_ids=cex_ids
    )
    # Preserve existing test ids; append attacker observations.
    combined = list(existing.get("tests", [])) + attack_items
    blockers = safe_list(raw.get("route_blockers"))
    summary = safe_text(raw.get("summary", "")) or existing.get(
        "summary", "Adversarial pressure recorded; nothing verified as a refutation."
    )
    if blockers:
        summary = f"{summary} Route blockers: {'; '.join(blockers)}"

    test_report = {
        "claim_id": claim_id,
        "target": existing.get("target", "claim"),
        "tests": combined,
        "summary": safe_text(summary),
    }
    TestReport.model_validate(test_report)
    write_yaml(run_dir / artifacts["tests"], test_report)
    return {
        "unsafe_language": [],
        "notes": [
            f"attacker appended {len(attack_items)} adversarial observation(s) "
            f"(all not_tested/human_needed; attacks never refute the claim)"
        ],
    }


# ---------------------------------------------------------------------------
# S7 — 06 Source Matching + 07 Theorem Applicability
# ---------------------------------------------------------------------------

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
