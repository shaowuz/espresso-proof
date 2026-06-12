"""Goal 42C: gap triage + bounded autofix planning.

``run_triage`` aggregates the six review reports plus the cycle's blockers,
bottlenecks, and the campaign gap ledger, and chooses EXACTLY ONE evidenced
gap by the Goal 42 priority order:

1. kernel safety P0/P1;
2. proof/source/refutation/status overclaim;
3. math-capability blocker (proof-search, counterexample, source
   applicability, subgoal progress);
4. user-blocking UX P0/P1 (only when it blocks mathematical workflow);
5. source/tool capability;
6. regression/scorecard coverage gap;
7. polish.

Hard rules implemented here:

- a Math Logic, Source Applicability, or Counterexample BLOCKER always enters
  triage and cannot be out-voted by other reviewers;
- planted calibration-probe findings are evaluator material and are excluded
  from selection;
- the selected gap must reference an existing reviewer finding (fail closed
  otherwise), and every unchosen P0/P1/P2 finding gets a ``why_not_selected``;
- findings whose recommended fix is a broad rewrite are converted into a
  new-goal proposal instead of an autofix, and the next candidate is selected;
- ``build_autofix_plan`` validates the 8-file / 800-line budgets and mandatory
  reviewers via the AutofixPlan model and refuses oversized plans.

Triage never changes code; it writes only inside the cycle folder and updates
the campaign gap ledger's status fields.
"""

from __future__ import annotations

from pathlib import Path

from .io import read_yaml, write_text, write_yaml
from .loop_models import (
    ActionableFinding,
    AutofixPlan,
    BlockerReport,
    BottleneckReport,
    GapLedger,
    GapLedgerEntry,
    ReviewReport,
    TriageDecision,
    UnselectedFinding,
)

_REVIEWER_FILES = (
    "math_logic", "source_applicability", "counterexample",
    "proof_search", "kernel_safety", "product_ux",
)

# Math-side reviewers whose BLOCKER cannot be overridden by majority.
_NON_OVERRIDABLE = {"math_logic", "source_applicability", "counterexample"}

_BROAD_FIX_MARKERS = ("broad rewrite", "redesign", "rewrite the", "rearchitect")

_SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _priority_bucket(reviewer: str, finding: ActionableFinding) -> int:
    if finding.gap_kind == "kernel_safety" and finding.severity in {"P0", "P1"}:
        return 1
    if finding.gap_kind == "status_overclaim":
        return 2
    if finding.gap_kind == "math_capability":
        return 3
    if (
        finding.gap_kind == "product_workflow"
        and finding.severity in {"P0", "P1"}
        and "block" in (finding.why_it_matters + finding.what_happened).lower()
    ):
        return 4
    if finding.gap_kind == "source_tool_capability":
        return 5
    if finding.gap_kind == "coverage_gap":
        return 6
    return 7


def _is_probe_finding(finding: ActionableFinding) -> bool:
    return "planted_probe" in finding.evidence_path


def _is_oversized(finding: ActionableFinding) -> bool:
    text = finding.recommended_fix.lower()
    return any(marker in text for marker in _BROAD_FIX_MARKERS)


def _load_reports(cycle_dir: Path) -> dict[str, ReviewReport]:
    reports = {}
    for name in _REVIEWER_FILES:
        path = cycle_dir / "review_reports" / f"{name}.yaml"
        if not path.exists():
            raise FileNotFoundError(
                f"missing review report {name}; run the six-reviewer pass first"
            )
        reports[name] = ReviewReport.model_validate(read_yaml(path))
    return reports


def _gap_kind_for_triage(finding: ActionableFinding) -> str:
    if finding.gap_kind in {"kernel_safety", "status_overclaim"}:
        return "kernel_safety"
    if finding.gap_kind in {"math_capability", "source_tool_capability"}:
        return "math_capability"
    if finding.gap_kind == "product_workflow":
        return "product_workflow"
    return "harness_gap"


