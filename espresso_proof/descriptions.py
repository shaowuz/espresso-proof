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
        "The claim faces serious pressure, but no checked counterexample has "
        "been verified."
    ),
    "refuted": (
        "A checked counterexample satisfies the claim hypotheses and violates "
        "the conclusion."
    ),
}

# The ONLY statuses that may render the unqualified, GLOBAL "a proof has been
# checked by a trusted verifier" sentence. computation_checked is deliberately
# NOT here: a computation is checked for a stated finite/computational SCOPE and
# is never a proof of the full claim, so it gets its own scoped sentence in
# describe_grade. lean_checked / human_confirmed are global-proof statuses that
# are not earnable in V0 (the kernel rejects them), so this sentence does not
# currently appear -- the set is kept for when an audited global lane exists.
VERIFIED_PROOF_STATUSES: frozenset[str] = frozenset(
    {"lean_checked", "human_confirmed"}
)

PROOF_CHECKED_DESCRIPTION = "A proof has been checked by a trusted verifier."

# computation_checked is scoped evidence, never a global proof.
COMPUTATION_CHECKED_DESCRIPTION = (
    "A computation was checked for a stated finite/computational scope; "
    "this is not a proof of the full claim."
)

# ProofLanguageStatus enum value -> concrete English (never the word "proved").
PROOF_LANGUAGE_DESCRIPTIONS: dict[str, str] = {
    "idea_sketch": "an idea sketch (not a checked proof)",
    "proof_candidate": "a candidate argument (not yet checked)",
    "source_checked_argument": (
        "an argument with reference-supported steps (not a checked proof)"
    ),
    "computation_checked": (
        "a computation checked for a stated finite/computational scope "
        "(not a proof of the full claim)"
    ),
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

    pls = str(proof_language_status)
    # A scoped computation check is NOT a global proof: it gets a scoped sentence,
    # never the unqualified "a proof has been checked by a trusted verifier".
    if pls == "computation_checked":
        return COMPUTATION_CHECKED_DESCRIPTION
    if pls in VERIFIED_PROOF_STATUSES:
        return PROOF_CHECKED_DESCRIPTION
    return describe_idea_status(status)


# ---------------------------------------------------------------------------
# Mathematical evidence wording.
#
# Internal artifacts use stable status enums (``SourceStatus``,
# ``ComputationStatus``, ``ProofVerificationStatus``, ``RetrievedSourceStatus``,
# ``FailurePatternTier`` ...). User-facing memo / inspect / report surfaces must
# never print those raw enum tokens; they translate to plain mathematical
# evidence phrases here. See ``docs/engineering/EVIDENCE_DISPLAY_WORDING.md``.
# These are display phrases only — they never set or upgrade any status.
# ---------------------------------------------------------------------------

# A short fallback that never asserts more than the artifact earned.
_EVIDENCE_FALLBACK = "Needs a check"

# Source / evidence / proof-verification status -> evidence phrase.
EVIDENCE_PHRASES: dict[str, str] = {
    "source_backed": (
        "Reference supports this stated claim; see locator and applicability reason"
    ),
    # Reference-supported steps are local support, not a machine check.
    "source_checked": (
        "Reference supports this stated claim; see locator and applicability reason"
    ),
    "computation_checked": (
        "Checked by computation for the stated finite/computational scope"
    ),
    # ComputationStatus.checked is the earned machine-checked computation result.
    "checked": (
        "Checked by computation for the stated finite/computational scope"
    ),
    "instance_checked": "Checked in this finite case only",
    "lean_checked": "Checked by a trusted verifier for the stated scope",
    "human_attested": "Attested by a human reviewer (not a machine check)",
    "human_confirmed": "Confirmed by a human reviewer (not a machine check)",
    "candidate": "Proposed, not checked",
    "human_needed": "Needs human judgment",
    "not_found": "Needs a source",
    "unverified": "Needs a check",
    "inconclusive": "Not enough information to assess safely",
    "misapplied": "A reviewer found a gap in how the source applies",
    "wrong": "A check found this does not hold as stated",
}


def evidence_phrase(label: str | None) -> str:
    """Plain mathematical-evidence phrase for an internal source/evidence status.

    Never echoes the raw enum token; an unknown label falls back to "Needs a
    check" so a missing/unexpected status is shown as unproven, not as support.
    """

    if not label:
        return _EVIDENCE_FALLBACK
    return EVIDENCE_PHRASES.get(str(label), _EVIDENCE_FALLBACK)


# FailurePatternTier value -> reviewer-objection phrase.
OBJECTION_PHRASES: dict[str, str] = {
    "hard": "Objection: matches a known failure pattern",
    "audited": "Objection: matches a reviewed warning pattern",
    "candidate": "Objection to review: possible failure pattern",
}


def objection_phrase(tier: str | None) -> str:
    """Reviewer-objection phrase for a failure-pattern tier (advisory, never a refutation)."""

    return OBJECTION_PHRASES.get(
        str(tier), "Objection to review: possible failure pattern"
    )


# JudgmentRiskTier value -> readable severity word (advisory risk, never a status).
JUDGMENT_TIER_PHRASES: dict[str, str] = {
    "info": "Note",
    "warn": "Caution",
    "block_candidate": "Serious concern, needs human review",
}


def judgment_tier_phrase(tier: str | None) -> str:
    """Readable severity word for a mathematical-judgment risk tier."""

    return JUDGMENT_TIER_PHRASES.get(str(tier), "Note")


# --------------------------------------------------------------------------- #
# Goal 31 — product copy inventory (single language map for user-facing labels)
# --------------------------------------------------------------------------- #

# Prover-loop stop reason -> plain product wording (never the raw token).
LOOP_STOP_PHRASES: dict[str, str] = {
    "budget_rounds": "the round budget was used up",
    "budget_agent_calls": "the agent-call budget was used up",
    "no_progress": "no new progress was being made",
    "clean": "no blocking reviewer objection remained in the latest round (not a verification)",
    "validation_error": "a validation problem stopped the loop safely",
    "loop_decision_ask_human": "the loop asked for your decision",
    "loop_decision_stop": "the loop chose to stop",
    "time_allocation_ask_human": "the scheduler asked for your decision",
    "time_allocation_stop": "the scheduler chose to stop",
}


def loop_stop_phrase(reason: str | None) -> str:
    """Plain wording for a prover-loop stop reason (falls back conservatively)."""

    return LOOP_STOP_PHRASES.get(str(reason), "the loop stopped")


def effort_phrase(effort: str | None) -> str:
    """Human wording for an exploration-effort tier ('quick test', never quick_test)."""

    return str(effort or "").replace("_", " ")


# The Goal 31 copy inventory: one consistent product sentence per state. These
# are DISPLAY phrases (never statuses); each pairs what-happened wording with
# the safety framing the workbench repeats: process state is not evidence, and
# nothing here upgrades a proof/source/evidence/grade/claim status.
PRODUCT_COPY: dict[str, str] = {
    "waiting": "Queued to run.",
    "running": "In progress; not yet evidence.",
    "partial_result": "Preliminary — evidence agents incomplete.",
    "failed_job": "This step failed safely. Your math state is unchanged — the previous valid run is preserved. Retry, or skip this agent.",
    "retrying": "Retrying — the previous valid run is preserved.",
    "skipped_agent": "Skipped by request; contributes no evidence.",
    "missing_source": "Needs a source.",
    "needs_locator": "Needs an exact locator.",
    "needs_hypothesis_match": "Needs a hypothesis match.",
    "route_attack": "Attacks a proof step — not a claim refutation.",
    "dropped_hypothesis": "Drops a hypothesis — cannot refute the original claim.",
    "admissible_refutation": "An admissible checked counterexample refutes the claim.",
    "scoped_computation": "Checked in this finite/computational scope — not a proof of the universal claim.",
    "smoke_checked": "Smoke-checked — plausible model reasoning, not verified.",
    "reference_supported": "Reference-supported step — a cited result for this step, not a proof of the whole claim.",
}


def product_copy(state: str) -> str:
    """The product sentence for a copy-inventory state (conservative fallback)."""

    return PRODUCT_COPY.get(state, "Needs a check.")
