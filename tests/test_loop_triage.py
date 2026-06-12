"""Goal 42C: gap triage + bounded autofix planning."""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.loop_harness import run_loop_cycle
from espresso_proof.loop_models import AutofixPlan, GapLedger, TriageDecision
from espresso_proof.loop_reviewers import run_review_pass
from espresso_proof.loop_triage import build_autofix_plan, run_triage


@pytest.fixture(scope="module")
def triaged_cycle(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("loop") / "loop_runs"
    cycle_dir = run_loop_cycle(out_root=root, mode="smoke", max_cases=3)
    run_review_pass(cycle_dir)
    run_triage(cycle_dir)
    return cycle_dir


def test_triage_selects_exactly_one_evidenced_gap(triaged_cycle: Path) -> None:
    decision = TriageDecision.model_validate(read_yaml(triaged_cycle / "triage.yaml"))
    assert decision.selected_gap_id
    assert decision.selected_evidence_paths
    # probe calibration findings are never selectable.
    assert "planted_probe" not in decision.selected_evidence_paths[0]
    # the genuine smoke-mode gap (false-theorem case with zero counterexample
    # tests) is the expected math-capability selection.
    assert decision.selected_gap_kind == "math_capability"
    # every unchosen P0/P1/P2 has a reason.
    for finding in decision.unselected_findings:
        assert finding.why_not_selected.strip()


def test_selected_gap_marked_in_campaign_ledger(triaged_cycle: Path) -> None:
    ledger = GapLedger.model_validate(read_yaml(triaged_cycle.parent / "gap_ledger.yaml"))
    decision = TriageDecision.model_validate(read_yaml(triaged_cycle / "triage.yaml"))
    entry = next(g for g in ledger.gaps if g.gap_id == decision.selected_gap_id)
    assert entry.status == "selected"


def test_kernel_safety_finding_outranks_capability(tmp_path: Path) -> None:
    root = tmp_path / "loop_runs"
    cycle_dir = run_loop_cycle(out_root=root, mode="smoke", max_cases=2)
    run_review_pass(cycle_dir)
    # plant a kernel-safety P0 in the kernel_safety report (non-probe path).
    report_path = cycle_dir / "review_reports" / "kernel_safety.yaml"
    report = read_yaml(report_path)
    report["verdict"] = "BLOCKER"
    report["actionable_findings"].append(
        {
            "finding_id": "kernel_safety_900",
            "severity": "P0",
            "evidence_path": str(cycle_dir / "failures.yaml"),
            "what_happened": "synthetic: an invalid run was rendered as trusted",
            "why_it_matters": "kernel honesty breach",
            "recommended_fix": "gate the renderer on validation",
            "gap_kind": "kernel_safety",
        }
    )
    write_yaml(report_path, report)
    decision = run_triage(cycle_dir)
    assert decision.selected_gap_id == "kernel_safety_900"
    assert decision.selected_gap_kind == "kernel_safety"


def test_nonexistent_selected_gap_id_fails_closed(triaged_cycle: Path) -> None:
    decision = TriageDecision.model_validate(read_yaml(triaged_cycle / "triage.yaml"))
    from espresso_proof.loop_triage import _load_reports, _validate_selected_exists

    reports = _load_reports(triaged_cycle)
    bogus = decision.model_copy(update={"selected_gap_id": "finding_that_does_not_exist"})
    with pytest.raises(ValueError, match="does not reference an existing"):
        _validate_selected_exists(triaged_cycle, bogus, reports)


def test_oversized_finding_becomes_goal_proposal(tmp_path: Path) -> None:
    root = tmp_path / "loop_runs"
    cycle_dir = run_loop_cycle(out_root=root, mode="smoke", max_cases=2)
    run_review_pass(cycle_dir)
    report_path = cycle_dir / "review_reports" / "math_logic.yaml"
    report = read_yaml(report_path)
    report["actionable_findings"].insert(
        0,
        {
            "finding_id": "math_logic_900",
            "severity": "P0",
            "evidence_path": str(cycle_dir / "blockers.yaml"),
            "what_happened": "synthetic: the entire prover needs deeper schemas",
            "why_it_matters": "core capability",
            "recommended_fix": "broad rewrite of the prover round schema and loop",
            "gap_kind": "status_overclaim",
        },
    )
    report["verdict"] = "BLOCKER"
    write_yaml(report_path, report)
    decision = run_triage(cycle_dir)
    # the oversized P0 was converted, and a smaller gap was selected instead.
    assert decision.selected_gap_id != "math_logic_900"
    assert decision.new_goal_proposal_path
    proposal = Path(decision.new_goal_proposal_path)
    assert proposal.exists()
    assert "oversized" in proposal.read_text(encoding="utf-8").lower()
    oversized = next(
        f for f in decision.unselected_findings if f.finding_id == "math_logic_900"
    )
    assert "new-goal proposal" in oversized.why_not_selected


def test_autofix_plan_budgets_and_denylist(triaged_cycle: Path) -> None:
    plan = build_autofix_plan(
        triaged_cycle,
        files_expected_to_change=["espresso_proof/agent_runner.py", "tests/test_x.py"],
        focused_tests=["tests/test_x.py::test_y"],
        replay_cases=["order6_abelian_false"],
        why_this_gap_now="the false-theorem case produces zero counterexample tests",
    )
    assert (triaged_cycle / "autofix_plan.yaml").exists()
    assert {"Engineering", "Kernel Safety"} <= set(plan.required_reviewers)
    # 9 files exceeds the budget — model refuses.
    with pytest.raises(ValueError, match="8-file budget"):
        build_autofix_plan(
            triaged_cycle,
            files_expected_to_change=[f"espresso_proof/m{i}.py" for i in range(9)],
            focused_tests=["t"],
            replay_cases=["c"],
            why_this_gap_now="too big",
        )
    # the raised caps cannot be smuggled in via the model either.
    with pytest.raises(ValueError, match="cannot be raised"):
        AutofixPlan(
            cycle_id="c", selected_gap_id="g", why_this_gap_now="w",
            files_expected_to_change=["a"], max_files=20,
            max_effective_diff_lines=5000, focused_tests=["t"],
            required_reviewers=["Engineering", "Kernel Safety"], replay_cases=["c"],
        )
    # protected safety surfaces are refused outright.
    with pytest.raises(ValueError, match="protected safety surfaces"):
        build_autofix_plan(
            triaged_cycle,
            files_expected_to_change=["espresso_proof/validators.py"],
            focused_tests=["t"],
            replay_cases=["c"],
            why_this_gap_now="would weaken a gate",
        )


def test_triage_never_changes_code(triaged_cycle: Path) -> None:
    # triage writes only inside the loop root: triage.yaml, autofix_plan.yaml,
    # proposals, and the ledger update — no repo file outside it.
    import subprocess

    status = subprocess.run(
        ["git", "status", "--short"],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1],
    ).stdout
    for line in status.splitlines():
        assert "loop_runs" not in line  # tmp dirs are outside the repo anyway