def run_triage(cycle_dir: Path) -> TriageDecision:
    """Aggregate findings and choose exactly one evidenced gap. Writes
    triage.yaml (and new-goal proposals for oversized findings); updates the
    campaign gap ledger. Never changes code."""

    cycle_dir = Path(cycle_dir)
    cycle_id = cycle_dir.name
    reports = _load_reports(cycle_dir)

    candidates: list[tuple[str, ActionableFinding]] = []
    for name, report in reports.items():
        for finding in report.actionable_findings:
            if _is_probe_finding(finding):
                continue  # calibration material, never a fix target
            candidates.append((name, finding))
    if not candidates:
        raise ValueError(
            "no evidenced actionable findings: a cycle without real P0/P1/P2 "
            "gaps must improve the harness instead (Goal 42 five-cycle rule)"
        )

    # Non-overridable math-side BLOCKERs constrain the candidate pool unless a
    # kernel-safety P0/P1 outranks them in the priority order.
    blocker_pool = [
        (name, finding)
        for name, finding in candidates
        if reports[name].verdict == "BLOCKER" and name in _NON_OVERRIDABLE
    ]
    kernel_pool = [
        (name, finding)
        for name, finding in candidates
        if _priority_bucket(name, finding) == 1
    ]
    pool = kernel_pool or blocker_pool or candidates

    def sort_key(item):
        name, finding = item
        return (
            _priority_bucket(name, finding),
            _SEVERITY_RANK.get(finding.severity, 9),
            finding.finding_id,
        )

    ordered = sorted(pool, key=sort_key)

    # Oversized findings become new-goal proposals; selection moves on.
    proposals_dir = cycle_dir / "new_goal_proposals"
    proposal_path: str | None = None
    selected_pair = None
    for name, finding in ordered:
        if _is_oversized(finding):
            proposals_dir.mkdir(exist_ok=True)
            proposal = proposals_dir / f"{finding.finding_id}.md"
            write_text(
                proposal,
                "# New goal proposal (oversized for a loop autofix)\n\n"
                f"Source finding: {finding.finding_id} ({finding.severity})\n\n"
                f"Evidence: {finding.evidence_path}\n\n"
                f"What happened: {finding.what_happened}\n\n"
                f"Why it matters: {finding.why_it_matters}\n\n"
                f"Recommended direction: {finding.recommended_fix}\n\n"
                "This exceeds the one-cycle autofix budget (8 files / 800 lines) "
                "and must become a dedicated goal file instead of a broad fix.\n",
            )
            proposal_path = str(proposal)
            continue
        selected_pair = (name, finding)
        break
    if selected_pair is None:
        raise ValueError(
            "every candidate finding is oversized; new-goal proposals were "
            "written and no autofix is allowed this cycle"
        )

    selected_reviewer, selected = selected_pair
    unselected = [
        UnselectedFinding(
            finding_id=finding.finding_id,
            severity=finding.severity,
            why_not_selected=(
                "converted to a new-goal proposal (oversized)"
                if _is_oversized(finding)
                else f"lower triage priority than {selected.finding_id} "
                f"(bucket {_priority_bucket(name, finding)} vs "
                f"{_priority_bucket(selected_reviewer, selected)}, "
                f"severity {finding.severity} vs {selected.severity})"
            ),
        )
        for name, finding in sorted(candidates, key=sort_key)
        if finding.finding_id != selected.finding_id
        and finding.severity in {"P0", "P1", "P2"}
    ]

    decision = TriageDecision(
        cycle_id=cycle_id,
        selected_gap_id=selected.finding_id,
        selected_gap_kind=_gap_kind_for_triage(selected),
        selected_evidence_paths=[selected.evidence_path],
        selection_rationale=(
            f"highest-priority evidenced finding (reviewer={selected_reviewer}, "
            f"bucket={_priority_bucket(selected_reviewer, selected)}, "
            f"severity={selected.severity}): {selected.what_happened[:200]}"
        ),
        unselected_findings=unselected,
        new_goal_proposal_path=proposal_path,
    )
    _validate_selected_exists(cycle_dir, decision, reports)
    write_yaml(cycle_dir / "triage.yaml", decision.model_dump(mode="json"))
    _update_ledger_for_selection(cycle_dir, decision, selected)
    return decision


