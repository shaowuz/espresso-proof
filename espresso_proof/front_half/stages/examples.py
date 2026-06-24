"""Front-half stage: examples (Phase 04 physical split)."""

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
from espresso_proof.kernel.research_bank import load_bankable_dict
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
            "machine_check": {
                "kind": "exact_arithmetic|permutation_order|finite_enumeration",
                "expression": "string",
                "expected": "string",
                "notes": "string",
            },
            "notes": "string",
        }
    ],
}


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


_MACHINE_CHECK_KINDS = {"exact_arithmetic", "permutation_order", "finite_enumeration"}


def _normalize_machine_check(raw: object) -> dict | None:
    """Coerce an agent-supplied machine_check spec, or None.

    The spec is STORED so the Evidence Auditor can run it later; the agent NEVER
    self-certifies — the computation status stays human_needed until a trusted
    machine result confirms it.
    """

    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if kind not in _MACHINE_CHECK_KINDS:
        return None
    expression = safe_text(raw.get("expression", "")).strip()
    expected = safe_text(raw.get("expected", "")).strip()
    sentinel = "No usable agent text returned."
    if not expression or not expected or sentinel in (expression, expected):
        return None
    return {
        "kind": kind,
        "expression": expression,
        "expected": expected,
        "notes": safe_text(raw.get("notes", "")),
    }


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
                # SAFETY: agent computations are never self-certified as evidence;
                # an attached machine_check is only a PROPOSAL to be machine-run.
                "status": "human_needed",
                "machine_check": _normalize_machine_check(raw.get("machine_check")),
                "notes": safe_text(raw.get("notes", "")),
            }
        )
    return checks


def compute_examples(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    """Pure compute for the examples lane (subgoal 04 compute/commit split).

    Builds and validates the candidate artifacts in memory and writes NOTHING
    under ``run_dir``. Returns ``{"artifacts": {...}, "note": str}``. The
    ``tests.yaml`` reconciliation is deliberately NOT done here: it is a live
    read-modify-write that must happen at commit time, AFTER counterexamples has
    committed (see ``persist_examples``).
    """
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

    note = (
        f"examples wrote {len(examples)} candidate example(s), "
        f"{len(checks)} unverified computation proposal(s) (status human_needed)"
    )
    return {
        "artifacts": {
            "examples": example_selection,
            "computation_checks": computation_checks,
        },
        "note": note,
    }


def persist_examples(
    run_dir: Path, artifacts: dict, candidate_artifacts: dict, claim_id: str
) -> int:
    """Write the examples lane's precomputed artifacts, then reconcile tests.yaml
    against the LIVE on-disk state. Returns the count of dropped stale tests.

    SAFETY/CONSISTENCY: replacing examples.yaml can orphan seeded tests.yaml
    entries whose evidence references point at scaffold ids. Drop the stale test
    entry rather than inventing/rewriting evidence links. This is a soft
    normalization before validate_run; kernel reference checks remain strict.
    """
    write_yaml(run_dir / artifacts["examples"], candidate_artifacts["examples"])
    write_yaml(
        run_dir / artifacts["computation_checks"],
        candidate_artifacts["computation_checks"],
    )
    example_ids = {ex["id"] for ex in candidate_artifacts["examples"]["examples"]}
    dropped_tests = _reconcile_tests_with_examples(run_dir, artifacts, claim_id, example_ids)
    dropped_counterexamples = _reconcile_counterexamples_with_examples(
        run_dir, artifacts, claim_id, example_ids
    )
    dropped_grade_refs = _reconcile_grade_with_counterexamples(run_dir, artifacts)
    return dropped_tests + dropped_counterexamples + dropped_grade_refs


@register_stage("examples")
def _examples(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    claim_id = context["claim"]["id"]
    result = compute_examples(run_dir=run_dir, client=client, context=context)
    dropped = persist_examples(run_dir, artifacts, result["artifacts"], claim_id)
    note = result["note"]
    if dropped:
        note += f"; dropped {dropped} stale test reference(s)"
    return {
        "unsafe_language": [],
        "notes": [note],
    }


def _reconcile_tests_with_examples(
    run_dir: Path,
    artifacts: dict,
    claim_id: str,
    example_ids: set[str],
    counterexample_ids: set[str] | None = None,
) -> int:
    """Drop tests whose example/counterexample references no longer resolve."""
    tests_path = run_dir / artifacts["tests"]
    if not tests_path.exists():
        return 0

    if counterexample_ids is None:
        counterexample_ids = set()
        counterexamples_path = run_dir / artifacts["counterexamples"]
        if counterexamples_path.exists():
            counterexamples = load_bankable_dict(
                run_dir, "counterexamples", read_yaml(run_dir / "run_manifest.yaml")
            )
            counterexample_ids = {
                test.get("id")
                for test in counterexamples.get("tests", [])
                if isinstance(test, dict) and test.get("id")
            }

    test_report = read_yaml(tests_path) or {}
    original = test_report.get("tests", [])
    kept = []
    for test in original:
        if not isinstance(test, dict):
            continue
        if set(test.get("uses_examples", [])) - example_ids:
            continue
        if set(test.get("uses_counterexamples", [])) - counterexample_ids:
            continue
        kept.append(test)
    if len(kept) == len(original):
        return 0
    test_report["claim_id"] = test_report.get("claim_id") or claim_id
    test_report["tests"] = kept
    TestReport.model_validate(test_report)
    write_yaml(tests_path, test_report)
    return len(original) - len(kept)


def _reconcile_counterexamples_with_examples(
    run_dir: Path,
    artifacts: dict,
    claim_id: str,
    example_ids: set[str],
) -> int:
    """Drop counterexample tests whose example reference no longer exists."""

    counterexamples_path = run_dir / artifacts["counterexamples"]
    if not counterexamples_path.exists():
        return 0

    counterexamples = read_yaml(counterexamples_path) or {}
    original = counterexamples.get("tests", [])
    kept = []
    for test in original:
        if not isinstance(test, dict):
            continue
        example_id = test.get("example_id")
        if example_id and example_id not in example_ids:
            continue
        kept.append(test)
    if len(kept) == len(original):
        return 0
    counterexamples["claim_id"] = counterexamples.get("claim_id") or claim_id
    counterexamples["tests"] = kept
    CounterexampleReport.model_validate(counterexamples)
    write_yaml(counterexamples_path, counterexamples)
    return len(original) - len(kept)


def _reconcile_grade_with_counterexamples(run_dir: Path, artifacts: dict) -> int:
    """Drop grade evidence references to counterexample tests that no longer exist."""

    grade_path = run_dir / artifacts["idea_grade"]
    counterexamples_path = run_dir / artifacts["counterexamples"]
    if not grade_path.exists() or not counterexamples_path.exists():
        return 0

    grade = read_yaml(grade_path) or {}
    counterexamples = read_yaml(counterexamples_path) or {}
    known_tests = {
        test.get("id")
        for test in counterexamples.get("tests", [])
        if isinstance(test, dict) and test.get("id")
    }
    changed = 0
    for field in ("survived_tests", "failed_tests"):
        original = list(grade.get(field) or [])
        kept = [test_id for test_id in original if test_id in known_tests]
        if kept != original:
            grade[field] = kept
            changed += len(original) - len(kept)
    if not changed:
        return 0
    IdeaGrade.model_validate(grade)
    write_yaml(grade_path, grade)
    return changed


# ---------------------------------------------------------------------------
# S4 — 04 Counterexamples + 05 Admissibility (tests)
# ---------------------------------------------------------------------------
