"""Front-half stage: examples (Phase 04 physical split)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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


_MACHINE_CHECK_KINDS = {"exact_arithmetic", "permutation_order"}


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

    # SAFETY/CONSISTENCY: replacing examples.yaml can orphan a seeded tests.yaml
    # whose uses_examples points at scaffold example ids (e.g. the hodge_atoms
    # mock seeds ex_special_cubic). The post-stage validate_run would then fail
    # before the counterexamples stage rewrites tests.yaml. Prune uses_examples
    # to the example ids we actually wrote (conservative: drops an unsupported
    # test reference, never invents one, never upgrades any status).
    example_ids = {ex["id"] for ex in examples}
    tests_path = run_dir / artifacts["tests"]
    if tests_path.exists():
        test_report = read_yaml(tests_path)
        changed = False
        for test in test_report.get("tests", []):
            kept = [eid for eid in test.get("uses_examples", []) if eid in example_ids]
            if kept != test.get("uses_examples", []):
                test["uses_examples"] = kept
                changed = True
        if changed:
            TestReport.model_validate(test_report)
            write_yaml(tests_path, test_report)
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

