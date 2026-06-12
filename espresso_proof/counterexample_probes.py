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
    r"(?:every|all|each)\s+(?:finite\s+)?groups?\s+of\s+order\s+([0-9]{1,3})\s+"
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


# Anchored claim form (Goal 42E cycle 8): the WHOLE normalized statement must
# be exactly "every continuous function f: R -> R attains a maximum (value)."
# — no free modifiers, so negations ("no continuous function ..."), weakened
# domains ("on [a, b]"), "bounded", "supremum", and "minimum" phrasings can
# never match. The witness below is specific to THIS form.
_CONTINUOUS_MAX_EXACT = re.compile(
    r"every\s+continuous\s+functions?\s+f\s*:\s*r\s*->\s*r\s+"
    r"attains\s+(?:a|its)\s+maximum(?:\s+value)?\s*\.?",
    re.IGNORECASE,
)


def _verify_strictly_increasing_linear(coeffs: list[int]) -> dict:
    """Exact-arithmetic check that the polynomial with integer coefficients
    ``coeffs`` (ascending degree) is strictly increasing on all of R, via the
    UNIVERSAL identity p(x+1) - p(x) > 0 for degree <= 1: the difference of a
    linear polynomial is a constant, so one exact integer comparison decides
    the fact for every real x — algebra, not sampling. Degree > 1 fails
    closed (the constant-difference argument no longer applies)."""

    if len(coeffs) > 2:
        return {"is_strictly_increasing": False, "detail": "degree > 1: out of scope"}
    slope = coeffs[1] if len(coeffs) == 2 else 0
    # p(x+1) - p(x) == slope exactly, for every real x.
    return {
        "is_strictly_increasing": slope > 0,
        "detail": (
            f"p(x+1) - p(x) = {slope} (constant, exact integer arithmetic); "
            "strictly increasing on R iff that constant is positive"
        ),
    }


# Hypothesis names that say nothing beyond what the matched statement already
# carries ("f is continuous", "f maps R to R") — anything else (e.g. "f is
# bounded") could exclude the identity witness and the probe must stay silent.
_TRIVIAL_CONTINUITY_HYPOTHESIS = re.compile(
    r"(?:"
    r"(?:f\s+is\s+)?continuous(?:\s+on\s+r)?"
    r"|f\s*:\s*r\s*->\s*r"
    r"|(?:the\s+)?domain(?:\s+of\s+f)?\s+is\s+r"
    r"|f\s+(?:maps|is\s+defined\s+on)\s+r(?:\s+to\s+r)?"
    r")\s*\.?"
)


def _missing_hypothesis_counterexample(claim: dict) -> list[dict]:
    """The continuous-max claim as stated omits compactness: the identity
    function f(x) = x is continuous on R and attains no maximum, because it
    is strictly increasing (so f(x+1) > f(x) for EVERY candidate maximum x —
    the deterministic check above proves this universally by exact
    polynomial algebra). The record exposes the missing hypothesis and
    proposes the repaired claim; per the cycle-1 discipline an undetermined
    hypothesis ledger never earns a refutation, and exposing a missing
    hypothesis never refutes the REPAIRED claim, so refutes_claim stays
    False here regardless of admissibility."""

    hypotheses = claim.get("hypotheses") or []
    names = [
        h.get("name", "") if isinstance(h, dict) else str(h) for h in hypotheses
    ]
    if any(not _TRIVIAL_CONTINUITY_HYPOTHESIS.fullmatch(_normalize(n)) for n in names):
        # a non-trivial hypothesis (e.g. "f is bounded") might exclude the
        # identity witness; fail closed entirely (cycle-1 discipline).
        return []
    verdict = _verify_strictly_increasing_linear([0, 1])
    if not verdict["is_strictly_increasing"]:
        return []  # fail closed: a non-increasing witness earns nothing.
    return [
        {
            "id": "ce_probe_identity_no_maximum",
            "operator_id": "deterministic_missing_hypothesis_probe",
            "example_id": "identity_function_on_R",
            "satisfies_all_claim_hypotheses": True if hypotheses else "unclear",
            "target": "theorem",
            "result": "failed",
            "refutes_claim": False,
            "refutes_step_only": False,
            "exposes_missing_hypothesis": True,
            "missing_hypothesis": (
                "the domain must be compact (e.g. a closed bounded interval "
                "[a, b]) — on all of R the extreme value theorem does not apply"
            ),
            "support": (
                f"machine check (strict increase by exact polynomial algebra): "
                f"{verdict['detail']}"
            )[:300],
            "explanation": (
                "f(x) = x is continuous on R and attains no maximum: it is "
                "strictly increasing (f(x+1) - f(x) = 1 > 0 for every real x, "
                "an exact algebraic identity), so any candidate maximum point "
                "x0 is beaten by x0 + 1. This exposes the missing compactness "
                "hypothesis; it does not refute the repaired claim below."
            ),
            "repaired_claim_suggestion": (
                "Every continuous function f: [a, b] -> R on a closed bounded "
                "interval attains a maximum value (extreme value theorem)."
            ),
        }
    ]


def propose_deterministic_counterexamples(claim: dict) -> list[dict]:
    """Machine-verified counterexample tests for the locked claim, or []."""

    normalized = _normalize(claim.get("normalized_statement", ""))
    if _CONTINUOUS_MAX_EXACT.fullmatch(normalized):
        return _missing_hypothesis_counterexample(claim)
    match = _ORDER_N_ABELIAN_EXACT.fullmatch(normalized)
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
