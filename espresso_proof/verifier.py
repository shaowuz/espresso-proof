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


# A multivariate polynomial over Q is a dict mapping a canonical monomial key
# (a sorted tuple of ``(variable, exponent)`` pairs, ``()`` for the constant
# term) to a nonzero Fraction coefficient. This gives an exact normal form, so
# two expressions are equal as polynomials iff their dicts are equal.
_MAX_POLY_TERMS = 5000        # bound the normal-form size
_MAX_POLY_EXPONENT = 100      # bound ** exponent

# Bounds for the FALSE-identity rational-witness search (fail closed). A real
# counterexample for a non-identity must be cheap to find; anything beyond these
# bounds earns NO status (verdict ``error``), never a witness.
_MAX_WITNESS_VARS = 6         # cap declared/used variable count
_MAX_WITNESS_TOTAL_DEGREE = 50  # cap total degree across the combined polynomials
_WITNESS_GRID = (Fraction(0), Fraction(1), Fraction(-1), Fraction(2), Fraction(1, 2))


def _poly_normalize(poly: dict[tuple, Fraction]) -> dict[tuple, Fraction]:
    return {mono: coeff for mono, coeff in poly.items() if coeff != 0}


def _poly_mul(a: dict[tuple, Fraction], b: dict[tuple, Fraction]) -> dict[tuple, Fraction]:
    out: dict[tuple, Fraction] = {}
    for amono, acoeff in a.items():
        for bmono, bcoeff in b.items():
            exps: dict[str, int] = {}
            for var, exp in amono:
                exps[var] = exps.get(var, 0) + exp
            for var, exp in bmono:
                exps[var] = exps.get(var, 0) + exp
            mono = tuple(sorted((v, e) for v, e in exps.items() if e != 0))
            out[mono] = out.get(mono, Fraction(0)) + acoeff * bcoeff
            if len(out) > _MAX_POLY_TERMS:
                raise VerifierError("polynomial too large for safe verification")
    return _poly_normalize(out)


def _poly_pow(base: dict[tuple, Fraction], exp: int) -> dict[tuple, Fraction]:
    result: dict[tuple, Fraction] = {(): Fraction(1)}
    for _ in range(exp):
        result = _poly_mul(result, base)
    return result


def _poly_eval_node(node: ast.AST) -> dict[tuple, Fraction]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ARITH_BINOPS):
        if isinstance(node.op, ast.Add):
            return _poly_add(_poly_eval_node(node.left), _poly_eval_node(node.right), 1)
        if isinstance(node.op, ast.Sub):
            return _poly_add(_poly_eval_node(node.left), _poly_eval_node(node.right), -1)
        if isinstance(node.op, ast.Mult):
            return _poly_mul(_poly_eval_node(node.left), _poly_eval_node(node.right))
        if isinstance(node.op, ast.Div):
            divisor = _poly_eval_node(node.right)
            const = _poly_as_constant(divisor)
            if const is None:
                raise VerifierError("polynomial division is only supported by a nonzero constant")
            if const == 0:
                raise VerifierError("division by zero")
            return _poly_normalize({mono: coeff / const for mono, coeff in _poly_eval_node(node.left).items()})
        # Pow
        exp_poly = _poly_eval_node(node.right)
        exp_const = _poly_as_constant(exp_poly)
        if exp_const is None or exp_const.denominator != 1 or exp_const < 0:
            raise VerifierError("polynomial exponent must be a non-negative integer")
        exp = int(exp_const)
        if exp > _MAX_POLY_EXPONENT:
            raise VerifierError("polynomial exponent too large for safe verification")
        return _poly_pow(_poly_eval_node(node.left), exp)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _ARITH_UNARY):
        operand = _poly_eval_node(node.operand)
        if isinstance(node.op, ast.UAdd):
            return operand
        return _poly_normalize({mono: -coeff for mono, coeff in operand.items()})
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return _poly_normalize({(): Fraction(node.value)})
    if isinstance(node, ast.Name):
        return {((node.id, 1),): Fraction(1)}
    raise VerifierError(
        "unsupported polynomial element; allow only variables, integers, and + - * / ** "
        "(division only by a nonzero constant)"
    )


def _poly_add(a: dict[tuple, Fraction], b: dict[tuple, Fraction], sign: int) -> dict[tuple, Fraction]:
    out = dict(a)
    for mono, coeff in b.items():
        out[mono] = out.get(mono, Fraction(0)) + sign * coeff
    return _poly_normalize(out)


def _poly_as_constant(poly: dict[tuple, Fraction]) -> Fraction | None:
    poly = _poly_normalize(poly)
    if not poly:
        return Fraction(0)
    if set(poly) == {()}:
        return poly[()]
    return None


def _parse_polynomial(expr: str) -> dict[tuple, Fraction]:
    if len(expr) > _MAX_EXPR_LEN:
        raise VerifierError("polynomial expression too long for safe verification")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise VerifierError(f"could not parse polynomial: {exc}") from exc
    except RecursionError as exc:  # pragma: no cover - defensive
        raise VerifierError("polynomial nested too deeply") from exc
    return _poly_eval_node(tree.body)


