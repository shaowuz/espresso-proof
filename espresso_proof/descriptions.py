"""Concrete English descriptions for user-visible grade / proof status.

Internal enums (`IdeaStatus`, `ProofLanguageStatus`) stay for compatibility and
validation, but user-facing surfaces — the concise memo, ``espresso inspect``
output, strategy-brief summaries, and any UI-facing text — must render concrete
English, never abstract labels such as ``unclear`` / ``fragile`` / ``promising``
/ ``likely_false`` / ``refuted`` / ``proved``.

The word "proved" must never appear unless a trusted verifier actually checked
the proof (a machine verifier or a human confirmation), so the "a proof has been
checked" description is gated on ``VERIFIED_PROOF_STATUSES``.
"""

from __future__ import annotations

# IdeaStatus enum value -> concrete English description.
IDEA_STATUS_DESCRIPTIONS: dict[str, str] = {
    "unclear": "The claim is not precise enough to study safely.",
    "fragile": (
        "The claim is precise, but the current route has missing hypotheses, "
        "source gaps, or counterexample pressure."
    ),
    "promising": (
        "The claim is precise and has a plausible route, but no proof has been "
        "checked."
    ),
    "likely_false": (
        "The claim faces multiple serious pressures, but no checked "
        "counterexample has been verified."
    ),
    "refuted": (
        "A checked counterexample satisfies the claim hypotheses and violates "
        "the conclusion."
    ),
}

# A proof may be described as "checked" ONLY when a trusted verifier actually
# checked it: a machine verifier (computation / Lean) or an explicit human
# confirmation. A merely source-checked argument is NOT a checked proof.
VERIFIED_PROOF_STATUSES: frozenset[str] = frozenset(
    {"computation_checked", "lean_checked", "human_confirmed"}
)

PROOF_CHECKED_DESCRIPTION = "A proof has been checked by a trusted verifier."

# ProofLanguageStatus enum value -> concrete English (never the word "proved").
PROOF_LANGUAGE_DESCRIPTIONS: dict[str, str] = {
    "idea_sketch": "an idea sketch (not a checked proof)",
    "proof_candidate": "a candidate argument (not yet checked)",
    "source_checked_argument": (
        "an argument with source-checked steps (not a full verified proof)"
    ),
    "computation_checked": "a computation-checked result",
    "lean_checked": "a Lean-checked proof",
    "human_confirmed": "a human-confirmed proof",
}


def describe_idea_status(status: str | None) -> str:
    """Concrete English for an IdeaStatus value."""

    return IDEA_STATUS_DESCRIPTIONS.get(
        str(status), "The claim status is not yet determined."
    )


def describe_proof_language(proof_language_status: str | None) -> str:
    """Concrete English for a ProofLanguageStatus value."""

    return PROOF_LANGUAGE_DESCRIPTIONS.get(
        str(proof_language_status), "an unverified candidate (not a checked proof)"
    )


def describe_grade(
    *, status: str | None, proof_language_status: str | None = None
) -> str:
    """The single user-visible sentence for a claim's grade.

    If a trusted verifier actually checked the proof, that takes precedence (and
    is the only way the "a proof has been checked" wording appears). Otherwise the
    description reflects the IdeaStatus, none of which assert a proof.
    """

    if str(proof_language_status) in VERIFIED_PROOF_STATUSES:
        return PROOF_CHECKED_DESCRIPTION
    return describe_idea_status(status)
