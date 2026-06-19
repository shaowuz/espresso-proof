"""Front-half stage: counterexamples (Phase 04 physical split)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.verification.counterexample_probes import propose_deterministic_counterexamples
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


def compute_counterexamples(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    """Pure compute for the counterexamples lane (subgoal 04 compute/commit split).

    Builds and validates the candidate artifacts in memory (including the
    deterministic machine probes, which read only the claim dict) and writes
    NOTHING under ``run_dir``. Returns ``{"artifacts": {...}, "note": str}``.
    """
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

    # Goal 42E cycle 1: deterministic machine probes. Agent TEXT stays
    # candidate-only (refutes_claim forced False above); a Cayley-table
    # verified non-abelian group of the claimed order is a MACHINE result and
    # may carry result=failed — with refutes_claim=True only when the probe's
    # anchored statement match AND the locked hypothesis list make
    # admissibility machine-derivable. The kernel's gates check the record's
    # flag coherence; the mathematical trust base is the probe's fail-closed
    # matcher plus verify_group_abelian (see counterexample_probes docstring).
    existing_ids = {t.get("id") for t in counterexamples}
    probe_tests = [
        t
        for t in propose_deterministic_counterexamples(claim)
        if t["id"] not in existing_ids
    ]
    counterexamples.extend(probe_tests)

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

    note = (
        f"counterexamples wrote {len(counterexamples) - len(probe_tests)} agent "
        f"candidate(s) (refutes_claim forced False), {len(probe_tests)} "
        f"deterministic machine probe(s), {len(test_items)} admissibility test(s)"
    )
    return {
        "artifacts": {
            "counterexamples": counterexample_report,
            "tests": test_report,
        },
        "note": note,
    }


def persist_counterexamples(
    run_dir: Path, artifacts: dict, candidate_artifacts: dict
) -> None:
    """Write the counterexamples lane's precomputed artifacts (blind full write;
    counterexamples is the producer of tests.yaml, examples only reconciles it)."""
    write_yaml(run_dir / artifacts["counterexamples"], candidate_artifacts["counterexamples"])
    write_yaml(run_dir / artifacts["tests"], candidate_artifacts["tests"])


@register_stage("counterexamples")
def _counterexamples(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    result = compute_counterexamples(run_dir=run_dir, client=client, context=context)
    persist_counterexamples(run_dir, artifacts, result["artifacts"])
    return {
        "unsafe_language": [],
        "notes": [result["note"]],
    }


# ---------------------------------------------------------------------------
# S5 — 09 Strategy Generator + 11 Grader + 12 Brief
# ---------------------------------------------------------------------------

