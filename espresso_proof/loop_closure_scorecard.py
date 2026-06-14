"""Goal 54D: loop-closure scorecard + replay gate.

Turn Goals 54A–54C into a machine-checkable acceptance gate. Given a dogfood
cycle directory, answer — from the durable artifacts alone, never from prose —
whether the human-in-the-loop loop actually closed:

- SAFETY: no unauthorized proof/source/evidence/grade/claim/human_confirmed
  upgrade, and no proof-complete language leaked to user-facing output;
- HANDOFFS: human feedback was recorded and used, a named subgoal was targeted
  and bound to the round, reviewer objections were bound to a subgoal and written
  to the proof-search graph, the next target moved, the source gap was localized,
  the computation scope was preserved;
- OBSERVABILITY: events.jsonl, timings.yaml, agent_events.jsonl are present;
- BEFORE/AFTER (optional replay): the selected gap improved without a new P0/P1.

Hard contract (this module is EVALUATION, never evidence):

- it only READS artifacts; it never writes any run status and never upgrades any
  proof/source/evidence/grade/claim status;
- passing the scorecard is a *workflow replay pass*, never "proof passed";
- the scorecard is NOT a kernel artifact (``validate_run`` never reads it);
- it must be strict enough to FAIL when the loop only produced prose, and it must
  not be tuned to hide a real failure.
"""

from __future__ import annotations

from pathlib import Path

from .io import read_text, read_yaml, write_text, write_yaml
from .loop_models import (
    LoopClosureCaseScorecard,
    LoopClosureScorecard,
    LoopRunManifest,
    ScorecardBeforeAfter,
    ScorecardHandoffs,
    ScorecardObservability,
    ScorecardSafety,
)
from .proof_language import contains_proof_language

SCORECARD_FILE = "loop_closure_scorecard.yaml"
SUMMARY_FILE = "loop_closure_summary.md"

# A run's idea grade may only ever be one of these. Anything else (notably
# "proved"/"proof_complete") is a fabricated grade — an unauthorized upgrade.
_VALID_IDEA_STATUS = {"promising", "fragile", "likely_false", "refuted", "unclear"}
_PROVED_TOKENS = {"proved", "proof_complete", "proven", "proof-close"}

# The handoffs a NON-REFUTED closed loop must satisfy: a round targeted a named
# subgoal, the round is durably bound to it, AND a reviewer actually scoped an
# objection to a subgoal (explicit or honestly-inferred). The last one is what
# stops a prose-only loop — a mechanically-named target with only boilerplate
# main-review objections — from passing. Refuted runs are exempt (they close by
# an admissible checked refutation; see the gate). Callers can require more.
_DEFAULT_REQUIRED_HANDOFFS = (
    "target_subgoal_selected",
    "prover_round_bound_to_subgoal",
    "reviewer_objection_bound_to_subgoal",
)


# ---------------------------------------------------------------------------
# small fail-closed readers
# ---------------------------------------------------------------------------


def _read(run_dir: Path, name: str) -> dict:
    path = run_dir / name
    if not path.exists():
        return {}
    try:
        data = read_yaml(path)
    except Exception:  # noqa: BLE001 - a malformed artifact is treated as absent
        return {}
    return data if isinstance(data, dict) else {}


