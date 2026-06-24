"""Conservative semantic hypothesis matching for source grounding.

This module lets mathematically equivalent *notations* for the same hypothesis
match during source grounding, WITHOUT loosening any safety gate. It only
recognises explicit, test-backed equivalent phrasings (for example
``"group of order p^2"`` and ``"|G| = p^2"``).

It never:

* earns ``source_backed`` on its own — the :class:`SourceRef` model validator
  still requires an exact locator, attribution, and ``matches is True``; a
  semantic match only contributes to the hypothesis-compatibility gate;
* equates a property with a weaker, stronger, or opposite property (finite vs
  infinite, smooth vs singular, characteristic zero vs arbitrary characteristic,
  very general vs all, generic vs arbitrary, algebraically closed vs arbitrary
  field);
* guesses. If no explicit rule fires it returns ``None`` and the caller falls
  back to conservative whole-word token coverage, leaving the step a candidate
  that "Needs a check".

Each rule maps ONE mathematical concept to the claim-side and source-side
surface forms that are genuinely the same statement. Patterns use word
boundaries and exact notation so an opposite or weaker phrasing cannot fire.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class EquivalenceRule:
    """One explicit, conservative notational-equivalence rule.

    ``claim_patterns`` recognise how a claim hypothesis may be phrased;
    ``source_patterns`` recognise an equivalent phrasing in the cited source
    statement. A rule fires only when both sides are recognised.
    """

    concept: str
    claim_patterns: tuple[str, ...]
    source_patterns: tuple[str, ...]


# IMPORTANT: keep these conservative. Add a rule only when the two surface forms
# denote the *same* hypothesis, and always cover the negative direction with a
# test (an opposite/weaker phrasing must NOT match). Patterns are matched
# case-insensitively against whitespace-normalised text.
SEMANTIC_EQUIVALENCE_RULES: tuple[EquivalenceRule, ...] = (
    EquivalenceRule(
        concept="order p^2",
        claim_patterns=(
            r"group of order\s+p\s*\^?\s*2\b",
            r"\border\s+p\s*\^?\s*2\b",
            r"\|\s*g\s*\|\s*=\s*p\s*\^?\s*2\b",
        ),
        source_patterns=(
            r"\|\s*g\s*\|\s*=\s*p\s*\^?\s*2\b",
            r"group of order\s+p\s*\^?\s*2\b",
            r"\border\s+p\s*\^?\s*2\b",
        ),
    ),
    EquivalenceRule(
        concept="p is prime",
        claim_patterns=(
            r"\bp\s+is\s+(?:a\s+)?prime\b",
            r"\bprime\s+p\b",
            r"\bp\s+(?:a\s+)?prime\s+number\b",
        ),
        source_patterns=(
            r"\bprime\s+p\b",
            r"\bp\s+is\s+(?:a\s+)?prime\b",
            r"\bp\s+(?:a\s+)?prime\s+number\b",
            r"\bp\s*\\?\)?\s+(?:a\s+)?prime\s+number\b",
        ),
    ),
    EquivalenceRule(
        concept="prime order",
        claim_patterns=(
            r"\|\s*g\s*\|\s*=\s*p\b.*\bprime\b",
            r"\bprime\s+order\b",
            r"\border\s+p\b.*\bprime\b",
        ),
        source_patterns=(
            r"\|\s*g\s*\|\s*=\s*p\b.*\bprime\b",
            r"\bprime\s+order\b",
            r"\border\s+p\b.*\bprime\b",
        ),
    ),
    EquivalenceRule(
        concept="center of G",
        claim_patterns=(
            r"\bcent(?:er|re)\s+of\s+g\b",
            r"\bz\s*\(\s*g\s*\)",
        ),
        source_patterns=(
            r"\bz\s*\(\s*g\s*\)",
            r"\bcent(?:er|re)\s+of\s+g\b",
        ),
    ),
    EquivalenceRule(
        concept="G/Z(G) is cyclic",
        claim_patterns=(
            r"\bg\s*/\s*z\s*\(\s*g\s*\)\s+is\s+cyclic\b",
            r"\bquotient\s+by\s+the\s+cent(?:er|re)\s+is\s+cyclic\b",
        ),
        source_patterns=(
            r"\bquotient\s+by\s+the\s+cent(?:er|re)\s+is\s+cyclic\b",
            r"\bg\s*/\s*z\s*\(\s*g\s*\)\s+is\s+cyclic\b",
        ),
    ),
    EquivalenceRule(
        # Ambient object-type: "G is a group" against the way a cited theorem
        # introduces the object. Word-boundary \bgroup\b never fires inside
        # "semigroup"/"subgroup", and monoid/ring/module/etc. lack the word, so
        # a weaker/different structure can never match here.
        concept="G is a group",
        claim_patterns=(
            r"\bg\s+is\s+(?:a\s+)?group\b",
            r"\bgroup\s+g\b",
        ),
        source_patterns=(
            r"\blet\s+g\s+be\s+a\s+group\b",
            r"\bg\s+is\s+(?:a\s+)?group\b",
            r"\bgroup\s+g\b",
            r"\bgroup\s+of\s+order\s+p\s*\^?\s*2\b",
            r"\bg\s+is\s+cyclic\b",
            r"\bg\s*\\?\)?\s+is\s+cyclic\b",
        ),
    ),
    EquivalenceRule(
        concept="center is nontrivial",
        claim_patterns=(
            r"\bcent(?:er|re)\s+is\s+non-?trivial\b",
            r"\bz\s*\(\s*g\s*\)\s*(?:!=|≠|\\neq|\\ne)\s*1\b",
        ),
        source_patterns=(
            r"\bz\s*\(\s*g\s*\)\s*(?:!=|≠|\\neq|\\ne)\s*1\b",
            r"\bcent(?:er|re)\s+is\s+non-?trivial\b",
            r"\bnon-?trivial\s+cent(?:er|re)\b",
        ),
    ),
)


def _normalize_spacing(text: str) -> str:
    """Collapse whitespace but preserve case so matched surfaces stay readable."""

    return re.sub(r"\s+", " ", text).strip()


def match_hypothesis_semantically(claim_phrase: str, source_statement: str) -> str | None:
    """Return a human-readable matched-pair reason, or ``None`` if no rule fires.

    Example return value::

        '"group of order p^2" matched "|G| = p^2"'

    Conservative by construction: only the explicit rules in
    :data:`SEMANTIC_EQUIVALENCE_RULES` can fire, each side is required, and word
    boundaries / exact notation keep opposite or weaker phrasings from matching.
    A returned reason contributes to the hypothesis-compatibility gate only; it
    can never by itself set ``source_backed`` (the model validator still demands
    an exact locator and attribution).
    """

    claim_norm = _normalize_spacing(claim_phrase)
    source_norm = _normalize_spacing(source_statement)
    if not claim_norm or not source_norm:
        return None
    for rule in SEMANTIC_EQUIVALENCE_RULES:
        if not any(re.search(pattern, claim_norm, re.IGNORECASE) for pattern in rule.claim_patterns):
            continue
        source_hit = None
        for pattern in rule.source_patterns:
            source_hit = re.search(pattern, source_norm, re.IGNORECASE)
            if source_hit is not None:
                break
        if source_hit is None:
            continue
        return f'"{claim_norm}" matched "{source_hit.group(0).strip()}"'
    return None
