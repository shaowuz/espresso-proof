"""Real verifier backends for Espresso computation checks.

This is the first module where ``computation_checked`` is backed by an actually
executed computation rather than an asserted result. It is intentionally a
small, zero-dependency, auditable backend:

- ``exact_arithmetic``: evaluate an exact rational expression with a safe AST
  walker over :class:`fractions.Fraction` (no ``eval``; only +, -, *, /, **).
- ``permutation_order``: compute the order of a permutation given in cycle
  notation as the lcm of its cycle lengths.

The verifier never upgrades status. It produces a verification report recording,
per check, whether the machine confirmed or refuted the claimed result.
Validators (see :mod:`espresso_proof.validators`) then forbid a check from
claiming ``checked`` when the machine refuted it.
"""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from fractions import Fraction
from math import gcd
from pathlib import Path

from .io import read_yaml, write_yaml
from .models import ComputationChecks, ComputationVerificationReport

BACKEND = "espresso-stdlib-verifier"

_ARITH_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_ARITH_UNARY = (ast.UAdd, ast.USub)


class VerifierError(Exception):
    """Raised when a machine check cannot be executed safely."""


def _eval_arith(expr: str) -> Fraction:
    """Evaluate an exact rational expression. Raises VerifierError on anything unsafe."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise VerifierError(f"could not parse expression: {exc}") from exc
    return _eval_node(tree.body)


def _eval_node(node: ast.AST) -> Fraction:
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ARITH_BINOPS):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise VerifierError("division by zero")
            return left / right
        # Pow
        if right.denominator != 1:
            raise VerifierError("only integer exponents are supported")
        return left ** int(right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _ARITH_UNARY):
        operand = _eval_node(node.operand)
        return operand if isinstance(node.op, ast.UAdd) else -operand
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return Fraction(node.value)
    raise VerifierError(
        "unsupported expression element; allow only integers and + - * / ** "
        "(write fractions as a/b)"
    )


def _frac_str(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _permutation_order(expr: str) -> int:
    """Order of a permutation written in cycle notation, e.g. ``(1 2 3)(4 5)``."""
    cycles = re.findall(r"\(([^)]*)\)", expr)
    if not cycles:
        raise VerifierError("expected cycle notation like (1 2 3)(4 5)")
    order = 1
    for raw in cycles:
        elements = [token for token in re.split(r"[,\s]+", raw.strip()) if token]
        if len(set(elements)) != len(elements):
            raise VerifierError(f"repeated element in cycle ({raw.strip()})")
        length = len(elements)
        if length <= 1:
            continue
        order = order * length // gcd(order, length)
    return order


def verify_check(check: dict) -> dict | None:
    """Verify one computation check. Returns a result dict, or None if it has no machine spec."""
    spec = check.get("machine_check")
    if not spec:
        return None
    kind = spec["kind"]
    expression = spec["expression"]
    expected = spec["expected"]
    base = {
        "check_id": check["id"],
        "claim_id": check["claim_id"],
        "kind": kind,
        "expected": expected,
    }
    try:
        if kind == "exact_arithmetic":
            computed = _eval_arith(expression)
            expected_value = _eval_arith(expected)
            computed_str = _frac_str(computed)
            verdict = "confirmed" if computed == expected_value else "refuted"
            backend = "stdlib_fraction"
        elif kind == "permutation_order":
            computed_value = _permutation_order(expression)
            expected_value = _eval_arith(expected)
            if expected_value.denominator != 1:
                raise VerifierError("permutation_order expected must be an integer")
            computed_str = str(computed_value)
            verdict = "confirmed" if computed_value == int(expected_value) else "refuted"
            backend = "stdlib_permutation"
        else:
            return {
                **base,
                "backend": BACKEND,
                "executed": False,
                "verdict": "unsupported",
                "computed": "",
                "detail": f"unknown machine_check kind: {kind}",
            }
    except VerifierError as exc:
        return {
            **base,
            "backend": BACKEND,
            "executed": False,
            "verdict": "error",
            "computed": "",
            "detail": str(exc),
        }
    return {
        **base,
        "backend": backend,
        "executed": True,
        "verdict": verdict,
        "computed": computed_str,
        "detail": f"executed {expression!r}",
    }


def build_verification_report(computations: dict) -> dict:
    """Build a verification report dict from a computation_checks dict."""
    results = []
    for check in computations.get("checks", []):
        result = verify_check(check)
        if result is not None:
            results.append(result)
    report = {
        "schema_version": 1,
        "claim_id": computations["claim_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "backend": BACKEND,
        "results": results,
    }
    ComputationVerificationReport.model_validate(report)
    return report


def verify_computation_checks(run_dir: Path) -> dict:
    """Execute machine checks for a run and write computation_verification.yaml.

    Registers the artifact in the run manifest's optional_artifacts so the
    standard validator picks it up.
    """
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    computations_path = run_dir / manifest["artifacts"]["computation_checks"]
    computations = read_yaml(computations_path)
    ComputationChecks.model_validate(computations)
    report = build_verification_report(computations)
    write_yaml(run_dir / "computation_verification.yaml", report)
    manifest.setdefault("optional_artifacts", {})["computation_verification"] = (
        "computation_verification.yaml"
    )
    write_yaml(manifest_path, manifest)
    return report
