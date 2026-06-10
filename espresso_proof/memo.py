"""Concise Answer Agent: render a compact, user-facing research memo.

This is the first post-Kernel-Beta runtime slice (AGENT_AUTOMATION_ENGINEERING_PLAN
Step 1, contract docs/goals/agent_goals/22_CONCISE_ANSWER_AGENT_GOAL.md). It turns
already-validated run artifacts into the central product output: a short memo with
the grade, the strongest obstruction, source gaps, the top route, and the next
cheapest test -- without overstating the result.

Safety: this renderer only echoes fields from artifacts that already passed
``validate_run``. It never sets status, never invents facts, and never emits
proof-complete language on its own; truth labels (status enums) are surfaced
verbatim so source/evidence/proof separation stays visible.
"""

from __future__ import annotations

from pathlib import Path

from .descriptions import (
    describe_grade,
    describe_proof_language,
    evidence_phrase,
    judgment_tier_phrase,
    objection_phrase,
)
from .failure_pattern_bank import failure_pattern_allocation_hints
from .io import read_yaml
from .proof_language import scrub_proof_language
from .subgoal_graph import render_proof_search_lines


# Conservative defaults for a proof-route field that has no specific artifact
# data. Shown verbatim so a missing field never reads as support.
_NO_APPLICABILITY_REASON = "Needs a check"
_NO_REVIEWER_OBJECTIONS = "none recorded"
_DEFAULT_REMAINING_RISK = "this evidence does not prove the full theorem"

# The seven required proof-route step headings (used by tests and the renderer).
PROOF_ROUTE_STEP_HEADINGS = (
    "Statement:",
    "Evidence phrase:",
    "Evidence detail:",
    "Scope of check or source:",
    "Why it applies:",
    "Reviewer objections:",
    "Remaining risk:",
)


def _applicability_reason(proof_step_id: str, theorem_applicability: dict) -> str:
    """The applicability / hypothesis-match reason already recorded for a step.

    Reads the satisfied-condition text from the theorem-applicability audit for
    this step. Never invents a reason: if none is recorded, returns "Needs a
    check".
    """

    reasons: list[str] = []
    for usage in theorem_applicability.get("usages", []):
        if usage.get("proof_step_id") != proof_step_id:
            continue
        for condition in usage.get("conditions", []):
            text = (condition.get("text") or "").strip()
            if condition.get("status") == "satisfied" and text:
                reasons.append(text)
    # De-duplicate while preserving order.
    reasons = list(dict.fromkeys(reasons))
    return "; ".join(reasons) if reasons else _NO_APPLICABILITY_REASON


def render_proof_route_steps(
    source_grounding: dict,
    computations: dict,
    theorem_applicability: dict | None = None,
) -> list[str]:
    """Render reference-backed and machine-checked proof-route steps.

    Each step uses the full human-readable mathematical-evidence shape:
    ``Statement`` / ``Evidence phrase`` / ``Evidence detail`` / ``Scope of check
    or source`` / ``Why it applies`` / ``Reviewer objections`` / ``Remaining
    risk``. This only echoes already-earned artifact fields; it never sets or
    upgrades status, never invents an applicability reason, and never emits
    proof-complete language. Fields with no specific artifact data render
    conservative text.
    """

    theorem_applicability = theorem_applicability or {}
    lines: list[str] = []
    refs_by_id = {r.get("id"): r for r in source_grounding.get("source_refs", [])}
    # Reference-backed steps: a cited result, never Espresso's own proof.
    for step in source_grounding.get("steps", []):
        if step.get("status") != "source_backed":
            continue
        details = []
        for ref_id in step.get("source_refs", []):
            ref = refs_by_id.get(ref_id)
            if not ref:
                continue
            loc = ref.get("locator", {})
            authors = ", ".join(ref.get("authors", [])) or "unknown author"
            details.append(
                f"{authors}, {ref.get('title', 'untitled')} "
                f"[{loc.get('kind')}:{loc.get('value')}]"
            )
        lines.append(f"Statement: {step.get('needed_theorem', '').strip()}")
        lines.append(f"Evidence phrase: {evidence_phrase('source_backed')}")
        lines.append(
            "Evidence detail: "
            + ("; ".join(details) if details else "cited reference")
        )
        lines.append(
            "Scope of check or source: cited reference support "
            "(a cited result, not a proof of the claim)"
        )
        lines.append(
            "Why it applies: "
            + _applicability_reason(step.get("proof_step_id", ""), theorem_applicability)
        )
        lines.append(f"Reviewer objections: {_NO_REVIEWER_OBJECTIONS}")
        lines.append(f"Remaining risk: {_DEFAULT_REMAINING_RISK}")
        lines.append("-")
    # Machine-checked computations: a confirmed deterministic result, not a proof.
    for check in computations.get("checks", []):
        if check.get("status") != "checked":
            continue
        mc = check.get("machine_check") or {}
        if not mc:
            continue
        statement = (check.get("computed_quantity") or check.get("id") or "").strip()
        lines.append(f"Statement: {statement}")
        lines.append(f"Evidence phrase: {evidence_phrase('computation_checked')}")
        lines.append(
            "Evidence detail: "
            f"{mc.get('kind')} check: {mc.get('expression')} = {mc.get('expected')}"
        )
        lines.append(
            "Scope of check or source: this finite/computational instance only; "
            "not a proof of the claim"
        )
        # A computation has no source-applicability reason of its own.
        lines.append(f"Why it applies: {_NO_APPLICABILITY_REASON}")
        lines.append(f"Reviewer objections: {_NO_REVIEWER_OBJECTIONS}")
        lines.append(f"Remaining risk: {_DEFAULT_REMAINING_RISK}")
        lines.append("-")
    return lines


