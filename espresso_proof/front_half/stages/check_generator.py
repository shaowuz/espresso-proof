"""Front-half stage: LLM-generated Rail-A machine-check specs.

The agent may propose machine-check specifications, but this stage never earns
evidence. Survivors are appended as ``human_needed`` candidates and the trusted
Evidence Auditor is the only component allowed to set ``checked`` or ``wrong``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ...io import read_yaml, write_yaml
from ...llm_agents import LLMClient, safe_text
from ...machine_check_guards import (
    RAIL_A_KINDS,
    dedupe_key,
    is_executable_machine_check,
    is_non_vacuous_machine_check,
    source_step_ids,
)
from ...models import ComputationChecks
from ..registry import register_stage


_CHECK_GENERATOR_SYSTEM = (
    "You are the Espresso Proof Machine-Check Generator. Propose Rail-A "
    "machine_check specs only when a listed proof step has a soundly checkable "
    "finite or computational claim. You may not set evidence status, proof "
    "status, source status, or grade. Respond with ONLY a JSON object."
)

_CHECK_GENERATOR_OUTPUT_SCHEMA = {
    "checks": [
        {
            "kind": (
                "exact_arithmetic|permutation_order|matrix_rank|matrix_determinant|"
                "polynomial_identity|finite_enumeration"
            ),
            "expression": "string",
            "expected": "string",
            "target_step_id": "one proof_step_id from source_grounding.steps",
            "notes": "string",
        }
    ]
}

_BACKEND_CATALOG = [
    {
        "kind": "exact_arithmetic",
        "format": {"expression": "integer/rational arithmetic", "expected": "integer/rational arithmetic"},
        "example": {"expression": "2**5", "expected": "32"},
    },
    {
        "kind": "permutation_order",
        "format": {"expression": "cycle notation such as (1 2 3)(4 5)", "expected": "integer"},
        "example": {"expression": "(1 2 3)(4 5)", "expected": "6"},
    },
    {
        "kind": "matrix_rank",
        "format": {"expression": "matrix literal over integers/rationals", "expected": "integer"},
        "example": {"expression": "[[1, 2], [2, 4]]", "expected": "1"},
    },
    {
        "kind": "matrix_determinant",
        "format": {"expression": "square matrix literal over integers/rationals", "expected": "integer/rational"},
        "example": {"expression": "[[1, 2], [3, 4]]", "expected": "-2"},
    },
    {
        "kind": "polynomial_identity",
        "format": {"expression": "polynomial over Q", "expected": "polynomial over Q"},
        "example": {"expression": "(x+1)**2", "expected": "x**2 + 2*x + 1"},
    },
    {
        "kind": "finite_enumeration",
        "format": {"expression": "@n[a:b] body", "expected": "@n[a:b] body"},
        "example": {"expression": "@n[1:20] sum(1,n)", "expected": "@n[1:20] n*(n+1)/2"},
    },
]

_SENTINEL = "No usable agent text returned."
_STATUS_FIELDS = {
    "status",
    "earned_status",
    "method",
    "proof_language_status",
    "human_confirmed",
    "source_status",
    "idea_status",
}
_MAX_PER_RUN = 8
_MAX_PER_STEP = 1


@register_stage("check_generator")
def _check_generator(*, run_dir: Path, client: LLMClient, context: dict) -> dict:
    artifacts = context["manifest"]["artifacts"]
    claim = context["claim"]
    claim_id = claim["id"]
    source_grounding = context.get("source_grounding") or {}
    step_ids = source_step_ids(source_grounding)
    if not step_ids:
        return {"unsafe_language": [], "notes": ["check_generator skipped: no source steps to bind"]}

    examples = context.get("examples") or {}
    example_ids = [
        str(example.get("id"))
        for example in examples.get("examples", [])
        if isinstance(example, dict) and str(example.get("id", "")).strip()
    ]
    if not example_ids:
        return {"unsafe_language": [], "notes": ["check_generator skipped: no example anchor"]}
    default_example_id = example_ids[0]

    payload = {
        "task": "generate_candidate_rail_a_machine_checks",
        "claim": {
            "id": claim_id,
            "normalized_statement": claim.get("normalized_statement", ""),
            "objects": claim.get("objects", []),
            "hypotheses": claim.get("hypotheses", []),
            "conclusion": claim.get("conclusion", ""),
        },
        "source_steps": [
            {
                "proof_step_id": step.get("proof_step_id", ""),
                "needed_theorem": step.get("needed_theorem", ""),
                "note": step.get("note", ""),
            }
            for step in source_grounding.get("steps", [])
            if isinstance(step, dict)
        ],
        "backend_catalog": _BACKEND_CATALOG,
        "required_json_schema": _CHECK_GENERATOR_OUTPUT_SCHEMA,
        "rules": [
            "emit {\"checks\": []} when no step is soundly machine-checkable",
            "each target_step_id must be one listed proof_step_id",
            "do not emit status/method/evidence/proof/grade fields",
            "do not emit tautologies, identical sides, or finite_enumeration bodies without the bound variable",
            "finite_enumeration and instance checks are computational evidence only, never a proof",
        ],
    }
    raw = client.complete_json(
        system=_CHECK_GENERATOR_SYSTEM,
        prompt=json.dumps(payload, indent=2),
    ) or {}

    comp_path = run_dir / artifacts["computation_checks"]
    existing = read_yaml(comp_path) or {"claim_id": claim_id, "checks": []}
    existing_checks = [
        check for check in existing.get("checks", []) if isinstance(check, dict)
    ]
    generated, dropped = _normalize_generated_checks(
        raw.get("checks"),
        claim_id=claim_id,
        default_example_id=default_example_id,
        step_ids=step_ids,
        existing_checks=existing_checks,
    )
    if not generated:
        return {
            "unsafe_language": [],
            "notes": [f"check_generator appended 0 candidate machine check(s); dropped {dropped}"],
        }

    updated = {"claim_id": claim_id, "checks": existing_checks + generated}
    ComputationChecks.model_validate(updated)
    write_yaml(comp_path, updated)
    return {
        "unsafe_language": [],
        "notes": [
            f"check_generator appended {len(generated)} candidate machine check(s) "
            f"(status human_needed); dropped {dropped}"
        ],
    }


def _normalize_generated_checks(
    raw_checks: object,
    *,
    claim_id: str,
    default_example_id: str,
    step_ids: set[str],
    existing_checks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    generated: list[dict[str, Any]] = []
    dropped = 0
    if not isinstance(raw_checks, list):
        return generated, 0

    seen = {
        dedupe_key(check.get("machine_check") or {})
        for check in existing_checks
        if isinstance(check.get("machine_check"), dict)
    }
    per_step: dict[str, int] = {}
    for check in existing_checks:
        if not str(check.get("id", "")).startswith("comp_gen_"):
            continue
        spec = check.get("machine_check") or {}
        target = str(spec.get("target_step_id") or "")
        if target:
            per_step[target] = per_step.get(target, 0) + 1

    next_index = _next_generated_index(existing_checks)
    for raw in raw_checks:
        if len(generated) >= _MAX_PER_RUN:
            dropped += 1
            continue
        spec = _normalize_spec(raw, claim_id=claim_id, step_ids=step_ids)
        if spec is None:
            dropped += 1
            continue
        target = str(spec["target_step_id"])
        if per_step.get(target, 0) >= _MAX_PER_STEP:
            dropped += 1
            continue
        key = dedupe_key(spec)
        if key in seen:
            dropped += 1
            continue
        generated.append(
            {
                "id": f"comp_gen_{next_index:03d}",
                "example_id": default_example_id,
                "claim_id": claim_id,
                "computed_quantity": f"machine-check proposal for {target}",
                "input_data": [f"target_step_id={target}"],
                "method": "manual_review_needed",
                "tool_or_source": "agent-proposed Rail-A machine_check",
                "result": spec["expected"],
                "cross_check": [],
                "status": "human_needed",
                "machine_check": spec,
                "notes": "candidate generated by check_generator; awaiting audit_evidence",
            }
        )
        next_index += 1
        per_step[target] = per_step.get(target, 0) + 1
        seen.add(key)

    return generated, dropped


def _normalize_spec(
    raw: object,
    *,
    claim_id: str,
    step_ids: set[str],
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if _STATUS_FIELDS & set(raw):
        return None
    kind = raw.get("kind")
    if kind not in RAIL_A_KINDS:
        return None
    expression = safe_text(raw.get("expression", "")).strip()
    expected = safe_text(raw.get("expected", "")).strip()
    target_step_id = safe_text(raw.get("target_step_id", "")).strip()
    if not expression or not expected or _SENTINEL in {expression, expected}:
        return None
    if target_step_id not in step_ids:
        return None
    spec = {
        "kind": kind,
        "expression": expression,
        "expected": expected,
        "target_step_id": target_step_id,
        "notes": safe_text(raw.get("notes", "")),
    }
    if not is_non_vacuous_machine_check(spec):
        return None
    if not is_executable_machine_check(spec, claim_id=claim_id):
        return None
    return spec


def _next_generated_index(existing_checks: list[dict[str, Any]]) -> int:
    high = 0
    for check in existing_checks:
        match = re.fullmatch(r"comp_gen_(\d+)", str(check.get("id", "")))
        if match:
            high = max(high, int(match.group(1)))
    return high + 1
