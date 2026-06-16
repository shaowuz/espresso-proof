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

import re
from pathlib import Path

from .descriptions import (
    describe_grade,
    describe_proof_language,
    effort_phrase,
    evidence_phrase,
    judgment_tier_phrase,
    objection_phrase,
)
from .external_tool_runner import run_completed_cleanly
from .failure_pattern_bank import failure_pattern_allocation_hints
from .io import read_yaml
from .proof_language import scrub_proof_language, scrub_source_support_language
from .research_bank import load_bankable_dict
from .route_table import build_route_step_rows, render_route_step_table_lines
from .subgoal_graph import render_proof_search_lines
from .surface_warnings import collect_surface_warnings, render_warning_lines


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


_FUTURE_TEST_STATUS_PATTERNS = (
    r"\b(?:prove|proves|proved|proving|proof)\b",
    r"\b(?:verify|verifies|verified|verifying|verification)\b",
    r"\bchecked\b",
    r"\bsource[- ]backed\b",
    r"\bsource support\b",
    r"\bsource-supported\b",
    r"\b(?:refute|refutes|refuted|refuting|refutation)\b",
)


def _future_test_scope_note(text: str) -> str | None:
    lowered = text.lower()
    if any(re.search(pattern, lowered) for pattern in _FUTURE_TEST_STATUS_PATTERNS):
        return (
            "Scope: proposed next check only; not current evidence, source support, "
            "proof, or refutation."
        )
    return None


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
            "Scope of check or source: this step only — cited reference support "
            "(a cited result for this step, not a proof of the whole claim or route)"
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
    # Self-safe: a step's echoed free text (e.g. needed_theorem) can never render
    # global proof-complete language, regardless of caller. Idempotent with the
    # whole-memo scrub in build_concise_memo. Never upgrades status.
    return [scrub_source_support_language(line) for line in lines]


