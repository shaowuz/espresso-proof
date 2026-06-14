"""Evidence trust vocabulary + colour discipline (Goal 28, Phase 4).

A single place that enumerates the human-facing trust levels the workbench shows
and the colour tone each one is allowed to use. The whole point is the colour
discipline:

- GREEN is reserved for genuinely EARNED, kernel-checked evidence
  (reference-backed step, checked in a finite/computational scope);
- reference metadata that is not yet support is neutral/blue, not green;
- amber is for smoke-checked / candidate / preliminary;
- grey is for missing / not-run / skipped;
- red is for a real blocker or an admissible refutation.

This module sets no status and reads nothing — it is the shared label/tone map
used by the renderers and pinned by tests so a future change cannot accidentally
paint a candidate/running/skipped item green.
"""

from __future__ import annotations

# level -> human-readable label (never a raw enum).
TRUST_LABELS: dict[str, str] = {
    "reference_backed": "Reference-backed step",
    "scope_checked": "Checked in this finite/computational scope",
    "smoke_checked": "Smoke-checked",
    "ai_reviewed": "AI-reviewed — no blocking objection (advisory, not a verification)",
    "candidate": "Candidate",
    "needs_check": "Needs check",
    "running": "Running",
    "skipped": "Skipped",
    "failed": "Failed",
    "admissible_refutation": "Admissible refutation",
    "route_attack": "Attacks proof route only",
    "dropped_hypothesis": "Drops a hypothesis",
}

# level -> colour tone. "earned" is the ONLY green tone.
TRUST_TONE: dict[str, str] = {
    "reference_backed": "earned",
    "scope_checked": "earned",
    "smoke_checked": "candidate",
    # AI-review signal: independent reviewers found no blocking objection. It is
    # ADVISORY ONLY (amber) and must NEVER be in EARNED_LEVELS / render green:
    # absence of an objection is not a verification, a proof, or checked evidence.
    "ai_reviewed": "candidate",
    "candidate": "candidate",
    "needs_check": "missing",
    "running": "running",
    "skipped": "missing",
    "failed": "blocker",
    "admissible_refutation": "blocker",
    "route_attack": "candidate",
    "dropped_hypothesis": "candidate",
}

# The only levels that may render green. Pinned by a test.
EARNED_LEVELS: frozenset[str] = frozenset({"reference_backed", "scope_checked"})

# tone -> CSS class fragment used by the renderers (green only for "earned").
TONE_CLASS: dict[str, str] = {
    "earned": "trust-green",
    "candidate": "trust-amber",
    "missing": "trust-grey",
    "running": "trust-amber",
    "blocker": "trust-red",
}


def trust_label(level: str) -> str:
    """Human label for a trust level (falls back to 'Needs check')."""

    return TRUST_LABELS.get(level, "Needs check")


def trust_tone(level: str) -> str:
    """Colour tone for a trust level (falls back to the conservative 'missing')."""

    return TRUST_TONE.get(level, "missing")


def is_green(level: str) -> bool:
    """True only for genuinely earned, kernel-checked evidence levels."""

    return level in EARNED_LEVELS


__all__ = [
    "TRUST_LABELS",
    "TRUST_TONE",
    "EARNED_LEVELS",
    "TONE_CLASS",
    "trust_label",
    "trust_tone",
    "is_green",
]
