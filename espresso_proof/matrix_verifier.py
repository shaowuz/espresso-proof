"""Deterministic matrix rank / determinant verifier (Goal 06).

A trusted, dependency-free backend that checks the rank and/or determinant of ONE
explicit integer matrix using exact rational (Fraction) Gaussian elimination. A
confirming result earns ``instance_checked`` for that specific matrix only — a
finite/computational check, never a universal proof about any family of matrices
or maps (e.g. it can witness a concrete rank/dimension jump, but does not prove a
general statement). Fails closed on a malformed, non-rectangular, non-integer, or
too-large matrix.
"""

from __future__ import annotations

from fractions import Fraction

# DoS caps: a scoped check stays small.
_MAX_DIM = 16
_MAX_ABS = 10**6

_SCOPE_NOTE = (
    "Machine-checked the rank/determinant of THIS specific integer matrix only. "
    "This is a finite/computational check, NOT a universal proof about any family "
    "of matrices, maps, or fibers."
)


def _is_int_matrix(matrix: object) -> bool:
    if not isinstance(matrix, list) or not matrix:
        return False
    if not all(isinstance(row, list) and row for row in matrix):
        return False
    width = len(matrix[0])
    for row in matrix:
        if len(row) != width:
            return False
        for x in row:
            if not isinstance(x, int) or isinstance(x, bool):
                return False
            if abs(x) > _MAX_ABS:
                return False
    return True


def _rank(matrix: list[list[int]]) -> int:
    rows = [[Fraction(x) for x in row] for row in matrix]
    nrows, ncols = len(rows), len(rows[0])
    pr = 0
    for c in range(ncols):
        piv = next((r for r in range(pr, nrows) if rows[r][c] != 0), None)
        if piv is None:
            continue
        rows[pr], rows[piv] = rows[piv], rows[pr]
        pivval = rows[pr][c]
        for r in range(nrows):
            if r != pr and rows[r][c] != 0:
                f = rows[r][c] / pivval
                rows[r] = [a - f * b for a, b in zip(rows[r], rows[pr])]
        pr += 1
        if pr == nrows:
            break
    return pr


def _determinant(matrix: list[list[int]]) -> Fraction:
    n = len(matrix)
    rows = [[Fraction(x) for x in row] for row in matrix]
    det = Fraction(1)
    for c in range(n):
        piv = next((r for r in range(c, n) if rows[r][c] != 0), None)
        if piv is None:
            return Fraction(0)
        if piv != c:
            rows[c], rows[piv] = rows[piv], rows[c]
            det = -det
        det *= rows[c][c]
        for r in range(c + 1, n):
            f = rows[r][c] / rows[c][c]
            rows[r] = [a - f * b for a, b in zip(rows[r], rows[c])]
    return det


def verify_matrix_rank(
    matrix: object,
    *,
    expected_rank: object = None,
    expected_determinant: object = None,
) -> dict:
    """Verify a matrix's rank and/or determinant against expected values.

    verdict: ``confirmed`` (all provided expectations match), ``refuted`` (a
    provided expectation mismatches), ``unsupported`` (nothing to check), or
    ``error`` (malformed input). Only ``confirmed`` may earn a checked status."""
    if not _is_int_matrix(matrix):
        return {"verdict": "error",
                "detail": "matrix must be a non-empty rectangular integer matrix within caps",
                "computed_rank": None, "computed_determinant": None, "scope_note": _SCOPE_NOTE}
    if len(matrix) > _MAX_DIM or len(matrix[0]) > _MAX_DIM:
        return {"verdict": "error", "detail": f"matrix exceeds {_MAX_DIM}x{_MAX_DIM} cap",
                "computed_rank": None, "computed_determinant": None, "scope_note": _SCOPE_NOTE}

    def _bad_int(v: object) -> bool:
        return not (v is None or (isinstance(v, int) and not isinstance(v, bool)))

    if _bad_int(expected_rank) or _bad_int(expected_determinant):
        return {"verdict": "error", "detail": "expected_rank/expected_determinant must be integers",
                "computed_rank": None, "computed_determinant": None, "scope_note": _SCOPE_NOTE}
    if expected_rank is None and expected_determinant is None:
        return {"verdict": "unsupported", "detail": "no expected_rank or expected_determinant provided",
                "computed_rank": None, "computed_determinant": None, "scope_note": _SCOPE_NOTE}

    computed_rank = _rank(matrix)
    computed_det: int | None = None
    confirmed = True
    details: list[str] = []

    if expected_rank is not None:
        ok = computed_rank == expected_rank
        confirmed = confirmed and ok
        details.append(f"rank={computed_rank} (expected {expected_rank}: {'ok' if ok else 'mismatch'})")

    if expected_determinant is not None:
        if len(matrix) != len(matrix[0]):
            return {"verdict": "error", "detail": "determinant requires a square matrix",
                    "computed_rank": computed_rank, "computed_determinant": None, "scope_note": _SCOPE_NOTE}
        det = _determinant(matrix)
        computed_det = int(det)  # determinant of an integer matrix is an integer
        ok = computed_det == expected_determinant
        confirmed = confirmed and ok
        details.append(
            f"determinant={computed_det} (expected {expected_determinant}: {'ok' if ok else 'mismatch'})"
        )

    return {
        "verdict": "confirmed" if confirmed else "refuted",
        "detail": "; ".join(details),
        "computed_rank": computed_rank,
        "computed_determinant": computed_det,
        "scope_note": _SCOPE_NOTE,
    }
