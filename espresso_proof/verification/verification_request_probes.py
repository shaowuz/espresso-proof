"""Deterministic scoped-verification request probes (cycle 2).

The proof-search loop stalls on dogfood cases because no machine evidence
ever enters a smoke run: reviewer requests like "source or exact computation"
recur forever. This module is the first DETERMINISTIC producer of the
existing ``scoped_verification_requests.yaml`` artifact: when the locked
claim matches an anchored family of equivalent forms of "every group of
order p^2, where p is prime, is abelian" (the symbolic universal claim; the
primality clause is mandatory), it proposes commutativity AND associativity
checks on the Cayley tables of the known groups of order 4 and 9 (p=2, 3),
so both the abelian property and the core group-law evidence are
machine-verified rather than constructor-trusted.

Trust model (mirrors the cycle-1 counterexample probe):

- A request is ONLY a proposal. It carries no status; the existing
  ``auto_wire_scoped_verifications`` path runs it through the trusted
  deterministic backend, and only a confirming result earns
  ``instance_checked`` — scoped to THIS table, never the universal claim.
- The statement match is an ANCHORED FULLMATCH of the whole normalized
  statement with no free modifiers, so modified or unrelated claims get no
  requests. The ``conclusion`` field is never concatenated into the search
  text.
- That C_{p^2} and C_p x C_p are ALL groups of order p^2 is the
  classification theorem — stated in every request target as NOT
  machine-verified here. The instance checks can therefore never be read as
  a proof of the universal claim; there is no "proved" status.
"""

from __future__ import annotations

import re

from espresso_proof.verification.finite_group_verifier import all_groups_of_order_p_squared

# Anchored claim form: the WHOLE normalized statement must be exactly
# "every/all/each (finite) group(s) of order p^2, where/for/with p (is) (a)
# prime, is/are abelian." — no free modifiers anywhere. The primality clause
# is MANDATORY (Kernel Safety review): without it the statement read
# literally (arbitrary p) is a DIFFERENT, false claim — non-abelian groups of
# order 36 = 6^2 exist — and these instances must not decorate it.
_ORDER_P_SQUARED_ABELIAN_EXACT = re.compile(
    r"(?:every|all|each)\s+(?:finite\s+)?groups?\s+of\s+order\s+p(?:\^2|²)\s*"
    r",?\s*(?:where|for|with)\s+p\s+(?:is\s+)?(?:a\s+)?prime\s*,?\s*"
    r"(?:is|are)\s+abelian\s*\.?",
    re.IGNORECASE,
)

_INSTANCE_PRIMES = (2, 3)  # orders 4 and 9 — within the auto-wire table cap


def _normalize(text: str) -> str:
    return " ".join(str(text).split()).strip().lower()


def propose_scoped_verification_requests(claim: dict) -> list[dict]:
    """Concrete finite-identity-table requests for the locked claim, or []."""

    statement = _normalize(claim.get("normalized_statement", ""))
    if not _ORDER_P_SQUARED_ABELIAN_EXACT.fullmatch(statement):
        return []
    requests: list[dict] = []
    for p in _INSTANCE_PRIMES:
        order = p * p
        for index, (name, table) in enumerate(
            all_groups_of_order_p_squared(p), start=1
        ):
            # Machine-verify associativity too, so the
            # instance evidence does not silently trust the table constructor
            # for the group law (identity/inverses remain constructor-level).
            for identity in ("commutative", "associative"):
                requests.append(
                    {
                        "id": f"req_fg_p{p}_g{index}_{identity}",
                        "kind": "finite_identity_table",
                        "identity": identity,
                        "table": table,
                        "target": (
                            f"{identity} law of {name} (order {order}): the "
                            f"p={p} instance of the locked universal claim. "
                            f"That these are ALL groups of order {order} is "
                            "the classification theorem, not machine-verified "
                            "here; a confirming check covers only this "
                            "explicit table."
                        ),
                    }
                )
    return requests


__all__ = ["propose_scoped_verification_requests"]
