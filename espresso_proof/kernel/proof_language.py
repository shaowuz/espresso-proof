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


def scrub_source_support_language(text: object) -> str:
    """Translate raw source-support enum vocabulary in display text.

    Internal artifacts may store values like ``source_backed``. User-facing
    surfaces should use plain scoped wording, so free text and rendered labels
    pass through this display-only helper.
    """

    return re.sub(
        r"\bsource(?:[_ -])backed(?:[_ -]status)?\b",
        "reference-supported",
        scrub_proof_language(text),
        flags=re.IGNORECASE,
    )


# Goal 65E: bare earned-status VERDICTS an advisory instant draft must never
# assert. scrub_proof_language only blocks proof-COMPLETE overclaims and lets
# "checked"/"verified"/"refuted" through, so the instant lane needs this extra
# filter. It targets verdict FORMS only ("source-backed", "refuted", and
# "is/are/was/were/been/now <checked|verified|...>") so advisory phrasing like
# "needs to be checked" / "to be verified" is NOT matched.
_DRAFT_STATUS_CLAIM_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"source[ _-]?backed"
    r"|refuted"
    r"|(?:is|are|was|were|been|now)\s+(?:checked|verified|proven|proved|confirmed|established|grounded)"
    r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def contains_draft_status_claim(text: object) -> str | None:
    """Return the first earned-status VERDICT phrase in ``text``, or None.

    Used by the instant conversation lane (Goal 65E): an advisory pre-pipeline
    draft references no validated artifact, so it must not assert source-backed /
    refuted / checked-style verdicts. Advisory phrasing ("needs a check", "to be
    verified") is deliberately not matched."""
    if not text:
        return None
    match = _DRAFT_STATUS_CLAIM_PATTERN.search(str(text))
    return match.group(0) if match else None


def scrub_draft_status_claims(text: object) -> str:
    """Redact earned-status verdict forms from advisory instant-draft text
    (Goal 65E). Verdict phrasing like "is checked" / "refuted" / "source-backed"
    becomes a neutral '[unverified]' marker; advisory phrasing is left intact.
    Pure/never raises."""
    if text is None:
        return ""
    return _DRAFT_STATUS_CLAIM_PATTERN.sub("[unverified]", str(text))


__all__ = [
    "PROOF_COMPLETE_PHRASES",
    "REDACTION",
    "contains_proof_language",
    "contains_draft_status_claim",
    "scrub_proof_language",
    "scrub_source_support_language",
    "scrub_draft_status_claims",
]


# --------------------------------------------------------------------------- #
# Agent-output sanitization (moved here from agents.llm_agents: this file is
# the language system's single home, and strategy/front_half/literature must
# be able to sanitize agent text without importing the agents package).
# --------------------------------------------------------------------------- #

# A superset of the proof-complete phrases the validators forbid at parse time
# (test_forbidden_language_redaction_covers_validator pins the invariant).
FORBIDDEN_LANGUAGE = [
    "proved",
    "proven",
    "proves",
    "verified",
    "certainly",
    "obviously",
    "clearly",
    "complete proof",
    "proof complete",
    "this proof",
    "full proof",
    "proof establishes",
    "establishes the theorem",
    "establishes the claim",
    "no gaps remain",
    "the claim is true",
    "the theorem is established",
    "proof-close",
    # Goal 41: Chinese global completion markers (FORBIDDEN_LANGUAGE must stay
    # a superset of the validator's parse-time gate; the ASCII lookarounds in
    # forbidden_pattern() are satisfied around CJK characters).
    "证毕",
    "证明完毕",
    "证明完成",
    "定理得证",
    "命题得证",
    "猜想得证",
    "定理已证明",
    "命题已证明",
    "我们证明了该定理",
    "我们证明了这个定理",
]
FORBIDDEN_LANGUAGE = list(dict.fromkeys([*FORBIDDEN_LANGUAGE, *PROOF_COMPLETE_PHRASES]))

RESERVED_STATUS_LANGUAGE = [
    "proof_status",
    "proof-language status",
    "proof language status",
    "source_status",
    "source status",
    "source_backed_status",
    "source-backed status",
    "source backed status",
    "source_backed",
    "source-backed",
    "source backed",
    "checked_evidence",
    "checked evidence",
    "evidence_status",
    "evidence status",
    "refutation_status",
    "refutation status",
    "grade_status",
    "grade status",
    "agent_consensus_mock_checked",
    "agent consensus mock checked",
    "mock_checked",
    "mock checked",
]
RESERVED_STATUS_LANGUAGE = list(dict.fromkeys(RESERVED_STATUS_LANGUAGE))


def forbidden_pattern(phrase: str) -> str:
    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    return rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])"


# The one sanctioned home for this phrase is a caller that explicitly opts in
# (a prover attempt's output_summary); safe_text itself never manufactures it.
NO_AGENT_TEXT = "No usable agent text returned."
_SAFE_TEXT_MAX = 1200
_TRUNCATION_MARKER = " …[truncated]"


def safe_text(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    for phrase in sorted(RESERVED_STATUS_LANGUAGE, key=len, reverse=True):
        text = re.sub(
            forbidden_pattern(phrase),
            "[withheld status phrase]",
            text,
            flags=re.IGNORECASE,
        )
    for phrase in sorted(FORBIDDEN_LANGUAGE, key=len, reverse=True):
        text = re.sub(
            forbidden_pattern(phrase),
            "[withheld proof-like phrase]",
            text,
            flags=re.IGNORECASE,
        )
    # Empty in, empty out: callers own their fallbacks (`... or None` etc.).
    # Manufacturing placeholder prose here defeated every such fallback and
    # leaked "No usable agent text returned." into optional artifact fields.
    # Truncation is visible, never a silent mid-sentence cliff; total length
    # stays <= _SAFE_TEXT_MAX.
    if len(text) > _SAFE_TEXT_MAX:
        text = text[: _SAFE_TEXT_MAX - len(_TRUNCATION_MARKER)].rstrip() + _TRUNCATION_MARKER
    return text


def safe_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [safe_text(item) for item in value if str(item).strip()][:12]
