"""Shared guards for agent-authored machine checks.

These helpers are deliberately deterministic and conservative. They do not earn
status; they only decide whether an agent proposal is structurally usable and
whether a confirmed check is allowed to count toward eval grounding.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from espresso_proof.verification.verifier import _eval_arith, _parse_enum, verify_check

RAIL_A_KINDS = {
    "exact_arithmetic",
    "permutation_order",
    "matrix_rank",
    "matrix_determinant",
    "polynomial_identity",
    "finite_enumeration",
}


def source_step_ids(source_grounding: dict[str, Any]) -> set[str]:
    """Stable ids a machine check may bind to."""
    ids: set[str] = set()
    for step in source_grounding.get("steps", []):
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("proof_step_id") or step.get("id") or "").strip()
        if step_id:
            ids.add(step_id)
    return ids


def normalized_expression(expression: str) -> str:
    return re.sub(r"\s+", "", str(expression or ""))


def dedupe_key(spec: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(spec.get("target_step_id") or ""),
        str(spec.get("kind") or ""),
        normalized_expression(str(spec.get("expression") or "")),
    )


def is_executable_machine_check(spec: dict[str, Any], *, claim_id: str = "claim") -> bool:
    """Return True iff the existing trusted backend can execute the spec.

    Both confirmed and refuted verdicts are usable; error/unsupported are not.
    The temporary check is never persisted and never earns status.
    """
    kind = spec.get("kind")
    if kind not in RAIL_A_KINDS:
        return False
    check = {
        "id": "candidate_machine_check",
        "example_id": "candidate_example",
        "claim_id": claim_id,
        "computed_quantity": "candidate machine check",
        "input_data": [],
        "method": "manual_review_needed",
        "tool_or_source": "agent proposal",
        "result": str(spec.get("expected") or ""),
        "cross_check": [],
        "status": "human_needed",
        "machine_check": {
            "kind": kind,
            "expression": str(spec.get("expression") or ""),
            "expected": str(spec.get("expected") or ""),
            "target_step_id": spec.get("target_step_id"),
            "notes": str(spec.get("notes") or ""),
        },
        "notes": "",
    }
    result = verify_check(check)
    return bool(result and result.get("executed") and result.get("verdict") in {"confirmed", "refuted"})


def is_non_vacuous_machine_check(spec: dict[str, Any]) -> bool:
    """Reject tautological or unbound checks before they can farm grounding."""
    kind = spec.get("kind")
    expression = str(spec.get("expression") or "").strip()
    expected = str(spec.get("expected") or "").strip()
    if not expression or not expected:
        return False
    if normalized_expression(expression) == normalized_expression(expected):
        return False

    if kind == "finite_enumeration":
        return _finite_enumeration_non_vacuous(expression, expected)
    if kind == "polynomial_identity":
        if not (_ast_names(expression) or _ast_names(expected)):
            return not _same_arithmetic_value(expression, expected)
        return True
    if kind == "exact_arithmetic":
        # A confirmed exact-arithmetic equality with no symbolic input is
        # structurally constant. It can still be audited, but it cannot be used
        # to farm source-grounding credit for the generator.
        if not (_ast_names(expression) or _ast_names(expected)):
            return not _same_arithmetic_value(expression, expected)
    return True


def _finite_enumeration_non_vacuous(expression: str, expected: str) -> bool:
    try:
        var_l, lo_l, hi_l, body_l = _parse_enum(expression)
        var_r, lo_r, hi_r, body_r = _parse_enum(expected)
    except Exception:  # noqa: BLE001 - parse failure is not usable/non-vacuous
        return False
    if (var_l, lo_l, hi_l) != (var_r, lo_r, hi_r):
        return False
    if normalized_expression(body_l) == normalized_expression(body_r):
        return False
    names = _ast_names(body_l) | _ast_names(body_r)
    return var_l in names


def _ast_names(expression: str) -> set[str]:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return set()
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _same_arithmetic_value(left: str, right: str) -> bool:
    try:
        return _eval_arith(left) == _eval_arith(right)
    except Exception:  # noqa: BLE001 - if parsing fails, executable guard handles it
        return False