def _validate_selected_exists(
    cycle_dir: Path, decision: TriageDecision, reports: dict[str, ReviewReport]
) -> None:
    known_ids = {
        finding.finding_id
        for report in reports.values()
        for finding in report.actionable_findings
    }
    blockers = BlockerReport.model_validate(read_yaml(cycle_dir / "blockers.yaml"))
    known_ids.update(b.recurrence_fingerprint for b in blockers.blockers)
    bottlenecks = BottleneckReport.model_validate(
        read_yaml(cycle_dir / "bottleneck_report.yaml")
    )
    known_ids.update(b.bottleneck_id for b in bottlenecks.bottlenecks)
    ledger_path = cycle_dir.parent / "gap_ledger.yaml"
    if ledger_path.exists():
        ledger = GapLedger.model_validate(read_yaml(ledger_path))
        known_ids.update(g.gap_id for g in ledger.gaps)
    if decision.selected_gap_id not in known_ids:
        raise ValueError(
            f"selected_gap_id {decision.selected_gap_id!r} does not reference an "
            "existing reviewer finding, blocker, bottleneck, or ledger entry"
        )


def _update_ledger_for_selection(
    cycle_dir: Path, decision: TriageDecision, selected: ActionableFinding
) -> None:
    ledger_path = cycle_dir.parent / "gap_ledger.yaml"
    if ledger_path.exists():
        ledger = GapLedger.model_validate(read_yaml(ledger_path))
    else:
        ledger = GapLedger(campaign_id=cycle_dir.parent.name or "campaign", gaps=[])
    entry = next((g for g in ledger.gaps if g.gap_id == decision.selected_gap_id), None)
    if entry is None:
        entry = GapLedgerEntry(
            gap_id=decision.selected_gap_id,
            first_seen_cycle=decision.cycle_id,
            linked_finding_ids=[decision.selected_gap_id],
            before_metric=selected.what_happened[:160],
        )
        ledger.gaps.append(entry)
    entry.status = "selected"
    entry.selection_note = decision.selection_rationale[:240]
    if decision.new_goal_proposal_path:
        # mark oversized companions as converted in the ledger when present.
        for gap in ledger.gaps:
            if gap.gap_id != entry.gap_id and gap.status == "selected":
                gap.status = "open"
    write_yaml(ledger_path, ledger.model_dump(mode="json"))


def build_autofix_plan(
    cycle_dir: Path,
    *,
    files_expected_to_change: list[str],
    focused_tests: list[str],
    replay_cases: list[str],
    why_this_gap_now: str,
    extra_reviewers: list[str] | None = None,
) -> AutofixPlan:
    """Write the bounded autofix plan for the cycle's selected gap. The
    AutofixPlan model enforces the 8-file budget, the un-raisable 800-line
    cap, mandatory Engineering + Kernel Safety reviewers, and replay cases."""

    cycle_dir = Path(cycle_dir)
    triage_path = cycle_dir / "triage.yaml"
    if not triage_path.exists():
        raise FileNotFoundError("run triage before planning an autofix")
    decision = TriageDecision.model_validate(read_yaml(triage_path))
    denylisted = _denylisted_paths(files_expected_to_change)
    if denylisted:
        raise ValueError(
            "autofix plan touches protected safety surfaces "
            f"({', '.join(denylisted)}); weakening validators/gates is forbidden — "
            "write a new goal proposal instead"
        )
    plan = AutofixPlan(
        cycle_id=decision.cycle_id,
        selected_gap_id=decision.selected_gap_id,
        why_this_gap_now=why_this_gap_now,
        files_expected_to_change=files_expected_to_change,
        focused_tests=focused_tests,
        required_reviewers=sorted({"Engineering", "Kernel Safety", *(extra_reviewers or [])}),
        replay_cases=replay_cases,
    )
    write_yaml(cycle_dir / "autofix_plan.yaml", plan.model_dump(mode="json"))
    return plan


# Paths an autofix may never weaken. Touching them is allowed only through a
# dedicated goal (not a loop autofix), so the planner fails closed here.
_PROTECTED_PREFIXES = (
    "espresso_proof/validators.py",
    "espresso_proof/validation/",
    "espresso_proof/proof_language.py",
    "schemas/",
)


def _denylisted_paths(paths: list[str]) -> list[str]:
    return [
        path
        for path in paths
        if any(path.startswith(prefix) for prefix in _PROTECTED_PREFIXES)
    ]


__all__ = ["run_triage", "build_autofix_plan"]