def _poly_str(poly: dict[tuple, Fraction]) -> str:
    poly = _poly_normalize(poly)
    if not poly:
        return "0"
    parts = []
    for mono in sorted(poly):
        coeff = poly[mono]
        factors = "*".join(f"{v}^{e}" if e != 1 else v for v, e in mono)
        if not factors:
            parts.append(_frac_str(coeff))
        elif coeff == 1:
            parts.append(factors)
        elif coeff == -1:
            parts.append(f"-{factors}")
        else:
            parts.append(f"{_frac_str(coeff)}*{factors}")
    return " + ".join(parts)


def _poly_variables(poly: dict[tuple, Fraction]) -> set[str]:
    """Variable names occurring in a polynomial's normal form."""
    return {var for mono in poly for var, _exp in mono}


def _poly_total_degree(poly: dict[tuple, Fraction]) -> int:
    """Maximum total degree of any monomial (0 for the zero/constant polynomial)."""
    return max((sum(exp for _v, exp in mono) for mono in poly), default=0)


def _poly_eval_at(poly: dict[tuple, Fraction], assignment: dict[str, Fraction]) -> Fraction:
    """Evaluate a polynomial at an exact rational assignment of all its variables."""
    total = Fraction(0)
    for mono, coeff in poly.items():
        term = coeff
        for var, exp in mono:
            term *= assignment[var] ** exp
        total += term
    return total


def find_polynomial_counterexample(
    lhs: dict[tuple, Fraction], rhs: dict[tuple, Fraction]
) -> dict | None:
    """Find a durable rational witness that ``lhs != rhs`` as functions over Q.

    Searches a small bounded grid (each variable over {0, 1, -1, 2, 1/2}) and
    returns the first assignment where the exact values differ, recording the
    assignment plus exact ``lhs``, ``rhs`` and nonzero ``difference``.

    Fails closed via :class:`VerifierError` when the inputs exceed the witness
    caps (too many variables / too high total degree); the caller turns that into
    verdict ``error`` so an over-limit input earns NO status. Returns ``None``
    only if no grid point separated the two polynomials (the caller still has the
    exact ``refuted`` verdict from the normal-form comparison).
    """
    variables = sorted(_poly_variables(lhs) | _poly_variables(rhs))
    if len(variables) > _MAX_WITNESS_VARS:
        raise VerifierError(
            f"too many variables for safe witness search (max {_MAX_WITNESS_VARS})"
        )
    total_degree = max(_poly_total_degree(lhs), _poly_total_degree(rhs))
    if total_degree > _MAX_WITNESS_TOTAL_DEGREE:
        raise VerifierError(
            f"polynomial total degree too high for safe witness search "
            f"(max {_MAX_WITNESS_TOTAL_DEGREE})"
        )
    if not variables:
        # Constant polynomials: a single (empty) assignment decides it.
        lhs_val = _poly_eval_at(lhs, {})
        rhs_val = _poly_eval_at(rhs, {})
        if lhs_val != rhs_val:
            return {
                "assignment": {},
                "lhs_value": _frac_str(lhs_val),
                "rhs_value": _frac_str(rhs_val),
                "difference": _frac_str(lhs_val - rhs_val),
            }
        return None
    # Grid size is bounded by len(_WITNESS_GRID) ** _MAX_WITNESS_VARS, which is
    # 5**6 = 15625 evaluations in the worst case — cheap and terminating.
    import itertools

    for combo in itertools.product(_WITNESS_GRID, repeat=len(variables)):
        assignment = dict(zip(variables, combo))
        lhs_val = _poly_eval_at(lhs, assignment)
        rhs_val = _poly_eval_at(rhs, assignment)
        if lhs_val != rhs_val:
            return {
                "assignment": {var: _frac_str(val) for var, val in assignment.items()},
                "lhs_value": _frac_str(lhs_val),
                "rhs_value": _frac_str(rhs_val),
                "difference": _frac_str(lhs_val - rhs_val),
            }
    return None


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
    witness = None
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
        elif kind == "polynomial_identity":
            lhs = _parse_polynomial(expression)
            rhs = _parse_polynomial(expected)
            computed_str = _poly_str(lhs)
            verdict = "confirmed" if lhs == rhs else "refuted"
            backend = "stdlib_polynomial"
            if verdict == "refuted":
                # The identity is FALSE over Q. Produce a durable rational witness
                # (the search caps fail closed to verdict ``error`` on over-limit
                # input — never a witness). The witness refutes ONLY this exact
                # identity; persistence (see persist_polynomial_witness) records it
                # with conservative scope flags so it can never refute the claim.
                witness = find_polynomial_counterexample(lhs, rhs)
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
    result = {
        **base,
        "backend": backend,
        "executed": True,
        "verdict": verdict,
        "computed": computed_str,
        "detail": f"executed {expression!r}",
    }
    if witness is not None:
        result["witness"] = witness
    return result