def build_concise_memo(run_dir: Path) -> str:
    """Render a compact memo string from a run's validated artifacts.

    This is a final user-facing surface, so it validates before rendering and
    then only reads/echoes artifact fields via the run manifest.
    """
    run_dir = Path(run_dir)
    from .validators import validate_run

    validate_run(run_dir)
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
    examples = load_bankable_dict(run_dir, "examples", manifest)
    counterexamples = load_bankable_dict(run_dir, "counterexamples", manifest)
    tests = artifact("tests")

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

    update_inbox: dict = {}
    _ui_rel = optional_artifacts.get("update_inbox")
    if _ui_rel and (run_dir / _ui_rel).exists():
        update_inbox = read_yaml(run_dir / _ui_rel)

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
    # Gap text comes from artifact free text and may carry internal tokens —
    # translate to the product vocabulary (same rewrite as the dashboard).
    evidence_summary = sorted({evidence_phrase(s) for s in source_statuses})
    shown_gaps = [
        scrub_source_support_language(g).rstrip(".")
        for g in source_gaps
    ]
    lines.append(
        "Source gaps: "
        + (", ".join(shown_gaps) if shown_gaps else "none")
        + ". Evidence so far: "
        + ("; ".join(evidence_summary) if evidence_summary else "none recorded")
        + "."
    )
    warning_lines = render_warning_lines(collect_surface_warnings(
        grade=grade,
        review=review,
        examples=examples,
        counterexamples=counterexamples,
        tests=tests,
        source_grounding=source_grounding,
        theorem_applicability=theorem_applicability,
        update_inbox=update_inbox,
    ))
    if warning_lines:
        lines.append("")
        lines.append("Open blockers / pending checks:")
        for warning in warning_lines:
            lines.append(f"  - {warning}")
    # Proof-route step table (Goal 24): one row per local step, separating local
    # step evidence from the whole-claim/route status above. Built from validated
    # artifacts (source grounding + confirmed scoped computations), using the
    # Goal 20-23 display vocabulary; never asserts the whole claim is proved.
    _pv_rel = optional_artifacts.get("proof_verification")
    proof_verification_doc: dict = {}
    if _pv_rel and (run_dir / _pv_rel).exists():
        proof_verification_doc = read_yaml(run_dir / _pv_rel)
    route_rows = build_route_step_rows(source_grounding, proof_verification_doc)
    table_lines = render_route_step_table_lines(route_rows)
    if table_lines:
        lines.append("")
        lines.extend(table_lines)
    # Detailed per-step evidence shape for earned (reference/computation) steps.
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
                "  A candidate was handed to source grounding; source_grounding currently "
                "records reference support for at least one step (see the proof-route step above)."
            )
        elif autonomous_retrieval.get("grounded"):
            lines.append(
                "  A candidate was handed to source grounding, but it did not earn reference support: "
                f"{evidence_phrase('not_found')}."
            )
        else:
            lines.append(f"  No candidate earned reference support yet: {evidence_phrase('not_found')}.")
    # Evidence-agent status board (Goal 27): show, honestly, which evidence agents
    # have run / are running / were skipped / failed / not run. A running / failed
    # / skipped / not-run / missing agent contributes NO evidence; only a 'Done'
    # agent's artifact (already validated above) is real evidence. When core
    # evidence is incomplete, route scores below are marked PRELIMINARY.
    from .agent_run_status import display_rows, evidence_incomplete

    agent_rows = display_rows(run_dir)
    routes_preliminary = evidence_incomplete(run_dir)
    if agent_rows:
        lines.append("")
        lines.append("Evidence agents:")
        for row in agent_rows:
            note = ""
            if row["status"] == "failed" and row["error_summary"]:
                note = f" — {str(row['error_summary'])[:80]}"
            elif row["status"] == "not_run":
                note = " — has not run; not counted as evidence"
            elif row["status"] == "skipped":
                note = " — skipped by request; contributes no evidence"
            elif row["status"] == "running":
                note = " — in progress; not yet evidence"
            lines.append(f"  - {row['title']}: {row['status_word']}{note}")
        lines.append(
            "  (Only a 'Done' agent's results count as evidence; the kernel still "
            "checks them. Missing/running/skipped/failed agents are not evidence.)"
        )

    lines.append("")
    if routes_preliminary:
        lines.append("Top routes (PRELIMINARY — evidence agents incomplete):")
    else:
        lines.append("Top routes:")
    if top:
        for index, route in enumerate(top[:3], start=1):
            lines.append(
                f"  {index}. {route.get('title')} "
                f"[score {route.get('score')} / 100, effort: {effort_phrase(route.get('estimated_effort'))}]"
            )
            if route.get("main_risk"):
                lines.append(f"     risk: {route.get('main_risk')}")
    else:
        lines.append("  none")
    lines.append("")
    lines.append(f"Next cheapest test: {next_test}")
    future_scope_note = _future_test_scope_note(next_test)
    if future_scope_note:
        lines.append(future_scope_note)

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

    # AI review signal (advisory): independent AI reviewers checked the run
    # against the kernel-locked checklist. ADVISORY ONLY — never a verification, a
    # proof, or checked evidence, and it never changes any status. The raw verdict
    # enum is never printed; only the human sentence below is rendered.
    if "ai_review" in optional:
        ai_review = read_yaml(run_dir / optional["ai_review"])
        verdict = ai_review.get("verdict")
        count = ai_review.get("reviewer_count", 0)
        if verdict == "no_blocking_objection":
            lines.append("")
            lines.append(
                f"AI review: {count} independent reviewers found no blocking "
                "objection (advisory — not a verification)."
            )
        elif verdict == "objections_found":
            lines.append("")
            lines.append("AI review: open objections remain — see blocked subgoals.")
        elif verdict == "insufficient_review":
            lines.append("")
            lines.append(
                "AI review: not enough independent reviewers to assess."
            )

    # Mathematical judgment (advisory): risks / next checks only. Never a status
    # upgrade and never proof-complete language.
    if "mathematical_judgment" in optional:
        judgment = read_yaml(run_dir / optional["mathematical_judgment"])
        findings = judgment.get("findings", [])
        if findings:
            lines.append("")
            lines.append("Mathematical judgment (advisory risks / next checks — not a proof):")
            for f in findings:
                # kind / recommended_action are internal tokens; render as words.
                kind = str(f.get("kind") or "").replace("_", " ")
                action = str(f.get("recommended_action") or "").replace("_", " ")
                lines.append(
                    f"  {judgment_tier_phrase(f.get('tier'))}: {kind} "
                    f"({f.get('subject')}): {f.get('explanation')} "
                    f"-> {action}"
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
                # pattern ids are internal tokens; render as words (Goal 31).
                pattern = str(m.get("pattern_id") or "pattern").replace("_", " ")
                lines.append(
                    f"  {objection_phrase(m.get('tier'))} "
                    f"({pattern}): {m.get('explanation')}"
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
            schema_version = int(mc.get("schema_version") or 1)
            lines.append("")
            lines.append("Memory from prior runs (advisory; never changes status):")
            for m in matched:
                # recommended_use / type are internal tokens; render as words.
                if schema_version < 2 or m.get("revalidation_required"):
                    use = "pending revalidation; no action"
                else:
                    use = str(m.get("recommended_use") or "").replace("_", " ")
                kind = str(m.get("type") or "").replace("_", " ")
                lines.append(
                    f"  [{use}] {m.get('summary')} "
                    f"({kind}; {m.get('relevance_reason')})"
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

    # External tool runs (Goal 25): raw, audited evidence records. They are NOT a
    # proof and NEVER an earned status by themselves -- only the kernel-gated
    # proof-verification bridge can earn a scoped status from a confirming run.
    if "external_tool_runs" in optional:
        etr = read_yaml(run_dir / optional["external_tool_runs"])
        runs = etr.get("runs", [])
        if runs:
            lines.append("")
            lines.append("External tool runs (raw audited evidence; scoped, not a proof):")
            for r in runs:
                trust = "allowlisted tool" if r.get("trusted") else "unallowlisted tool (candidate only)"
                clean = run_completed_cleanly(r.get("timed_out", False), r.get("exit_code"))
                # Fail-closed: a run that did not complete cleanly shows NO
                # self-reported conclusion (a positive-sounding "conclusion" must
                # never appear above the fail-closed note); it is simply no evidence.
                outcome = (
                    (r.get("conclusion") or r.get("parsed_result") or "ran")
                    if clean
                    else "did not complete cleanly: treated as no evidence (fail-closed)"
                )
                lines.append(
                    f"  - {r.get('tool_name', 'tool')}"
                    + (f" v{r.get('version')}" if r.get("version") else "")
                    + f": {outcome} [{trust}]"
                )
                if r.get("statement_checked"):
                    lines.append(f"      Statement checked: {r.get('statement_checked')}")
                if clean and r.get("scope_note"):
                    lines.append(f"      Scope: {r.get('scope_note')}")
            lines.append("  (external tool runs are raw evidence; they do NOT prove the claim or set any status)")

    lines.append("")
    lines.append(
        "Note: this memo reflects the run's recorded results after safety validation; "
        "an assessment is not a proof. Full detail: `espresso inspect --run <dir>`."
    )
    # Display-layer safety: redact any global proof-complete overclaim that an
    # echoed free-text artifact field (objection / risk / explanation / summary)
    # might carry, and translate raw source-support enum vocabulary. Typed status
    # descriptions are unaffected. Never upgrades status.
    return scrub_source_support_language("\n".join(lines))