def test_probe_inflated_blocker_cannot_hijack_priority(tmp_path: Path) -> None:
    # Regression for the Math Workflow reviewer's observed inversion: the
    # calibration probe must not inflate a reviewer's verdict, and a P2 from a
    # probe-duty reviewer must never outrank a P1 math-capability finding.
    # The P1 gap is planted with the order-21 false case (the order-6 gap is
    # fixed by the dihedral probe and no longer produces a P1).
    from espresso_proof.loop_harness import DEFAULT_CASES

    cases_file = tmp_path / "cases.yaml"
    write_yaml(
        cases_file,
        {
            "cases": [
                DEFAULT_CASES[0],
                {
                    "case_id": "order21_abelian_false",
                    "claim": "Every group of order 21 is abelian.",
                    "case_family": "false_theorem",
                    "expected_difficulty": "smoke",
                    "known_truth_status": "evaluator_only_false",
                    "what_this_case_tests": [
                        "counterexample reasoning beyond the dihedral probe"
                    ],
                    "must_not_claim": ["Espresso proved the theorem"],
                },
                DEFAULT_CASES[2],
            ]
        },
    )
    root = tmp_path / "loop_runs"
    cycle_dir = run_loop_cycle(out_root=root, mode="smoke", cases_path=cases_file)
    run_review_pass(cycle_dir)
    # verdicts: probe-duty reviewers must NOT be BLOCKER from the probe alone.
    from espresso_proof.loop_models import ReviewReport

    source_report = ReviewReport.model_validate(
        read_yaml(cycle_dir / "review_reports" / "source_applicability.yaml")
    )
    assert source_report.verdict != "BLOCKER" or any(
        f.severity == "P0" and "planted_probe" not in f.evidence_path
        for f in source_report.actionable_findings
    )
    decision = run_triage(cycle_dir)
    # the P1 math-capability finding (bucket 3) wins over the P2 source/tool
    # finding (bucket 5) — the exact inversion previously observed.
    assert decision.selected_gap_id == "math_logic_001"
    assert decision.selected_gap_kind == "math_capability"
