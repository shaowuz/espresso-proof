"""Ambient object-type hypothesis normalization (V0).

A front-half agent sometimes decomposes an ambient object-type hypothesis such
as "G is a group" into the individual group axioms (closure, associativity,
identity, inverses). Source grounding then fails closed because a cited theorem
writes "Let G be a group" rather than listing every axiom.

This module collapses a FULLY-expanded group-axiom set back into the single
ambient hypothesis "G is a group", so conservative semantic matching against
"Let G be a group" can succeed. It is deliberately narrow:

* it collapses ONLY when ALL FOUR group axioms are present;
* it NEVER promotes a weaker or different structure to a group — monoid,
  semigroup, magma, quasigroup, loop have fewer axioms; ring, field, module,
  algebra, vector space carry an extra operation. If any such structure appears
  the hypotheses are left untouched;
* it is idempotent and changes no proof/source/evidence status. It only renames
  / consolidates a hypothesis. All source-backed hard gates (exact locator,
  attribution, hypothesis match, theorem applicability) are unchanged.
"""

from __future__ import annotations

import re

# The four defining axioms of a group. Normalization collapses an expansion
# into "G is a group" ONLY when all four are present.
GROUP_AXIOM_PATTERNS: dict[str, str] = {
    "closure": r"\bclos(?:ure|ed)\b",
    "associativity": r"\bassociativ\w*\b",
    "identity": r"\bidentit\w*\b",
    "inverses": r"\binverse\w*\b",
}

AMBIENT_GROUP_HYPOTHESIS = "G is a group"

# A richer / weaker / different algebraic structure must NEVER be collapsed to a
# group. If any of these words appear in a hypothesis we refuse to collapse, so
# e.g. "R is a ring (closure, associativity, identity, inverses, distributivity)"
# stays untouched and a monoid is never treated as a group.
_OTHER_STRUCTURE = re.compile(
    r"\b(?:monoid|semigroup|magma|quasigroup|groupoid|loop|ring|field|module|"
    r"algebra|vector\s+space|lattice|distributiv\w*|multiplication|bilinear)\b",
    re.IGNORECASE,
)

# Words allowed (besides the axiom words) in a SEPARATE "pure group-axiom"
# hypothesis name when collapsing several axiom hypotheses into one. Anything
# outside this set means the hypothesis carries other mathematical content and
# must not be swallowed.
_AXIOM_CONTEXT_WORDS = {
    "g", "the", "a", "an", "is", "are", "has", "have", "with", "of", "for",
    "every", "each", "element", "elements", "exists", "exist", "operation",
    "binary", "law", "under", "and", "set", "satisfies", "satisfy", "holds",
    "hold", "must", "all", "that", "this", "property", "properties", "axiom",
    "axioms", "group", "closed", "closure", "associative", "associativity",
    "identity", "inverse", "inverses",
}

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z'-]*")


def _axioms_in(text: str) -> set[str]:
    return {name for name, pat in GROUP_AXIOM_PATTERNS.items() if re.search(pat, text, re.IGNORECASE)}


def _is_pure_axiom_hypothesis(name: str) -> bool:
    """True if the hypothesis is *only* about group axioms (no other content)."""

    if not _axioms_in(name) or _OTHER_STRUCTURE.search(name):
        return False
    return all(word.lower() in _AXIOM_CONTEXT_WORDS for word in _WORD_RE.findall(name))


def _consolidated_status(statuses: list) -> str:
    # Conservative: never invents "missing"; an unknown mix degrades to "unclear".
    if "explicit" in statuses:
        return "explicit"
    if "inferred" in statuses:
        return "inferred"
    return "unclear"