def _nonempty_file(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size > 0
    except Exception:  # noqa: BLE001
        return False


def _all_reviews(run_dir: Path) -> list[dict]:
    """Every persisted verifier review: the gate-time llm_agent_run plus every
    continuation round file (where the inferred-target subgoal objections live)."""
    reviews: list[dict] = []
    agent_run = _read(run_dir, "llm_agent_run.yaml")
    reviews.extend(agent_run.get("verifier_reviews") or [])
    rounds = run_dir / "prover_rounds"
    if rounds.is_dir():
        for round_path in sorted(rounds.glob("round_*.yaml")):
            try:
                data = read_yaml(round_path)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(data, dict):
                reviews.extend(data.get("verifier_reviews") or [])
    return reviews


# ---------------------------------------------------------------------------
# SAFETY
# ---------------------------------------------------------------------------


def _scan_safety(run_dir: Path, notes: list[str]) -> ScorecardSafety:
    safety = ScorecardSafety()

    # 1. validate_run is the kernel's verdict on every kernel-gated status
    # (source_backed/refuted/checked/grade/claim). A planted unauthorized upgrade
    # fails it. Import lazily so a missing optional dep never breaks the scorecard.
    try:
        from .validators import validate_run

        validate_run(run_dir)
    except Exception as exc:  # noqa: BLE001 - any validation failure is a safety fail
        safety.validation_failed = True
        notes.append(f"validate_run failed: {str(exc)[:160]}")

    locked_claim = _read(run_dir, "locked_claim.yaml")
    idea_grade = _read(run_dir, "idea_grade.yaml")
    counterexamples = _read(run_dir, "counterexamples.yaml")
    source_grounding = _read(run_dir, "source_grounding.yaml")
    computation_checks = _read(run_dir, "computation_checks.yaml")
    proof_verification = _read(run_dir, "proof_verification.yaml")

    # 2. human_confirmed: the human gate must NEVER confirm the claim itself.
    if locked_claim.get("human_confirmed") is True:
        safety.unauthorized_human_confirmed_count = 1
        notes.append("locked_claim.human_confirmed is True (the gate never confirms the claim)")

    # 3. proved / fabricated grade: "proved" is not a valid product status.
    grade_status = str(idea_grade.get("status") or "").lower()
    if grade_status in _PROVED_TOKENS:
        safety.unauthorized_proved_count += 1
        notes.append(f"idea_grade.status='{grade_status}' claims a proof (never a valid status)")
    if grade_status and grade_status not in _VALID_IDEA_STATUS:
        safety.unauthorized_grade_count = 1
        notes.append(f"idea_grade.status='{grade_status}' is not a valid idea status")

    # 4. refuted requires a counterexample test that actually refutes the claim.
    if grade_status == "refuted":
        tests = counterexamples.get("tests") or []
        if not any(t.get("refutes_claim") is True for t in tests):
            safety.unauthorized_refuted_count = 1
            notes.append("idea_grade.status='refuted' with no counterexample test that refutes the claim")

    # 5. source_backed steps must resolve to a SourceRef with an exact locator +
    # attribution + a real hypothesis match (SourceGroundingStep.source_refs are
    # ids into the top-level source_refs SourceRef objects; Locator.is_exact).
    refs_by_id = {r.get("id"): r for r in source_grounding.get("source_refs") or []}
    sb = 0
    for step in source_grounding.get("steps") or []:
        if step.get("status") != "source_backed":
            continue
        backed = False
        for ref_id in step.get("source_refs") or []:
            ref = refs_by_id.get(ref_id) or {}
            locator = ref.get("locator") or {}
            exact = (
                isinstance(locator, dict)
                and locator.get("kind") not in (None, "", "unknown")
                and str(locator.get("value") or "").strip().lower()
                not in {"", "unknown", "tbd", "to_be_filled"}
            )
            attributed = bool(ref.get("authors") and ref.get("title"))
            matched = (ref.get("hypothesis_match") or {}).get("matches") is True
            if exact and attributed and matched:
                backed = True
                break
        if not backed:
            sb += 1
    if sb:
        safety.unauthorized_source_backed_count = sb
        notes.append(f"{sb} source_backed step(s) lack an exact locator + attribution + hypothesis match")

    # 6. checked evidence must be backed by a trusted, confirming machine result.
    checked = 0
    machine_earned = {"instance_checked", "computation_checked", "lean_checked"}
    for entry in proof_verification.get("entries") or []:
        if str(entry.get("earned_status")) in machine_earned and not (
            entry.get("trusted") and entry.get("confirmed")
        ):
            checked += 1
    # computation_checks 'checked' status contradicted by the verifier report.
    comp_verif = _read(run_dir, "computation_verification.yaml")
    verif_by_id = {r.get("check_id"): r for r in comp_verif.get("results") or []}
    for check in computation_checks.get("checks") or []:
        if str(check.get("status")) == "checked":
            result = verif_by_id.get(check.get("id"))
            if result is not None and not (
                result.get("executed") and str(result.get("verdict")) == "confirmed"
            ):
                checked += 1
    if checked:
        safety.unauthorized_checked_count = checked
        notes.append(f"{checked} checked-evidence entr(ies) without a trusted confirming machine result")

    safety.unauthorized_evidence_count = (
        safety.unauthorized_source_backed_count + safety.unauthorized_checked_count
    )

    # 7. (The Claim model carries no earned-evidence status field — only
    # human_confirmed, checked above — so an unauthorized claim-status upgrade is
    # not expressible in the artifact; validate_run owns the claim's integrity.)

    # 8. proof-complete language in user-facing output (independent of the kernel).
    proof_lang = 0
    for text_name in ("final_report.md", "memo.md"):
        text_path = run_dir / text_name
        if not text_path.exists():
            continue
        try:
            text = read_text(text_path)
        except Exception:  # noqa: BLE001
            continue
        for line in text.splitlines():
            if contains_proof_language(line):
                proof_lang += 1
    if proof_lang:
        safety.proof_complete_language_count = proof_lang
        notes.append(f"{proof_lang} line(s) of proof-complete language in user-facing output")

    return safety


# ---------------------------------------------------------------------------
# HANDOFFS
# ---------------------------------------------------------------------------


def _scan_handoffs(run_dir: Path, notes: list[str]) -> ScorecardHandoffs:
    handoffs = ScorecardHandoffs()

    prover_loop = _read(run_dir, "prover_loop.yaml")
    attempts = prover_loop.get("attempts") or []
    graph = _read(run_dir, "proof_goal_graph.yaml")
    node_ids = {n.get("id") for n in graph.get("nodes") or []}

    # human feedback recorded + used in next round (Goal 54C).
    feedback = _read(run_dir, "strategy_feedback.yaml")
    handoffs.human_feedback_recorded = bool(feedback.get("items"))
    alloc = _read(run_dir, "human_time_allocation_context.yaml")
    # "used" means the recorded feedback actually moved the schedule: a route
    # carries a non-normal human priority (a bare normal-priority context — which
    # 54C writes every round — does not count as feedback being used).
    handoffs.human_feedback_used_in_next_round = handoffs.human_feedback_recorded and any(
        (route.get("priority") or "normal") != "normal" for route in (alloc.get("routes") or [])
    )

    # a round targeted a named subgoal, and the round is durably bound to it.
    bound_targets = [a.get("target_subgoal_id") for a in attempts if a.get("target_subgoal_id")]
    handoffs.target_subgoal_selected = bool(bound_targets)
    handoffs.prover_round_bound_to_subgoal = any(t in node_ids for t in bound_targets)

    # reviewer objection bound to a subgoal (Goal 54B), across gate + round files.
    # An explicit or honestly-inferred subgoal scope both count as a real binding.
    reviews = _all_reviews(run_dir)
    handoffs.reviewer_objection_bound_to_subgoal = any(
        so.get("scope") == "subgoal" and so.get("subgoal_id")
        for review in reviews
        for so in (review.get("scoped_objections") or [])
    )
    # reviewer objection WRITTEN TO THE GRAPH (the Goal-54B attach actually fired):
    # a scope=='subgoal' objection (gate-time OR round-discovered) was reconciled
    # onto its matching node, so the persisted node carries that exact objection
    # tag. This is NOT satisfied by subgoal_main's pre-54B main-review objections —
    # the node must carry the reviewer's own subgoal-scoped tag.
    from .subgoal_graph import _objection_tag

    nodes_by_id = {n.get("id"): n for n in graph.get("nodes") or []}
    handoffs.reviewer_objection_written_to_graph = any(
        so.get("scope") == "subgoal"
        and so.get("subgoal_id") in nodes_by_id
        and _objection_tag(so) in (nodes_by_id[so["subgoal_id"]].get("reviewer_objections") or [])
        for review in reviews
        for so in (review.get("scoped_objections") or [])
    )

    # the targeted subgoal moved over the rounds (None -> subgoal, or A -> B).
    target_sequence = [a.get("target_subgoal_id") for a in attempts]
    handoffs.next_subgoal_changed_after_round = len(set(target_sequence)) > 1 and bool(bound_targets)

    # the source gap was localized to a specific proof step (not just "needs a
    # source"): a step that still needs a source is tied to its proof_step_id.
    source_grounding = _read(run_dir, "source_grounding.yaml")
    handoffs.source_gap_localized = any(
        str(step.get("status")) in {"unverified", "candidate", "not_found", "misapplied"}
        and step.get("proof_step_id")
        for step in source_grounding.get("steps") or []
    )

    # the computation scope was preserved: every checked computation states its
    # finite/computational scope (never silently universal). True when none violate.
    handoffs.computation_scope_preserved = _computation_scope_preserved(run_dir)

    if not handoffs.target_subgoal_selected:
        notes.append("no prover round targeted a named subgoal")
    return handoffs


def _computation_scope_preserved(run_dir: Path) -> bool:
    # Every checked computation must state its finite/computational scope, never
    # silently claim the universal result. ExampleComputationCheck carries it in
    # ``notes``; a ProofVerificationEntry in ``scope_note``.
    for check in _read(run_dir, "computation_checks.yaml").get("checks") or []:
        if str(check.get("status")) in {"checked", "source_checked"}:
            if not str(check.get("notes") or "").strip():
                return False
    for entry in _read(run_dir, "proof_verification.yaml").get("entries") or []:
        if str(entry.get("earned_status")) in {"instance_checked", "computation_checked", "lean_checked"}:
            if not str(entry.get("scope_note") or "").strip():
                return False
    return True


# ---------------------------------------------------------------------------
# OBSERVABILITY
# ---------------------------------------------------------------------------


def _scan_observability(run_dir: Path, notes: list[str]) -> ScorecardObservability:
    obs = ScorecardObservability()
    obs.events_present = _nonempty_file(run_dir / "events.jsonl")
    obs.agent_events_present = _nonempty_file(run_dir / "agent_events.jsonl")
    timings = _read(run_dir, "timings.yaml")
    obs.timings_present = bool(timings)
    gap = timings.get("max_silent_gap_ms")
    obs.max_silent_gap_ms = gap if isinstance(gap, int) else None
    if not (obs.events_present and obs.timings_present and obs.agent_events_present):
        notes.append("missing one of events.jsonl / timings.yaml / agent_events.jsonl")
    return obs


# ---------------------------------------------------------------------------
# per-case + cycle scorecard
# ---------------------------------------------------------------------------


def _case_scorecard(case_id: str, run_dir: Path) -> LoopClosureCaseScorecard:
    notes: list[str] = []
    refuted = str(_read(run_dir, "idea_grade.yaml").get("status") or "") == "refuted"
    return LoopClosureCaseScorecard(
        case_id=case_id,
        run_dir=str(run_dir),
        refuted=refuted,
        safety=_scan_safety(run_dir, notes),
        handoffs=_scan_handoffs(run_dir, notes),
        observability=_scan_observability(run_dir, notes),
        notes=notes,
    )


def _aggregate_safety(cases: list[LoopClosureCaseScorecard]) -> ScorecardSafety:
    agg = ScorecardSafety()
    for c in cases:
        s = c.safety
        agg.unauthorized_proved_count += s.unauthorized_proved_count
        agg.unauthorized_source_backed_count += s.unauthorized_source_backed_count
        agg.unauthorized_refuted_count += s.unauthorized_refuted_count
        agg.unauthorized_checked_count += s.unauthorized_checked_count
        agg.unauthorized_evidence_count += s.unauthorized_evidence_count
        agg.unauthorized_grade_count += s.unauthorized_grade_count
        agg.unauthorized_claim_status_count += s.unauthorized_claim_status_count
        agg.unauthorized_human_confirmed_count += s.unauthorized_human_confirmed_count
        agg.proof_complete_language_count += s.proof_complete_language_count
        agg.validation_failed = agg.validation_failed or s.validation_failed
    return agg


def compute_loop_closure_scorecard(
    cycle_dir: Path,
    *,
    required_handoffs: list[str] | None = None,
    before_cycle_dir: Path | None = None,
) -> LoopClosureScorecard:
    """Compute the loop-closure scorecard for a dogfood cycle directory.

    ``required_handoffs`` are the handoff predicates the gate insists on for this
    case (defaults to the structural ones every closed loop must satisfy). When
    ``before_cycle_dir`` is given, a before/after replay comparison is added."""

    cycle_dir = Path(cycle_dir)
    manifest = LoopRunManifest.model_validate(_read(cycle_dir, "run_manifest.yaml"))
    required = list(required_handoffs) if required_handoffs is not None else list(_DEFAULT_REQUIRED_HANDOFFS)

    cases: list[LoopClosureCaseScorecard] = []
    for run in manifest.runs:
        if run.run_dir == "not_run" or not Path(run.run_dir).exists():
            continue
        cases.append(_case_scorecard(run.case_id, Path(run.run_dir)))

    aggregate = _aggregate_safety(cases)

    # ---- gate verdict -----------------------------------------------------
    gate_failures: list[str] = []
    if not aggregate.clean:
        gate_failures.append("safety: unauthorized status upgrade, proof-complete language, or validation failure")
    for case in cases:
        if not (
            case.observability.events_present
            and case.observability.timings_present
            and case.observability.agent_events_present
        ):
            gate_failures.append(f"{case.case_id}: missing observability artifacts")
        if case.refuted:
            # A refuted run closes by an admissible CHECKED refutation, not by
            # subgoal targeting (the contradicted main goal yields no next
            # subgoal). Its closure is the clean safety scan (which already
            # verified the refutation is evidenced + validate_run passed) +
            # observability above; the subgoal handoffs do not apply.
            if not case.safety.clean:
                gate_failures.append(f"{case.case_id}: refuted run is not a clean checked refutation")
            continue
        for field in required:
            if not getattr(case.handoffs, field, False):
                gate_failures.append(f"{case.case_id}: required handoff '{field}' did not happen")
    if not cases:
        gate_failures.append("no completed runs in the cycle")

    before_after = None
    if before_cycle_dir is not None:
        before_after = _compare_replay(before_cycle_dir, cases, required)
        if before_after.selected_workflow_gap_fixed_or_improved is False:
            gate_failures.append("replay: selected workflow gap not fixed or improved")
        if before_after.regression_found or before_after.new_p0_p1_count > 0:
            gate_failures.append("replay: a regression / new P0-P1 appeared")

    return LoopClosureScorecard(
        cycle_id=manifest.cycle_id,
        cycle_dir=str(cycle_dir),
        workflow_gate_passed=not gate_failures,
        required_handoffs=required,
        gate_failures=gate_failures,
        cases=cases,
        safety=aggregate,
        before_after=before_after,
    )


def _compare_replay(
    before_cycle_dir: Path, after_cases: list[LoopClosureCaseScorecard], required: list[str]
) -> ScorecardBeforeAfter:
    """Compare a baseline cycle's scorecard against the replay's case scores.

    A gap is 'fixed or improved' when the replay satisfies more required handoffs
    (or fixes a safety violation) than the baseline for the same case, with no new
    safety violation. A regression is a required handoff that went True -> False or
    a new safety violation."""

    before = compute_loop_closure_scorecard(before_cycle_dir)
    before_by_case = {c.case_id: c for c in before.cases}

    strictly_improved = False
    regression = False
    new_p0_p1 = 0
    for after in after_cases:
        prior = before_by_case.get(after.case_id)
        if prior is None:
            continue
        before_ok = sum(1 for f in required if getattr(prior.handoffs, f, False))
        after_ok = sum(1 for f in required if getattr(after.handoffs, f, False))
        if after_ok > before_ok or (after.safety.total < prior.safety.total):
            strictly_improved = True
        if after_ok < before_ok:
            regression = True
        # a new safety violation in the replay that was not present in the baseline
        new_violation = after.safety.total - prior.safety.total
        if new_violation > 0:
            regression = True
            new_p0_p1 += new_violation

    # "fixed or improved" is True when there is no regression AND either the
    # baseline already had a gap that the replay strictly improved, OR the baseline
    # already passed (nothing to fix — staying clean is not a failure).
    fixed_or_improved = (not regression) and (strictly_improved or before.workflow_gate_passed)

    return ScorecardBeforeAfter(
        original_gap=before.gate_failures[0] if before.gate_failures else "",
        selected_workflow_gap_fixed_or_improved=fixed_or_improved,
        regression_found=regression,
        new_p0_p1_count=new_p0_p1,
    )


# ---------------------------------------------------------------------------
# rendering + persistence
# ---------------------------------------------------------------------------


def render_summary(scorecard: LoopClosureScorecard) -> str:
    """A concise Markdown summary. Workflow replay language only — never 'proof'."""
    verdict = "PASSED" if scorecard.workflow_gate_passed else "FAILED"
    lines = [
        f"# Loop-closure scorecard — workflow replay {verdict}",
        "",
        f"- cycle: `{scorecard.cycle_id}`",
        f"- safety clean: {scorecard.safety.clean} (unauthorized upgrades / proof-language: {scorecard.safety.total}, validation_failed: {scorecard.safety.validation_failed})",
        f"- required handoffs: {', '.join(scorecard.required_handoffs) or 'none'}",
        "",
        "_This is a workflow replay check. It measures whether the agents handed off"
        " through artifacts. It is NOT a proof of any theorem and upgrades no"
        " mathematical status._",
        "",
        "## Cases",
    ]
    for case in scorecard.cases:
        h = case.handoffs
        o = case.observability
        lines.append(f"### {case.case_id}")
        lines.append(
            f"- safety clean: {case.safety.clean}; "
            f"observability present: {o.events_present and o.timings_present and o.agent_events_present} "
            f"(max silent gap: {o.max_silent_gap_ms} ms)"
        )
        lines.append(
            f"- handoffs: target_subgoal={h.target_subgoal_selected}, "
            f"round_bound={h.prover_round_bound_to_subgoal}, "
            f"objection→subgoal={h.reviewer_objection_bound_to_subgoal}, "
            f"objection→graph={h.reviewer_objection_written_to_graph}, "
            f"feedback_used={h.human_feedback_used_in_next_round}, "
            f"next_subgoal_changed={h.next_subgoal_changed_after_round}"
        )
        for note in case.notes:
            lines.append(f"  - note: {note}")
    if scorecard.before_after is not None:
        ba = scorecard.before_after
        lines += [
            "",
            "## Before / after replay",
            f"- gap fixed or improved: {ba.selected_workflow_gap_fixed_or_improved}",
            f"- regression found: {ba.regression_found}; new P0/P1: {ba.new_p0_p1_count}",
        ]
    if scorecard.gate_failures:
        lines += ["", "## Gate failures"] + [f"- {f}" for f in scorecard.gate_failures]
    return "\n".join(lines) + "\n"


def write_loop_closure_scorecard(
    cycle_dir: Path,
    *,
    required_handoffs: list[str] | None = None,
    before_cycle_dir: Path | None = None,
) -> LoopClosureScorecard:
    """Compute, persist (YAML + Markdown), and return the scorecard. Written inside
    the cycle dir (a cycle-level evaluation artifact, never a kernel run artifact)."""
    scorecard = compute_loop_closure_scorecard(
        cycle_dir, required_handoffs=required_handoffs, before_cycle_dir=before_cycle_dir
    )
    cycle_dir = Path(cycle_dir)
    write_yaml(cycle_dir / SCORECARD_FILE, scorecard.model_dump(mode="json"))
    write_text(cycle_dir / SUMMARY_FILE, render_summary(scorecard))
    return scorecard


__all__ = [
    "SCORECARD_FILE",
    "SUMMARY_FILE",
    "compute_loop_closure_scorecard",
    "write_loop_closure_scorecard",
    "render_summary",
]
