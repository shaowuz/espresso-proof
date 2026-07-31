"""Conservative claim normalization from the SHAPE of a statement.

The claim-understanding layer (especially in stub/fast mode or with a weak model
reply) can misread a standard, precise statement as "unclear", which then
correctly trips the claim-clear gate and blocks every downstream stage. This
helper recovers the logical shape of such a statement and proposes a PRECISE
locked claim for it.

What changed and why. This module used to recognize one theorem: it matched
"group ... order ... cyclic" and, on a hit, wrote out a hand-authored claim with
hand-authored objects and hypotheses. That is an answer key. One sentence in
mathematics got a normalization nobody else could get, and any paraphrase
outside the pattern fell back to "unclear" — the system looked capable on the
sentence it had been taught and on no other. The defect is the mechanism, not
the coverage: adding a second template would have been the same mistake twice.

It now knows NO mathematics. It recognizes the grammatical forms a quantified
mathematical statement is written in ("every X is Y", "if H then C", "a X is Y")
and reports the parts it actually found, in the user's own words. Whether those
parts are precise enough is decided, as before, by the caller's safety floor.

Hard safety contract (unchanged):

- This NEVER upgrades proof / source / evidence / refutation / checked status,
  and never sets ``human_confirmed``. It only proposes a precise LOCKED CLAIM
  (an agent proposal) so downstream stages can run.
- "precise" does NOT mean "true": a recognized claim is locked as precise even if
  it is false. Truth is decided downstream, by evidence.
- Anything it cannot parse into a subject and a predicate returns ``None`` and
  the existing imprecise handling stands: vague claims stay vague.
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


# The shapes a quantified mathematical statement is written in. Subject and
# predicate are captured, never interpreted: this module cannot tell a group from
# a graph and must not pretend to.
_UNIVERSAL_RE = re.compile(
    r"^(?:for\s+)?(?:every|each|all|any)\s+(?P<subject>.+?)\s+(?:is|are|has|have)\s+(?P<predicate>.+)$",
    re.IGNORECASE,
)
_INDEFINITE_RE = re.compile(
    r"^(?:a|an)\s+(?P<subject>.+?)\s+(?:is|has)\s+(?P<predicate>.+)$",
    re.IGNORECASE,
)
_CONDITIONAL_RE = re.compile(
    r"^if\s+(?P<subject>.+?)[,\s]+then\s+(?P<predicate>.+)$",
    re.IGNORECASE,
)

# Vagueness in the STATEMENT ITSELF, independent of subject matter: a hedge or a
# value judgement means the sentence has no definite truth condition, whatever it
# is about. This is the one thing a shape-only normalizer can legitimately test,
# and it is why it may not simply accept every well-formed sentence.
_HEDGES = frozenset(
    {
        "probably", "maybe", "perhaps", "possibly", "roughly", "approximately",
        "somewhat", "fairly", "quite", "rather", "seems", "seem", "likely",
        "unlikely", "arguably", "interesting", "nice", "good", "better", "best",
        "useful", "important", "natural", "reasonable", "obvious", "trivial",
        "some", "several", "many", "most", "few", "usually", "often",
        "generally", "typically", "mostly", "sometimes",
    }
)

# A subject or predicate this long is a paragraph, not a mathematical part; a
# statement that needs one has not been reduced to a claim yet.
_MAX_PART_WORDS = 40


def _is_definite(part: str) -> bool:
    words = re.findall(r"[A-Za-z']+", part.lower())
    if not words or len(words) > _MAX_PART_WORDS:
        return False
    return not (_HEDGES & set(words))


def normalize_simple_claim(raw_text: str) -> dict | None:
    """A precise normalized claim recovered from a statement's shape, or ``None``.

    Recognized shapes (after stripping a command wrapper), in order:
    - "every/each/all/any SUBJECT is|are|has|have PREDICATE"
    - "if SUBJECT, then PREDICATE"
    - "a/an SUBJECT is|has PREDICATE"

    The parts are reported verbatim, so the user's own wording survives into the
    locked claim. ``None`` means the statement did not reduce to a subject and a
    predicate, or one of them was hedged or paragraph-length.
    """

    prop = strip_command_wrapper(raw_text)
    if not prop:
        return None

    for pattern, shape in (
        (_UNIVERSAL_RE, "universal"),
        (_CONDITIONAL_RE, "conditional"),
        (_INDEFINITE_RE, "indefinite"),
    ):
        match = pattern.match(prop)
        if not match:
            continue
        subject = match.group("subject").strip().rstrip(",")
        predicate = match.group("predicate").strip().rstrip(".")
        if not _is_definite(subject) or not _is_definite(predicate):
            return None
        if shape == "conditional":
            hypothesis = subject
            normalized = f"If {subject}, then {predicate}."
        else:
            hypothesis = f"the stated object satisfies: {subject}"
            quantifier = "For every" if shape == "universal" else "For any"
            normalized = f"{quantifier} {subject}: {predicate}."
        return {
            "recognized": shape,
            "normalized_statement": normalized,
            # The subject is what the statement quantifies over. Naming it in the
            # user's own words is the most this layer can honestly say: it has no
            # theory of what kind of object that is.
            "objects": [f"the object the statement quantifies over: {subject}"],
            "hypotheses": [{"name": hypothesis, "status": "explicit"}],
            "conclusion": f"{predicate}.",
            "claim_precision": "precise",
            "missing_or_unclear": [],
        }
    return None


__all__ = ["strip_command_wrapper", "normalize_simple_claim"]