def _polynomial_witness_test(check: dict, result: dict) -> dict:
    """Build a CounterexampleTest dict for a refuted polynomial identity witness.

    Scope flags are conservative and deterministic (never agent prose): the
    witness refutes ONLY this exact polynomial identity (``refutes_step_only``),
    never the broader claim. It drops the claim's hypotheses
    (``satisfies_all_claim_hypotheses=False``), so the existing
    ``CounterexampleTest.validate_refutation_scope`` would reject any attempt to
    set ``refutes_claim``.
    """
    witness = result["witness"]
    assignment = witness["assignment"]
    pretty = ", ".join(f"{var}={val}" for var, val in sorted(assignment.items())) or "(constants)"
    explanation = (
        f"Polynomial identity {result['kind']} is FALSE over Q: "
        f"lhs={check['machine_check']['expression']!r} != "
        f"rhs={check['machine_check']['expected']!r}. "
        f"At {pretty}: lhs={witness['lhs_value']}, rhs={witness['rhs_value']}, "
        f"difference={witness['difference']} (nonzero). "
        f"Refutes ONLY this exact identity (the proof step), not the claim."
    )
    return {
        "id": f"polywitness_{check['id']}",
        "operator_id": "polynomial_identity_witness",
        "example_id": check["id"],
        # The witness is a bare rational point; it does not satisfy the claim's
        # hypotheses, which is exactly why it can refute only the step.
        "satisfies_all_claim_hypotheses": False,
        "target": "proof_step",
        "result": "failed",
        "refutes_claim": False,
        "refutes_step_only": True,
        "exposes_missing_hypothesis": False,
        "support": (
            f"stdlib_polynomial exact rational evaluation; witness {witness['assignment']}"
        ),
        "explanation": explanation,
    }


def persist_polynomial_witness(run_dir: Path, check: dict, result: dict) -> Path | None:
    """Persist a refuted polynomial identity's rational witness as a durable
    CounterexampleTest through the EXISTING counterexamples persistence path.

    Reuses the existing ``CounterexampleTest`` model + counterexamples artifact
    (single-file ``counterexamples.yaml`` or a per-item bank directory). Returns
    the path written, or ``None`` if the result carries no witness (confirmed
    identity, or an over-limit search that failed closed to verdict ``error``).
    Idempotent: a witness whose id already exists is not re-appended.
    """
    if result.get("verdict") != "refuted" or "witness" not in result:
        return None

    from .models import CounterexampleReport, CounterexampleTest
    from .pressure_status import build_pressure_status
    from .research_bank import append_bank_item, bankable_artifact_path, is_bank_dir

    test = _polynomial_witness_test(check, result)
    CounterexampleTest.model_validate(test)  # fail closed on the scope guard

    cx_path = bankable_artifact_path(run_dir, "counterexamples")
    if is_bank_dir(cx_path):
        existing_ids = {
            read_yaml(p).get("id")
            for p in cx_path.glob("*.yaml")
            if p.name != "_meta.yaml"
        }
        if test["id"] in existing_ids:
            if (Path(run_dir) / "pressure_status.yaml").exists():
                build_pressure_status(run_dir)
            return cx_path
        written = append_bank_item(cx_path, test)
        if (Path(run_dir) / "pressure_status.yaml").exists():
            build_pressure_status(run_dir)
        return written

    bank = read_yaml(cx_path) if cx_path.exists() else {"claim_id": check["claim_id"], "tests": []}
    bank.setdefault("tests", [])
    if any(t.get("id") == test["id"] for t in bank["tests"]):
        if (Path(run_dir) / "pressure_status.yaml").exists():
            build_pressure_status(run_dir)
        return cx_path
    bank["tests"].append(test)
    CounterexampleReport.model_validate(bank)  # fail closed
    write_yaml(cx_path, bank)
    if (Path(run_dir) / "pressure_status.yaml").exists():
        build_pressure_status(run_dir)
    return cx_path


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
    from .claim_clear import ensure_claim_clear_for_downstream

    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
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

    from .claim_clear import ensure_claim_clear_for_downstream
    from .pressure_status import build_pressure_status
    from .validators import validate_run  # local import to avoid an import cycle

    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    report = verify_computation_checks(run_dir)
    result_by_check = {r["check_id"]: r for r in report["results"]}

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    comp_path = run_dir / manifest["artifacts"]["computation_checks"]
    computations = read_yaml(comp_path)
    for check in computations.get("checks", []):
        if not check.get("machine_check"):
            continue
        result = result_by_check.get(check["id"], {})
        verdict = result.get("verdict")
        if verdict == "confirmed":
            check["status"] = "checked"
            check["method"] = "exact_arithmetic"
        elif verdict == "refuted":
            check["status"] = "wrong"  # the computed value is wrong (computation-scoped)
            # A FALSE polynomial identity yields a durable rational witness,
            # persisted ONLY as a step-only counterexample (never claim-level).
            if "witness" in result:
                persist_polynomial_witness(run_dir, check, result)
        else:  # error / unsupported / not run
            check["status"] = "human_needed"
            check["method"] = "manual_review_needed"
    ComputationChecks.model_validate(computations)
    write_yaml(comp_path, computations)
    if (run_dir / "pressure_status.yaml").exists():
        build_pressure_status(run_dir)
    validate_run(run_dir)  # fail closed: confirming-result + evidence-method rules fire here
    return computations
