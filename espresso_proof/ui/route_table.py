"""Proof-route step table (shared by memo + dashboard).

Renders a route as research notes: one row per local step, separating what each
step LOCALLY needs/has from the whole-claim/route status. Rows are derived purely
from validated artifacts (source grounding + confirmed proof-verification), never
from invented UI prose. The evidence-status column uses the human display
vocabulary established in Goals 20-23; it never asserts the whole claim is proved.

Columns: Step | Local claim | Intended support | Evidence status |
         Locator / computation scope | Gaps | Next check
"""

from __future__ import annotations

from espresso_proof.kernel.models import Locator

# Trusted deterministic verifier backends whose confirmed result may surface as a
# scoped computation row. Mirrors validators._TRUSTED_EARNED (the kernel gate is
# authoritative; this keeps the DISPLAY gate from being looser than the kernel,
# so a hand-forged non-trusted entry never renders as a checked computation).
_TRUSTED_COMPUTATION_VERIFIERS = frozenset(
    {"finite_group_cayley", "finite_identity_table", "finite_ring_identity",
     "matrix_rank", "exact_computation"}
)

ROUTE_TABLE_COLUMNS = (
    "Step",
    "Local claim",
    "Intended support",
    "Evidence status",
    "Locator / computation scope",
    "Gaps",
    "Next check",
)


def _locator_str(locator: dict) -> str:
    kind = str(locator.get("kind") or "").strip()
    value = str(locator.get("value") or "").strip()
    if kind and value:
        return f"{kind}:{value}"
    return ""


def _is_exact_locator(locator: dict) -> bool:
    # Reuse the canonical Locator.is_exact (single source of truth) so the display
    # never disagrees with the kernel's source-backing gate. Fall back to False for
    # a malformed locator dict.
    try:
        return Locator.model_validate(locator).is_exact
    except Exception:  # noqa: BLE001 - a malformed locator is simply "not exact" for display
        return False


def _source_row(step: dict, refs_by_id: dict) -> dict:
    status = str(step.get("status") or "")
    refs = [refs_by_id[r] for r in step.get("source_refs", []) if r in refs_by_id]
    citation = "; ".join(
        f"{', '.join(r.get('authors') or []) or 'unknown author'}, {r.get('title', 'untitled')}"
        for r in refs
    ) or "none yet"
    locator = next((r.get("locator", {}) for r in refs if r.get("locator")), {})
    locator_text = _locator_str(locator) or "—"
    match = step.get("hypothesis_match") or {}
    matches = match.get("matches")
    mismatches = match.get("mismatches") or []
    has_exact = _is_exact_locator(locator)

    gaps: list[str] = []
    if status == "source_backed":
        evidence = "Reference-supported step"
        next_check = "—"
    elif status == "misapplied":
        evidence = "Hypothesis warning, not a refutation"
        gaps.append("cited theorem does not match this step")
        next_check = "Needs human math review"
    elif status == "not_found":
        evidence = "Needs a source"
        gaps.append("no source located")
        next_check = "Find a source for this step"
    else:  # candidate / unverified -> show the most blocking gap first
        if not refs:
            evidence = "Needs a source"
            gaps.append("no source cited for this step")
            next_check = "Find a source for this step"
        elif not has_exact:
            evidence = "Needs exact locator"
            gaps.append("no exact theorem/section/page locator")
            next_check = "Find the exact theorem/locator in the source"
        elif matches is not True:
            evidence = "Needs hypothesis match"
            gaps.append("hypotheses not confirmed to match the claim")
            next_check = "Check the source theorem's hypotheses against the claim"
        else:
            evidence = "Plausible but not checked"
            next_check = "Ground or check this step"
    if mismatches:
        gaps.append("mismatched: " + ", ".join(str(m) for m in mismatches))

    return {
        "step": step.get("proof_step_id", "step"),
        "local_claim": (step.get("needed_theorem") or "").strip() or "—",
        "intended_support": citation,
        "evidence_status": evidence,
        "locator_scope": locator_text,
        "gaps": "; ".join(gaps) if gaps else "none recorded",
        "next_check": next_check,
    }


def _computation_row(entry: dict) -> dict:
    # Only confirmed, trusted scoped checks become a row; always labelled scoped.
    return {
        "step": entry.get("id", "check"),
        "local_claim": (entry.get("target") or "").strip() or "—",
        "intended_support": "deterministic computation",
        "evidence_status": "Checked computation: scoped",
        "locator_scope": (entry.get("scope_note") or entry.get("result") or "scoped check").strip(),
        "gaps": "scoped to the stated finite/computational case; not a universal proof",
        "next_check": "—",
    }


def build_route_step_rows(
    source_grounding: dict | None,
    proof_verification: dict | None = None,
) -> list[dict]:
    """Return the proof-route step rows for display. Pure / artifact-derived.

    Source-grounding steps become source rows (Reference-supported / Needs exact
    locator / Needs hypothesis match / Needs a source / Hypothesis warning); each
    trusted+confirmed proof-verification entry becomes a scoped computation row.
    A row's evidence is LOCAL: it never asserts the whole claim or route is proved.
    """
    source_grounding = source_grounding or {}
    refs_by_id = {r.get("id"): r for r in source_grounding.get("source_refs", [])}
    rows = [_source_row(step, refs_by_id) for step in source_grounding.get("steps", [])]
    for entry in (proof_verification or {}).get("entries", []):
        if (
            entry.get("trusted")
            and entry.get("confirmed")
            and entry.get("verifier_type") in _TRUSTED_COMPUTATION_VERIFIERS
            and entry.get("earned_status") in {"computation_checked", "instance_checked"}
        ):
            rows.append(_computation_row(entry))
    return rows


def render_route_step_table_lines(rows: list[dict], *, cell_limit: int = 48) -> list[str]:
    """Render the route step table as plain memo text lines (empty if no rows).

    A compact per-step block keyed by the canonical columns -- readable in a
    terminal where a wide pipe table would wrap. Step-local only; the caller's
    memo separately shows the route/claim status.
    """
    if not rows:
        return []

    def short(text: object) -> str:
        text = " ".join(str(text or "").split())
        return text if len(text) <= cell_limit else text[: cell_limit - 1].rstrip() + "…"

    lines = [
        "Proof-route step table (source-grounding plan; local step evidence only; not a proof of the whole claim or route):"
    ]
    for row in rows:
        # Long free text (claim / citation / locator) is truncated for the terminal;
        # Gaps + Next check are shown in FULL so an honesty caveat (e.g. "not a
        # universal proof") and the recommended action are never cut off.
        lines.append(f"  [{short(row['step'])}] {short(row['local_claim'])}")
        lines.append(f"      Intended support: {short(row['intended_support'])}")
        lines.append(f"      Evidence status: {row['evidence_status']}")
        lines.append(f"      Locator / computation scope: {short(row['locator_scope'])}")
        lines.append(f"      Gaps: {row['gaps']}")
        lines.append(f"      Next check: {row['next_check']}")
    return lines


__all__ = [
    "ROUTE_TABLE_COLUMNS",
    "build_route_step_rows",
    "render_route_step_table_lines",
]