def _strip_axiom_parenthetical(name: str) -> tuple[str, bool]:
    """Remove a parenthetical that is purely the group-axiom enumeration.

    Returns ``(new_name, stripped)``. A qualifier such as "finite" / "p-group"
    that lives OUTSIDE the parenthetical is preserved, so
    "G is a finite group (closure, associativity, identity, inverses)" becomes
    "G is a finite group" — NOT a bare "G is a group". Only strips when the
    parentheticals together contain all four axioms and nothing but axiom /
    context words.
    """

    parentheticals = re.findall(r"\(([^()]*)\)", name)
    if not parentheticals:
        return name, False
    inner = " ".join(parentheticals)
    if _axioms_in(inner) != set(GROUP_AXIOM_PATTERNS):
        return name, False
    if not all(word.lower() in _AXIOM_CONTEXT_WORDS for word in _WORD_RE.findall(inner)):
        return name, False
    stripped = re.sub(r"\s*\([^()]*\)", "", name)
    stripped = re.sub(r"\s+", " ", stripped).strip().strip(",;:").strip()
    return stripped, bool(stripped)


def normalize_ambient_object_hypotheses(hypotheses):
    """Collapse a fully-expanded group-axiom set into "G is a group".

    Conservative and idempotent. Two recognised shapes:

    * Case A — a SINGLE hypothesis that already names all four group axioms
      (e.g. "G is a group (closure, associativity, identity, inverses)") is
      renamed to "G is a group".
    * Case B — SEPARATE pure-axiom hypotheses whose union covers all four axioms
      are consolidated into one "G is a group" (only when no other/ richer
      structure appears anywhere in the list).

    Returns the (possibly new) list; the input is never mutated. Non-list input
    is returned unchanged.
    """

    if not isinstance(hypotheses, list):
        return hypotheses

    all_axioms = set(GROUP_AXIOM_PATTERNS)

    # --- Case A: a single hypothesis lists ALL FOUR group axioms ---
    out: list = []
    case_a_changed = False
    for item in hypotheses:
        if not isinstance(item, dict):
            out.append(item)
            continue
        name = str(item.get("name", ""))
        if _axioms_in(name) != all_axioms or _OTHER_STRUCTURE.search(name):
            out.append(item)
            continue
        # All four axioms present and no other/ richer structure named.
        # Prefer stripping just the axiom parenthetical so a qualifier outside it
        # (finite / abelian / p-group / simple ...) is preserved.
        stripped, did_strip = _strip_axiom_parenthetical(name)
        if did_strip:
            new_item = dict(item)
            new_item["name"] = stripped
            out.append(new_item)
            case_a_changed = True
        elif _is_pure_axiom_hypothesis(name):
            # The whole hypothesis is nothing but the four axioms -> "G is a group".
            new_item = dict(item)
            new_item["name"] = AMBIENT_GROUP_HYPOTHESIS
            out.append(new_item)
            case_a_changed = True
        else:
            # Axioms appear alongside other (non-parenthetical) math content; do
            # NOT drop that content. Leave the hypothesis untouched.
            out.append(item)

    if case_a_changed:
        return out

    # --- Case B: SEPARATE pure-axiom hypotheses together cover all four ---
    combined_text = " ".join(
        str(h.get("name", "")) for h in out if isinstance(h, dict)
    )
    if _OTHER_STRUCTURE.search(combined_text):
        return out

    pure_indices = [
        index
        for index, h in enumerate(out)
        if isinstance(h, dict) and _is_pure_axiom_hypothesis(str(h.get("name", "")))
    ]
    union: set[str] = set()
    for index in pure_indices:
        union |= _axioms_in(str(out[index].get("name", "")))

    if union != all_axioms or len(pure_indices) < 2:
        return out

    pure_set = set(pure_indices)
    status = _consolidated_status([out[i].get("status") for i in pure_indices])
    source = out[pure_indices[0]].get("source", "agent")
    rebuilt: list = []
    inserted = False
    for index, h in enumerate(out):
        if index in pure_set:
            if not inserted:
                rebuilt.append(
                    {"name": AMBIENT_GROUP_HYPOTHESIS, "status": status, "source": source}
                )
                inserted = True
        else:
            rebuilt.append(h)
    return rebuilt
