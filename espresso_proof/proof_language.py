"""Single display vocabulary layer for proof-language safety (Goal 20).

Espresso Proof may show heuristic critique, a reference-supported step, or a
checked computation with explicit scope -- but user-visible output must NEVER
read as if Espresso established the whole theorem ("proof-close", "proof complete",
"Espresso proved the theorem", "verified theorem", ...). Model free text and
echoed artifact fields can contain such language, so every user-facing renderer
sanitizes its output through ``scrub_proof_language`` here.

This is a DISPLAY-LAYER sanitizer (defense in depth): it redacts global
proof-completion overclaims from rendered text. It is intentionally curated to
match only UNAMBIGUOUS global completion language -- never bare "proof" /
"proved" / "verified", which appear in legitimate scoped or negated phrasing
("not a checked proof", "the source proves Lemma 2.1", "needs a checked
proof"). It does not weaken, and is independent of, the parse-time validator
proof-language gate (validators._first_forbidden_proof_language) or the
agent-ingestion redaction (llm_agents.FORBIDDEN_LANGUAGE), which keep their own
tested behavior.
"""

from __future__ import annotations

import re

# Global "the whole thing is proved / proved by Espresso" overclaims, curated so
# legitimate mathematician prose is PRESERVED. Two safe families only:
#
#   1. Self-contained completion markers that negation/scope do not collide with
#      ("not complete" / "is not complete" break the adjacency, so the assertion
#      form is the only thing that matches): proof-close, "proof complete",
#      "the proof is complete", "this completes the proof", "no gaps remain".
#   2. FIRST-PERSON / Espresso global assertions, as whole phrases requiring the
#      subject+verb+global-object together ("we proved the theorem", "espresso
#      proved the claim", ...). The full phrase means a honest negation
#      ("we have NOT proved the theorem") never matches (the adjacency is broken),
#      and a scoped statement ("we proved Lemma 2.1", "we proved a weaker result")
#      is preserved (the object is not "the theorem"/"the claim").
#
# Deliberately EXCLUDED (they collide with honest scoped/bibliographic/negated
# prose, which the product must keep readable): bare "proved"/"proven"/
# "verified"; third-person "proves the theorem" / "establishes the theorem"
# (scoped citations: "the source proves the theorem in the compact case"); and
# "the theorem is established" ("no single step establishes the theorem"). Those
# remain covered, where appropriate, by the unchanged parse-time validator gate
# and the agent-ingestion redaction.
_SUBJECTS = ("we", "we have", "espresso", "espresso has")
_VERBS = ("proved", "proven")
_OBJECTS = ("the theorem", "the claim")
_FIRST_PERSON_ASSERTIONS = tuple(
    f"{s} {v} {o}" for s in _SUBJECTS for v in _VERBS for o in _OBJECTS
)

# Chinese global proof-completion phrases (Goal 36), curated by the SAME two
# safe families. Self-contained completion markers: 证毕 (= proof-close), 证明完毕 /
# 证明完成 (proof finished). Global assertions: 定理/命题/猜想 + 得证/已证明,
# and first-person 我们证明了 + a definite global object. Because the boundary
# lookarounds only block ASCII word characters, a phrase also matches inside
# prefixed forms ("该定理得证", "此定理已证明"). Deliberately EXCLUDED, exactly
# like bare "proved" / third-person citations in English: bare 已证明 / 得证 /
# 证明了 (collide with honest SCOPED prose: 该引理已证明, 引理2.1得证,
# 我们证明了引理2.1); the literature passive 已被证明 (a citation — 庞加莱猜想
# 已被证明 — like "the source proves the theorem"); and subject-less 证明了该
# 定理 (文献[3]证明了该定理的一个弱形式 is an honest scoped citation).
_CHINESE_COMPLETION_MARKERS = ("证毕", "证明完毕", "证明完成")
_CHINESE_GLOBAL_OBJECTS = ("定理", "命题", "猜想")
_CHINESE_GLOBAL_ASSERTIONS = tuple(
    f"{o}{suffix}" for o in _CHINESE_GLOBAL_OBJECTS for suffix in ("得证", "已证明")
) + tuple(
    f"我们证明了{d}{o}" for d in ("该", "这个") for o in _CHINESE_GLOBAL_OBJECTS
)

PROOF_COMPLETE_PHRASES: tuple[str, ...] = (
    "[proof-close]",
    "[proof-close]",
    "proof-close",
    "proof complete",
    "proof is complete",
    "the proof is complete",
    "this completes the proof",
    "no gaps remain",
    *_FIRST_PERSON_ASSERTIONS,
    *_CHINESE_COMPLETION_MARKERS,
    *_CHINESE_GLOBAL_ASSERTIONS,
)

# Neutral redaction marker (reuses the existing convention in llm_agents).
REDACTION = "[withheld proof-like phrase]"

# Both-sided word boundaries: a phrase must not match inside a longer word in
# either direction ("proof completeness", "oedipus", "verified theorems" are NOT
# matched). Longest phrases first so the whole assertion is replaced as a unit
# (no dangling "the theorem" left behind).
_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    + "|".join(re.escape(p) for p in sorted(PROOF_COMPLETE_PHRASES, key=len, reverse=True))
    + r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def contains_proof_language(text: object) -> str | None:
    """Return the first global proof-complete phrase in ``text``, or None."""
    if not text:
        return None
    match = _PATTERN.search(str(text))
    return match.group(0) if match else None


def scrub_proof_language(text: object) -> str:
    """Redact global proof-complete overclaims from user-visible text.

    Returns the input unchanged except that any global proof-completion phrase is
    replaced with a neutral marker. Read-only/pure; never raises on normal text.
    """
    if text is None:
        return ""
    return _PATTERN.sub(REDACTION, str(text))


__all__ = [
    "PROOF_COMPLETE_PHRASES",
    "REDACTION",
    "contains_proof_language",
    "scrub_proof_language",
]
