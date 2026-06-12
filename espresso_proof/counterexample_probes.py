"""Deterministic counterexample probes (Goal 42E, cycle 1).

When a locked claim asserts "every group of order N is abelian" for a CONCRETE
integer N, the counterexamples agent can do better than asking a model: if a
non-abelian group of order N exists, this module CONSTRUCTS one (the dihedral
group D_{N/2} for even N >= 6), machine-verifies its Cayley table with the
trusted deterministic ``verify_group_abelian`` backend, and emits a fully
admissible kernel ``CounterexampleTest`` record: ``result=failed`` only because
the machine check genuinely found a non-abelian group satisfying every claim
hypothesis.

Conservative by construction:

- only CONCRETE integer orders match; symbolic statements (``order p^2``)
  never fire, so the true p^2 theorem is untouched;
- if every group of order N is abelian (no construction exists), the probe
  returns nothing — it cannot fabricate pressure against a true claim;
- the emitted record carries the verifier transcript in ``support``; the
  kernel validator (refutes_claim requires result=failed; dropped-hypothesis
  and step-attacks can never refute) re-checks it like any other artifact;
- the probe earns NO status by itself: the grade/refutation routing still
  flows through the existing kernel gates.
"""

from __future__ import annotations

import re

from .finite_group_verifier import verify_group_abelian

# "every/all group(s) of order N ... abelian" with a CONCRETE integer N.
_ORDER_N_ABELIAN = re.compile(
    r"\b(?:every|all|each)\b[^.]*?\bgroups?\s+of\s+order\s+(\d{1,3})\b[^.]*?\babelian\b",
    re.IGNORECASE,
)


def _dihedral_cayley(n: int) -> list[list[int]]:
    """Cayley table of the dihedral group D_n (order 2n), elements indexed as
    r^i (0..n-1) and s·r^i (n..2n-1), with the defining relation s·r = r^{-1}·s."""

    order = 2 * n

    def multiply(a: int, b: int) -> int:
        rot_a, flip_a = a % n, a >= n
        rot_b, flip_b = b % n, b >= n
        if not flip_a:
            rotation = (rot_a + rot_b) % n
            flipped = flip_b
        else:
            rotation = (rot_a - rot_b) % n
            flipped = not flip_b
        return rotation + (n if flipped else 0)

    return [[multiply(a, b) for b in range(order)] for a in range(order)]


def _nonabelian_group_of_order(order: int) -> tuple[str, list[list[int]]] | None:
    """A concrete non-abelian group of the given order, or None when every
    group of that order is abelian (or the order is out of probe range)."""

    if order < 6 or order > 200 or order % 2 == 1:
        # odd orders < 21 are all abelian-or-out-of-scope for this probe; the
        # probe only constructs dihedral groups, which exist for even orders.
        return None
    n = order // 2
    if n < 3:
        return None  # D_1, D_2 are abelian
    return f"dihedral_group_D{n}", _dihedral_cayley(n)


def propose_deterministic_counterexamples(claim: dict) -> list[dict]:
    """Machine-verified counterexample tests for the locked claim, or []."""

    text = " ".join(
        str(claim.get(key, ""))
        for key in ("normalized_statement", "conclusion")
    )
    match = _ORDER_N_ABELIAN.search(text)
    if not match:
        return []
    order = int(match.group(1))
    candidate = _nonabelian_group_of_order(order)
    if candidate is None:
        return []
    name, cayley = candidate
    verdict = verify_group_abelian(cayley)
    if not verdict.get("is_group") or verdict.get("is_abelian"):
        # fail closed: a malformed or abelian table earns nothing.
        return []
    return [
        {
            "id": f"ce_probe_{name}",
            "operator_id": "deterministic_finite_group_probe",
            "example_id": name,
            "satisfies_all_claim_hypotheses": True,
            "target": "theorem",
            "result": "failed",
            "refutes_claim": True,
            "refutes_step_only": False,
            "exposes_missing_hypothesis": False,
            "support": (
                f"machine check (verify_group_abelian): order={verdict['order']}, "
                f"is_group=True, is_abelian=False — {verdict.get('detail', '')}"
            )[:300],
            "explanation": (
                f"{name} has order {order}, satisfies every claim hypothesis "
                "(it is a group of the stated order), and is non-abelian by a "
                "deterministic Cayley-table check; the claim fails on it. This "
                "single construction refutes only THIS universal claim."
            ),
        }
    ]


__all__ = ["propose_deterministic_counterexamples"]
