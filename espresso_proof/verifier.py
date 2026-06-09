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


# Resource bounds so an attacker-/agent-controlled spec cannot hang the kernel or
# exhaust memory (DoS). A confirmed result must be cheap to compute; anything
# beyond these bounds fails closed to verdict ``error`` (never ``confirmed``).
_MAX_EXPR_LEN = 1000          # input length (also bounds parse/recursion depth)
_MAX_EXPONENT = 1000          # exponent magnitude for **
_MAX_RESULT_BITS = 100_000    # estimated bit-length of a ** result
_MAX_MATRIX_DIM = 12          # max rows/cols for a matrix backend (bounds O(n^3) cost)


def _eval_arith(expr: str) -> Fraction:
    """Evaluate an exact rational expression. Raises VerifierError on anything unsafe."""
    if len(expr) > _MAX_EXPR_LEN:
        raise VerifierError("expression too long for safe verification")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise VerifierError(f"could not parse expression: {exc}") from exc
    except RecursionError as exc:  # pragma: no cover - defensive
        raise VerifierError("expression nested too deeply") from exc
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
        exp = int(right)
        if abs(exp) > _MAX_EXPONENT:
            raise VerifierError("exponent too large for safe verification")
        base_bits = max(
            abs(left.numerator).bit_length(), abs(left.denominator).bit_length(), 1
        )
        if base_bits * abs(exp) > _MAX_RESULT_BITS:
            raise VerifierError("exponentiation result too large for safe verification")
        return left ** exp
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


def _parse_matrix(expr: str) -> list[list[Fraction]]:
    """Parse a matrix literal like ``[[1, 2], [3, 4]]`` into exact Fractions.

    Entries may be integers or rationals written as ``a/b`` (any expression the
    safe arithmetic evaluator accepts). Fails closed on anything malformed,
    non-rectangular, or larger than the dimension bound.
    """
    if len(expr) > _MAX_EXPR_LEN:
        raise VerifierError("matrix expression too long for safe verification")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise VerifierError(f"could not parse matrix: {exc}") from exc
    body = tree.body
    if not isinstance(body, ast.List) or not body.elts:
        raise VerifierError("expected a non-empty matrix like [[1, 2], [3, 4]]")
    if len(body.elts) > _MAX_MATRIX_DIM:
        raise VerifierError(f"matrix too large for safe verification (max {_MAX_MATRIX_DIM} rows)")
    rows: list[list[Fraction]] = []
    width: int | None = None
    for row_node in body.elts:
        if not isinstance(row_node, ast.List) or not row_node.elts:
            raise VerifierError("each matrix row must be a non-empty list")
        if width is None:
            width = len(row_node.elts)
            if width > _MAX_MATRIX_DIM:
                raise VerifierError(f"matrix too large for safe verification (max {_MAX_MATRIX_DIM} cols)")
        elif len(row_node.elts) != width:
            raise VerifierError("matrix rows must all have the same length")
        rows.append([_eval_node(entry) for entry in row_node.elts])
    return rows


def _matrix_rank(rows: list[list[Fraction]]) -> int:
    """Exact rank over the rationals via fraction Gaussian elimination."""
    m = [row[:] for row in rows]
    nrows = len(m)
    ncols = len(m[0])
    rank = 0
    for col in range(ncols):
        pivot = next((r for r in range(rank, nrows) if m[r][col] != 0), None)
        if pivot is None:
            continue
        m[rank], m[pivot] = m[pivot], m[rank]
        pivot_value = m[rank][col]
        m[rank] = [value / pivot_value for value in m[rank]]
        for r in range(nrows):
            if r != rank and m[r][col] != 0:
                factor = m[r][col]
                m[r] = [a - factor * b for a, b in zip(m[r], m[rank])]
        rank += 1
        if rank == nrows:
            break
    return rank


def _matrix_determinant(rows: list[list[Fraction]]) -> Fraction:
    """Exact determinant over the rationals (square matrices only)."""
    n = len(rows)
    if any(len(row) != n for row in rows):
        raise VerifierError("determinant requires a square matrix")
    m = [row[:] for row in rows]
    det = Fraction(1)
    for col in range(n):
        pivot = next((r for r in range(col, n) if m[r][col] != 0), None)
        if pivot is None:
            return Fraction(0)
        if pivot != col:
            m[col], m[pivot] = m[pivot], m[col]
            det = -det
        det *= m[col][col]
        pivot_value = m[col][col]
        for r in range(col + 1, n):
            if m[r][col] != 0:
                factor = m[r][col] / pivot_value
                m[r] = [a - factor * b for a, b in zip(m[r], m[col])]
    return det


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
        elif kind == "matrix_rank":
            computed_value = _matrix_rank(_parse_matrix(expression))
            expected_value = _eval_arith(expected)
            if expected_value.denominator != 1:
                raise VerifierError("matrix_rank expected must be an integer")
            computed_str = str(computed_value)
            verdict = "confirmed" if computed_value == int(expected_value) else "refuted"
            backend = "stdlib_linalg_rank"
        elif kind == "matrix_determinant":
            computed = _matrix_determinant(_parse_matrix(expression))
            expected_value = _eval_arith(expected)
            computed_str = _frac_str(computed)
            verdict = "confirmed" if computed == expected_value else "refuted"
            backend = "stdlib_linalg_det"
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


def audit_evidence(run_dir: Path) -> dict:
    """Earn computation_checked from a TRUSTED MACHINE result.

    Runs the machine verifier (the single writer of computation_verification.yaml)
    and then derives each check's status FROM the machine verdict:

    - ``confirmed`` -> status ``checked`` + method ``exact_arithmetic`` (the
      machine-verification method label; ``machine_check.kind`` records the real
      backend);
    - ``refuted``   -> status ``wrong`` (the COMPUTATION's result is wrong — scoped
      to this computation, NOT a claim-level refutation);
    - ``error`` / ``unsupported`` / no result -> status ``human_needed``.

    Only a confirming deterministic backend result can set ``checked`` — the agent
    never self-certifies. Touches ONLY computation-check status/method; never
    idea_grade, tests, source, or proof status. Re-validates the run (fail closed).
    """

    from .validators import validate_run  # local import to avoid an import cycle

    report = verify_computation_checks(run_dir)
    verdict_by_check = {r["check_id"]: r["verdict"] for r in report["results"]}

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    comp_path = run_dir / manifest["artifacts"]["computation_checks"]
    computations = read_yaml(comp_path)
    for check in computations.get("checks", []):
        if not check.get("machine_check"):
            continue
        verdict = verdict_by_check.get(check["id"])
        if verdict == "confirmed":
            check["status"] = "checked"
            check["method"] = "exact_arithmetic"
        elif verdict == "refuted":
            check["status"] = "wrong"  # the computed value is wrong (computation-scoped)
        else:  # error / unsupported / not run
            check["status"] = "human_needed"
            check["method"] = "manual_review_needed"
    ComputationChecks.model_validate(computations)
    write_yaml(comp_path, computations)
    validate_run(run_dir)  # fail closed: confirming-result + evidence-method rules fire here
    return computations
