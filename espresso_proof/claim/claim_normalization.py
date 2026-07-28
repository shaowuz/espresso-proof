"""Conservative claim normalization for simple, recognized elementary
theorems.

The claim-understanding layer (especially in stub/fast mode or with a weak model
reply) can misread a standard, precise statement — e.g. "a group of prime order
is cyclic" — as "unclear", which then correctly trips the claim-clear gate and
blocks all downstream stages. This helper recognizes a small, tightly-gated set
of elementary phrasings and proposes a PRECISE locked claim for them.

Hard safety contract:

- This NEVER upgrades proof / source / evidence / refutation / checked status,
  and never sets ``human_confirmed``. It only proposes a precise LOCKED CLAIM
  (an agent proposal) so downstream strategy/evidence/prover stages can run.
- It is intentionally NARROW: a claim is recognized only when it clearly mentions
  a group, an order (symbolic prime ``p``, "prime order", or a concrete integer),
  and the conclusion ``cyclic``. Anything else returns ``None`` and the existing
  imprecise handling stands (vague claims stay vague).
- "precise" does NOT mean "true": a recognized claim is locked as precise even if
  it is false (e.g. a composite concrete order); truth is decided downstream.
"""

from __future__ import annotations

import re

# Command wrappers the user puts around a proposition ("prove that X" -> "X").
_WRAPPER_RE = re.compile(
    r"^\s*(?:please\s+)?(?:can\s+you\s+)?(?:prove|show|verify|establish|is\s+it\s+true)"
    r"\s+(?:to\s+me\s+)?(?:that\s+)?",
    re.IGNORECASE,
)


def strip_command_wrapper(text: str) -> str:
    """Remove leading proof-command wrappers and trailing punctuation, returning
    the bare mathematical proposition. Idempotent over repeated wrappers."""
    s = (text or "").strip()
    prev = None
    while prev != s:
        prev = s
        s = _WRAPPER_RE.sub("", s, count=1).strip()
    return s.rstrip(" .?!").strip()


_CONCRETE_ORDER_RE = re.compile(r"\bgroup\s+of\s+order\s+(\d+)\b", re.IGNORECASE)
_SYMBOLIC_ORDER_P_RE = re.compile(r"\bgroup\s+of\s+order\s+p\b", re.IGNORECASE)
_PRIME_MENTION_RE = re.compile(r"\bprime\b", re.IGNORECASE)
_PRIME_ORDER_RE = re.compile(r"\bgroup\s+of\s+prime\s+order\b", re.IGNORECASE)


def normalize_simple_claim(raw_text: str) -> dict | None:
    """Return a precise normalized claim dict for a recognized elementary
    group-order-cyclic statement, or ``None`` if not confidently recognized.

    Recognized shapes (after stripping a command wrapper):
    - "a group of order p is cyclic" + a prime mention for ``p``  (symbolic)
    - "every group of prime order is cyclic"                       (symbolic)
    - "a group of order <N> is cyclic"                             (concrete N)
    """
    prop = strip_command_wrapper(raw_text)
    low = prop.lower()
    # Tight gate: must be about a group and the conclusion 'cyclic'.
    if "group" not in low or "cyclic" not in low:
        return None

    concrete = _CONCRETE_ORDER_RE.search(low)
    symbolic_p = bool(_SYMBOLIC_ORDER_P_RE.search(low)) and bool(_PRIME_MENTION_RE.search(low))
    prime_order = bool(_PRIME_ORDER_RE.search(low))

    if concrete:
        n = concrete.group(1)
        objects = ["G: group"]
        hypotheses = [
            {"name": "G is a group", "status": "explicit"},
            {"name": f"|G| = {n}", "status": "explicit"},
        ]
        normalized = f"Let G be a group. If |G| = {n}, then G is cyclic."
    elif symbolic_p or prime_order:
        objects = ["G: group", "p: prime number"]
        hypotheses = [
            {"name": "G is a group", "status": "explicit"},
            {"name": "|G| = p", "status": "explicit"},
            {"name": "p is prime", "status": "explicit"},
        ]
        normalized = "Let G be a group and p a prime. If |G| = p, then G is cyclic."
    else:
        return None

    return {
        "recognized": "prime_order_group_cyclic",
        "normalized_statement": normalized,
        "objects": objects,
        "hypotheses": hypotheses,
        "conclusion": "G is cyclic.",
        "claim_precision": "precise",
        "missing_or_unclear": [],
    }


__all__ = ["strip_command_wrapper", "normalize_simple_claim"]
