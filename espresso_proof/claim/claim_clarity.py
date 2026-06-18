"""Goal 67A: shared claim-clarity rules — the single source of truth for what
counts as a BLOCKING missing/unclear item vs an advisory clarification note.

The claim-clear gate must block genuine gaps (missing object / hypothesis /
conclusion) but must NOT block a precise claim on a pedantic clarification note.
In external mode the model marks a claim ``precise`` yet adds a note like
"whether the quantifier is explicitly universal", which previously blocked the
claim before any proof search.

Design = DEFAULT-DENY: a non-empty ``missing_or_unclear`` entry BLOCKS unless it
is conservatively recognized as advisory, and it is NEVER advisory if it names a
core structural gap. Both the claim-clear gate (``claim_clear._blocking_items``)
and the front-half precision floor (``understanding._looks_imprecise``) use the
``CORE_GAP_TERMS`` below so the two places can never disagree.
"""

from __future__ import annotations

import re

# Real structural gaps. A missing item naming one of these ALWAYS blocks (and can
# never be reclassified as advisory). Single source of truth.
CORE_GAP_TERMS = frozenset(
    {
        "objects",
        "main objects",
        "object",
        "hypotheses",
        "hypothesis",
        "conclusion",
        "claim",
        "statement",
    }
)

# A hedging lead that introduces a clarification rather than asserting a gap.
_ADVISORY_LEAD_RE = re.compile(
    r"^\s*(?:whether|note that|optionally|for clarity|if desired)\b", re.IGNORECASE
)

# Clarification vocabulary (asking to make an already-stated thing explicit / pin
# a convention), as opposed to flagging something missing.
_ADVISORY_PHRASE_RE = re.compile(
    r"\b(?:explicit|explicitly|strict literal|literal parsing|convention|notation|"
    r"intended to (?:range|mean|denote)|could (?:also )?be (?:stated|written|phrased))\b",
    re.IGNORECASE,
)


def _names_core_gap(text: str) -> bool:
    low = (text or "").lower()
    return any(re.search(r"\b" + re.escape(term) + r"\b", low) for term in CORE_GAP_TERMS)


def is_advisory_missing_item(text: str) -> bool:
    """True iff a ``missing_or_unclear`` entry is a conservative advisory
    clarification (NOT a real gap). Default-deny: it is advisory ONLY when it both
    opens with a hedging lead AND uses clarification vocabulary, and it names no
    core gap. Anything else is treated as blocking."""
    s = (text or "").strip()
    if not s or _names_core_gap(s):
        return False
    return bool(_ADVISORY_LEAD_RE.search(s)) and bool(_ADVISORY_PHRASE_RE.search(s))


def is_blocking_missing_item(text: str) -> bool:
    """DEFAULT-DENY: a non-empty missing item BLOCKS the claim-clear gate unless it
    is recognized as advisory. Empty entries are ignored (not blocking)."""
    s = (text or "").strip()
    if not s:
        return False
    return not is_advisory_missing_item(s)


__all__ = [
    "CORE_GAP_TERMS",
    "is_advisory_missing_item",
    "is_blocking_missing_item",
]