def build_concise_memo(run_dir: Path) -> str:
    """Render a compact memo string from a run's validated artifacts.

    Callers must run ``validate_run`` first; this function assumes the run is
    valid and only reads/echoes artifact fields via the run manifest.
    """
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts", {})

    def artifact(key: str) -> dict:
        return read_yaml(run_dir / artifacts[key])

    claim = artifact("locked_claim")
    grade = artifact("idea_grade")
    review = artifact("review")
    strategy_brief = artifact("strategy_brief")
    source_grounding = artifact("source_grounding")
    computations = artifact("computation_checks")

    status = grade.get("status", "unknown")
    score = grade.get("score", "?")
    proof_status = grade.get("proof_language_status", "unknown")
    claim_text = (claim.get("normalized_statement") or "").strip()
    claim_state = "locked" if claim.get("locked") else "unlocked"
    objection = (review.get("strongest_objection") or "none recorded").strip()

    # Theorem-applicability audit (optional) carries the recorded "why it applies"
    # reason for a source-backed step; load it if present (never invented here).
    optional_artifacts = manifest.get("optional_artifacts", {})
    theorem_applicability: dict = {}
    _ta_rel = optional_artifacts.get("theorem_applicability")
    if _ta_rel and (run_dir / _ta_rel).exists():
        theorem_applicability = read_yaml(run_dir / _ta_rel)

    # Autonomous literature search (optional): candidate sources found without a
    # human-provided source. Rendered as "Found by autonomous literature search"
    # + "Proposed, not checked"; it never claims reference support by itself.
    autonomous_retrieval: dict = {}
    _alr_rel = optional_artifacts.get("autonomous_literature_retrieval")
    if _alr_rel and (run_dir / _alr_rel).exists():
        autonomous_retrieval = read_yaml(run_dir / _alr_rel)

    source_gaps = grade.get("source_gaps") or []
    source_statuses = sorted(
        {ref.get("status") for ref in source_grounding.get("source_refs", []) if ref.get("status")}
        | {step.get("status") for step in source_grounding.get("steps", []) if step.get("status")}
    )

    top = (strategy_brief.get("top_strategies") or [])
    next_test = (grade.get("next_cheapest_test") or "none recorded").strip()

    lines: list[str] = []
    lines.append(f"ESPRESSO MEMO — claim {grade.get('claim_id', claim.get('id', '?'))}")
    lines.append("")
    lines.append(f"Claim [{claim_state}]: {claim_text}")
    lines.append("")
    # 2-3 sentence summary in concrete English (no abstract status labels).
    assessment = describe_grade(status=status, proof_language_status=proof_status)
    lines.append(f"Summary: {assessment} Strongest obstruction: {objection}")
    if status == "unclear":
        lines.append("First action: lock/clarify the claim before testing it.")
    lines.append("")
    lines.append(f"Verdict: {assessment}")
    lines.append(
        f"Confidence score: {score} (internal heuristic; not a probability of truth)"
    )
    lines.append(f"Proof state: {describe_proof_language(proof_status)}")
    # Provenance in plain mathematical-evidence wording (never raw status tokens).
    evidence_summary = sorted({evidence_phrase(s) for s in source_statuses})
    lines.append(
        "Source: gaps="
        + (", ".join(source_gaps) if source_gaps else "none")
        + "; evidence so far: "
        + ("; ".join(evidence_summary) if evidence_summary else "none recorded")
    )
    # Proof-route steps in the human-readable evidence shape.
    route_steps = render_proof_route_steps(
        source_grounding, computations, theorem_applicability
    )
    if route_steps:
        lines.append("")
        lines.append("Proof-route steps:")
        lines.extend(route_steps)
    # Autonomous literature search candidates (proposed only; not checked).
    candidates = autonomous_retrieval.get("candidates") or []
    if candidates:
        lines.append("")
        lines.append("Autonomous literature search:")
        for cand in candidates[:3]:
            authors = ", ".join(cand.get("authors", [])) or "unknown author"
            lines.append(
                f"- Found by autonomous literature search: {cand.get('title', 'untitled')} "
                f"({authors}) [{cand.get('provider')}] — {evidence_phrase('candidate')}."
            )
            if cand.get("why_relevant"):
                lines.append(f"  Why relevant: {cand['why_relevant']}")
        if autonomous_retrieval.get("earned_source_backed"):
            lines.append(
                "  A candidate was handed to source grounding and a reference now supports a step "
                "(see the proof-route step above)."
            )
        elif autonomous_retrieval.get("grounded"):
            lines.append(
                "  A candidate was handed to source grounding, but it did not earn reference support: "
                f"{evidence_phrase('not_found')}."
            )
        else:
            lines.append(f"  No candidate earned reference support yet: {evidence_phrase('not_found')}.")
    lines.append("")
    lines.append("Top routes:")
    if top:
        for index, route in enumerate(top[:3], start=1):
            lines.append(
                f"  {index}. {route.get('title')} "
                f"[score {route.get('score')}, effort {route.get('estimated_effort')}]"
            )
            if route.get("main_risk"):
                lines.append(f"     risk: {route.get('main_risk')}")
    else:
        lines.append("  none")
    lines.append("")
    lines.append(f"Next cheapest test: {next_test}")

    optional = manifest.get("optional_artifacts", {})

    # Proof-search state (advisory): the durable subgoal graph, when one was
    # built. It is a map of the search (open / sketched / blocked / next), never
    # proof evidence; the renderer cannot emit proof-complete language. Rendered
    # from the SAME helper as the dashboard so both surfaces stay in sync.
    _pgg_rel = optional.get("proof_goal_graph")
    if _pgg_rel and (run_dir / _pgg_rel).exists():
        graph = read_yaml(run_dir / _pgg_rel)
        graph_lines = render_proof_search_lines(graph)
        if graph_lines:
            lines.append("")
            lines.extend(graph_lines)

    # Mathematical judgment (advisory): risks / next checks only. Never a status
    # upgrade and never proof-complete language.
    if "mathematical_judgment" in optional:
        judgment = read_yaml(run_dir / optional["mathematical_judgment"])
        findings = judgment.get("findings", [])
        if findings:
            lines.append("")
            lines.append("Mathematical judgment (advisory risks / next checks — not a proof):")
            for f in findings:
                lines.append(
                    f"  {judgment_tier_phrase(f.get('tier'))}: {f.get('kind')} "
                    f"({f.get('subject')}): {f.get('explanation')} "
                    f"-> {f.get('recommended_action')}"
                )

    # Failure pattern audit (advisory): matched risk shapes. Never a refutation
    # and never a status upgrade.
    if "failure_pattern_audit" in optional:
        audit = read_yaml(run_dir / optional["failure_pattern_audit"])
        pmatches = audit.get("matches", [])
        if pmatches:
            lines.append("")
            lines.append("Failure patterns matched (advisory; not a refutation, not a proof):")
            for m in pmatches:
                lines.append(
                    f"  {objection_phrase(m.get('tier'))} "
                    f"({m.get('pattern_id')}): {m.get('explanation')}"
                )
            # Audited-tier effect: ranking / time-allocation guidance (advisory
            # weight only; never changes a score, grade, or status).
            hints = failure_pattern_allocation_hints(audit)
            if hints["rank_downgrade"]:
                subjects = ", ".join(
                    sorted({h["subject"] for h in hints["rank_downgrade"] if h.get("subject")})
                )
                lines.append(
                    "  Ranking/time-allocation hint (advisory; no score/status change): "
                    f"downgrade ranking of routes touching {subjects or 'the affected steps'}."
                )
                for rec in hints["time_allocation"]:
                    lines.append(f"  Next-effort hint: {rec}")

    # Memory influence (advisory): prior-run lessons matched to this claim. Never
    # changes status; only suggests what to avoid / retest / source / prioritize.
    if "memory_context" in optional:
        mc = read_yaml(run_dir / optional["memory_context"])
        matched = mc.get("matched_entries", [])
        if matched:
            lines.append("")
            lines.append("Memory from prior runs (advisory; never changes status):")
            for m in matched:
                lines.append(
                    f"  [{m.get('recommended_use')}] {m.get('summary')} "
                    f"({m.get('type')}; {m.get('relevance_reason')})"
                )

    # Proof verification bridge: scoped machine-verified instances only. NEVER a
    # universal proof; the renderer never emits "proved".
    if "proof_verification" in optional:
        pv = read_yaml(run_dir / optional["proof_verification"])
        checked = [e for e in pv.get("entries", []) if e.get("earned_status") == "instance_checked"]
        if checked:
            lines.append("")
            lines.append("Machine-verified instances (scoped; NOT a proof of the universal claim):")
            for e in checked:
                lines.append(
                    f"  - {e.get('target')}: {e.get('result')} "
                    f"({evidence_phrase(e.get('earned_status'))})"
                )
                if e.get("scope_note"):
                    lines.append(f"      Scope: {e.get('scope_note')}")
            lines.append("  (these finite/computational checks do NOT prove the universal claim)")

    lines.append("")
    lines.append(
        "Note: artifact-backed memo; an assessment is not a proof. "
        "Full detail: `espresso inspect --run <dir>`."
    )
    # Display-layer safety: redact any global proof-complete overclaim that an
    # echoed free-text artifact field (objection / risk / explanation / summary)
    # might carry. Typed status descriptions are unaffected (the sanitizer only
    # matches global "the whole theorem is proved" language). Never upgrades status.
    return scrub_proof_language("\n".join(lines))
