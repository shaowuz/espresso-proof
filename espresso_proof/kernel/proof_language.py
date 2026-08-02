"""Single display vocabulary layer for proof-language safety.

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
import unicodedata

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
)

# Neutral redaction marker (reuses the existing convention in llm_agents).
REDACTION = "[withheld proof-like phrase]"

# Separators INSIDE an acronym: someone who types "Q. E. D." means exactly what
# "proof-close" means, and the periods/spaces carry no other reading.
_SEP = r"[\s._\-‐-―]*"

# Separators BETWEEN the words of a phrase: whitespace only. Hyphenating is not
# a spelling variant here, it changes the part of speech — "a sketch-only
# argument cannot become proof-complete evidence" is honest prose that names the
# thing it denies, and this module's rule is that legitimate prose stays
# readable. The hyphenated adjective remains covered by the agent-ingestion
# redactor, which is a separate list with separate rules.
_WORD_SEP = r"[\s\u00a0]+"

# The end-of-proof tombstone: a literal claim that the proof is finished, in the
# one notation everybody uses, which a list of English phrases could never catch.
# U+220E is unambiguous anywhere. The hollow square is only a tombstone in the
# position a tombstone occupies — ending a line — because "the square □ in the
# diagram" is ordinary geometry prose that must stay readable.
_TOMBSTONE_PATTERN = r"∎|□(?=[ \t]*$)"


def _phrase_regex(phrase: str) -> str:
    """Match one curated phrase tolerantly, without loosening what it means.

    A literal phrase list is a list of spellings, not of meanings: "Q. E. D.",
    "ＱＥＤ", "proof–complete" and "the proof is now complete" all assert the same
    thing and all walked straight through. Every separator inside a phrase now
    accepts the punctuation and spacing variants, an acronym accepts separators
    between its letters, and "is complete" tolerates one intensifier ("is now
    complete"). Nothing else is loosened: the words themselves, in order, are
    still required.
    """

    if phrase in {"[proof-close]", "[proof-close]", "proof-close"}:
        return _SEP.join(("q", "e", "d")) + r"\.?"
    words = phrase.replace(".", "").split()
    parts: list[str] = []
    for index, word in enumerate(words):
        if index:
            parts.append(_WORD_SEP)
            if word == "complete" and words[index - 1] == "is":
                parts.append(r"(?:(?:now|hereby|thus|therefore)" + _WORD_SEP + r")?")
        parts.append(re.escape(word))
    return "".join(parts)


# Both-sided word boundaries: a phrase must not match inside a longer word in
# either direction ("proof completeness", "oedipus", "verified theorems" are NOT
# matched). Longest phrases first so the whole assertion is replaced as a unit
# (no dangling "the theorem" left behind).
_PATTERN = re.compile(
    _TOMBSTONE_PATTERN
    + r"|(?<![A-Za-z0-9_])(?:"
    + "|".join(_phrase_regex(p) for p in sorted(PROOF_COMPLETE_PHRASES, key=len, reverse=True))
    + r")(?![A-Za-z0-9_])",
    re.IGNORECASE | re.MULTILINE,
)


def _folded_with_origin(text: str) -> tuple[str, list[int]]:
    """A compatibility-folded view of ``text`` plus each char's source index.

    Matching happens on the folded view so fullwidth, bold-math and ligature
    spellings ("ＱＥＤ", "𝐐𝐄𝐃") are caught; the replacement happens on the
    ORIGINAL, because folding the returned text would rewrite legitimate
    mathematics on every render (NFKC turns "p²" into "p2").
    """

    folded: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        expansion = unicodedata.normalize("NFKC", char) or char
        for piece in expansion:
            folded.append(piece)
            origin.append(index)
    return "".join(folded), origin


def _matches(text: str) -> list[tuple[int, int, str]]:
    """Spans in the ORIGINAL text that a curated phrase covers, left to right."""

    folded, origin = _folded_with_origin(text)
    spans: list[tuple[int, int, str]] = []
    for match in _PATTERN.finditer(folded):
        start, end = match.span()
        if start >= len(origin) or end == 0:
            continue
        spans.append((origin[start], origin[end - 1] + 1, match.group(0)))
    return spans


def contains_proof_language(text: object) -> str | None:
    """Return the first global proof-complete phrase in ``text``, or None."""
    if not text:
        return None
    source = str(text)
    spans = _matches(source)
    return source[spans[0][0] : spans[0][1]] if spans else None


def scrub_proof_language(text: object) -> str:
    """Redact global proof-complete overclaims from user-visible text.

    Returns the input unchanged except that any global proof-completion phrase is
    replaced with a neutral marker. Read-only/pure; never raises on normal text.
    """
    if text is None:
        return ""
    source = str(text)
    out: list[str] = []
    cursor = 0
    for start, end, _ in _matches(source):
        if start < cursor:  # overlapping match already covered
            continue
        out.append(source[cursor:start])
        out.append(REDACTION)
        cursor = end
    out.append(source[cursor:])
    return "".join(out)


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


# Bare earned-status VERDICTS an advisory instant draft must never
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

    Used by the instant conversation lane: an advisory pre-pipeline
    draft references no validated artifact, so it must not assert source-backed /
    refuted / checked-style verdicts. Advisory phrasing ("needs a check", "to be
    verified") is deliberately not matched."""
    if not text:
        return None
    match = _DRAFT_STATUS_CLAIM_PATTERN.search(str(text))
    return match.group(0) if match else None


def scrub_draft_status_claims(text: object) -> str:
    """Redact earned-status verdict forms from advisory instant-draft text.

    Verdict phrasing like "is checked" / "refuted" / "source-backed"
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


# A bare verb inside a CONDITION or a DENIAL is not a status claim. A live run
# recorded the honest gap "If proved, a foundational source ... would need to be
# located and verified" as "If [withheld proof-like phrase], ... located and
# [withheld proof-like phrase]" — the redaction destroyed the only sentence that
# said what was missing, while withholding nothing, because "if proved" and
# "would need to be verified" assert the opposite of a completed proof.
#
# Only these left contexts are exempt, and only for the bare single-word verbs.
# Every multi-word phrase ("we proved the theorem", "proof is complete") is
# still redacted wherever it appears: those cannot be turned into a denial by
# what precedes them.
_CONDITIONAL_VERBS = frozenset({"proved", "proven", "proves", "verified"})
_DENIAL_CONTEXT = re.compile(
    r"(?:\bif\b|\bnot\b|\bno\b|\bnever\b|\bunless\b|\bcannot\s+be\b|"
    r"\bmust\s+be\b|\bneed(?:s|ed)?\s+to\s+be\b|\bto\s+be\b|\bwould\s+be\b|"
    r"\byet\s+to\s+be\b|\bwhether\b|\bonce\b)"
    r"(?:\s+\w+){0,3}\s*$",
    re.IGNORECASE,
)


_BARE_PROOF_VERBS = re.compile(r"\b(?:proved|proven|proves|verified|refuted)\b", re.IGNORECASE)
_DENIED_MARKER = "\u00abdenied\u00bb"


def neutralize_denied_verbs(text: str) -> str:
    """Blank out bare proof verbs that a denial or condition governs.

    The same rule the redactor uses, exported so a guard downstream reaches the
    same verdict. Without it the two disagreed and a live run died: once "If
    proved, a foundational source ... would need to be located and verified"
    stopped being redacted, it reached the memory boundary as literal words, was
    read there as a proof claim, and rolled back an entire front half that had
    already been paid for.
    """

    def _sub(match: re.Match[str]) -> str:
        return _DENIED_MARKER if _DENIAL_CONTEXT.search(text[: match.start()]) else match.group(0)

    return _BARE_PROOF_VERBS.sub(_sub, text)


def _redact_phrase(text: str, phrase: str, replacement: str) -> str:
    """Redact one phrase, leaving conditional/denial uses of a bare verb intact."""

    exempt = phrase.lower() in _CONDITIONAL_VERBS

    def _sub(match: re.Match[str]) -> str:
        if exempt and _DENIAL_CONTEXT.search(text[: match.start()]):
            return match.group(0)
        return replacement

    return re.sub(forbidden_pattern(phrase), _sub, text, flags=re.IGNORECASE)


def safe_text(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    for phrase in sorted(RESERVED_STATUS_LANGUAGE, key=len, reverse=True):
        text = _redact_phrase(text, phrase, "[withheld status phrase]")
    for phrase in sorted(FORBIDDEN_LANGUAGE, key=len, reverse=True):
        text = _redact_phrase(text, phrase, "[withheld proof-like phrase]")
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
