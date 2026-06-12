"""Deterministic counterexample probes (Goal 42E, cycle 1).

When a locked claim states EXACTLY "every group of order N is abelian" for a
concrete integer N, the counterexamples pipeline can do better than asking a
model: if a non-abelian group of order N exists, this module CONSTRUCTS one
(the dihedral group D_{N/2} for even N >= 6) and machine-verifies its Cayley
table with the trusted deterministic ``verify_group_abelian`` backend.

Trust model (reviewed by Kernel Safety + Math Workflow, cycle 1):

- The kernel's refutation gates check FLAG coherence (refutes_claim requires
  result=failed and all-hypotheses admissibility), not the mathematics, and an
  admissible refuting record can flip ``idea_grade`` to ``refuted`` on a
  precise claim. The trusted base for this record is therefore this module's
  claim-form identification PLUS the deterministic constructor/verifier — so
  the identification must be fail-closed, not fuzzy.
- The statement match is an ANCHORED FULLMATCH of the whole normalized
  statement with no free modifiers: negations, disjunctions, extra adjectives
  ("cyclic", "nilpotent"), "subgroup"/"quotient" phrasings, and "non-abelian"
  all fail to match. The ``conclusion`` field is never concatenated into the
  search text.
- The record asserts ``satisfies_all_claim_hypotheses=True`` (and hence
  ``refutes_claim=True``) ONLY when the locked claim's hypothesis list is
  non-empty and every hypothesis is the trivial "is a (finite) group of order
  N" content. Any other hypothesis fails closed: the probe returns nothing.
- When the hypothesis list is EMPTY (the claim parser has not determined the
  hypotheses, e.g. the mock pipeline's vague-claim path), the probe still
  reports the machine fact — a non-abelian group of order N exists and the
  Cayley-table check ran (``result=failed``) — but DOWNGRADES admissibility to
  ``"unclear"`` and sets ``refutes_claim=False``. An undetermined hypothesis
  ledger never earns a refutation.
- If every group of order N is abelian (no construction exists), or the order
  is symbolic/odd/out of range, the probe returns nothing — it cannot
  fabricate pressure against a true claim.
- The probe earns NO status by itself: grade/refutation routing still flows
  through the existing kernel gates.
"""

from __future__ import annotations

import re

from .finite_group_verifier import verify_group_abelian

# Anchored claim form: the WHOLE normalized statement must be exactly
# "every/all/each (finite) group(s) of order N is/are abelian." — no free
# modifiers anywhere, so "it is not true that ...", "... abelian or dihedral",
# "every cyclic group ...", "... has an abelian subgroup", and
# "... is non-abelian" can never match.
_ORDER_N_ABELIAN_EXACT = re.compile(
    r"(?:every|all|each)\s+(?:finite\s+)?groups?\s+of\s+order\s+(\d{1,3})\s+"
    r"(?:is|are)\s+abelian\s*\.?",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    return " ".join(str(text).split()).strip().lower()


def _trivial_hypothesis_pattern(order: int) -> re.Pattern[str]:
    """Hypothesis names that say nothing beyond 'G is a (finite) group of
    order N' — the content already carried by the matched statement."""

    n = str(order)
    return re.compile(
        r"(?:"
        rf"(?:g\s+is\s+)?(?:a\s+|the\s+)?(?:finite\s+)?groups?(?:\s+of\s+order\s+{n})?"
        rf"|(?:g\s+)?has\s+order\s+{n}"
        rf"|order(?:\s+of\s+g)?\s*(?:=|is)?\s*{n}"
        rf"|\|\s*g\s*\|\s*=\s*{n}"
        r")\s*\.?"
    )


def _hypotheses_admissibility(claim: dict, order: int) -> bool | None:
    """True: all hypotheses are determined and trivial (full admissibility).
    None: hypotheses undetermined (empty list) — downgrade, never refute.
    False: any non-trivial hypothesis — the probe must stay silent."""

    hypotheses = claim.get("hypotheses") or []
    if not hypotheses:
        return None
    pattern = _trivial_hypothesis_pattern(order)
    for hypothesis in hypotheses:
        name = hypothesis.get("name", "") if isinstance(hypothesis, dict) else hypothesis
        if not pattern.fullmatch(_normalize(name)):
            return False
    return True


def _dihedral_cayley(n: int) -> list[list[int]]:
    """Cayley table of the dihedral group D_n (order 2n), elements indexed as
    r^i (0..n-1) and r^i·s (n..2n-1), with the defining relation
    s·r^i = r^{-i}·s. Nothing is trusted from this construction: the verifier
    independently re-proves closure, identity, inverses, and associativity."""

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
    """A concrete non-abelian group of the given order, or None when this
    probe cannot construct one. Dihedral groups have even order, so every odd
    order is out of scope for this probe regardless of whether a non-abelian
    group of that order exists (the smallest such odd order is 21)."""

    # The 200 cap bounds the synchronous O(order^3) pure-Python associativity
    # re-check in verify_group_abelian (~8M multiplications at the cap, a few
    # seconds); claims about larger concrete orders simply stay agent-only.
    if order < 6 or order > 200 or order % 2 == 1:
        return None
    n = order // 2
    if n < 3:
        return None  # D_1, D_2 are abelian
    return f"dihedral_group_D{n}", _dihedral_cayley(n)


def propose_deterministic_counterexamples(claim: dict) -> list[dict]:
    """Machine-verified counterexample tests for the locked claim, or []."""

    match = _ORDER_N_ABELIAN_EXACT.fullmatch(_normalize(claim.get("normalized_statement", "")))
    if not match:
        return []
    order = int(match.group(1))
    admissibility = _hypotheses_admissibility(claim, order)
    if admissibility is False:
        # a non-trivial hypothesis (e.g. "G is cyclic") might exclude the
        # construction; fail closed entirely.
        return []
    candidate = _nonabelian_group_of_order(order)
    if candidate is None:
        return []
    name, cayley = candidate
    verdict = verify_group_abelian(cayley)
    if not verdict.get("is_group") or verdict.get("is_abelian"):
        # fail closed: a malformed or abelian table earns nothing.
        return []
    fully_admissible = admissibility is True
    if fully_admissible:
        explanation = (
            f"{name} has order {order}, satisfies every claim hypothesis "
            "(the locked hypotheses say only that it is a group of the stated "
            "order), and is non-abelian by a deterministic Cayley-table "
            "check; the claim fails on it. This single construction refutes "
            "only THIS universal claim."
        )
    else:
        explanation = (
            f"{name} has order {order} and is non-abelian by a deterministic "
            "Cayley-table check, so the universal statement as written fails "
            "on it — but the claim's hypotheses are not yet determined, so "
            "admissibility is unclear and this record does NOT refute the "
            "claim."
        )
    return [
        {
            "id": f"ce_probe_{name}",
            "operator_id": "deterministic_finite_group_probe",
            "example_id": name,
            "satisfies_all_claim_hypotheses": True if fully_admissible else "unclear",
            "target": "theorem",
            "result": "failed",
            "refutes_claim": fully_admissible,
            "refutes_step_only": False,
            "exposes_missing_hypothesis": False,
            "support": (
                f"machine check (verify_group_abelian): order={verdict['order']}, "
                f"is_group=True, is_abelian=False — {verdict.get('detail', '')}"
            )[:300],
            "explanation": explanation,
        }
    ]


__all__ = ["propose_deterministic_counterexamples"]
