"""Goal 42B: six-reviewer dogfood review pass."""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.loop_harness import DEFAULT_CASES, run_loop_cycle
from espresso_proof.loop_models import ActionableFinding, ReviewReport
from espresso_proof.loop_reviewers import run_review_pass

REVIEWERS = (
    "math_logic", "source_applicability", "counterexample",
    "proof_search", "kernel_safety", "product_ux",
)

# The order-6 gap was fixed by the deterministic dihedral probe (cycle 1
# autofix), so detection-logic tests pin against a false-theorem case still
# beyond the probe's scope: order 21 is odd (no dihedral group), yet
# Z_7 x| Z_3 is non-abelian, so the claim is false and the smoke pipeline
# still yields zero counterexample tests for it.
ORDER21_GAP_CASE = {
    "case_id": "order21_abelian_false",
    "claim": "Every group of order 21 is abelian.",
    "case_family": "false_theorem",
    "expected_difficulty": "smoke",
    "known_truth_status": "evaluator_only_false",
    "what_this_case_tests": ["counterexample reasoning beyond the dihedral probe"],
    "must_not_claim": [
        "Espresso proved the theorem",
        "refuted without a checked counterexample",
    ],
}
GAP_CASES = {"cases": [DEFAULT_CASES[0], ORDER21_GAP_CASE, DEFAULT_CASES[2]]}


@pytest.fixture(scope="module")
def reviewed_cycle(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("loop") / "loop_runs"
    cycle_dir = run_loop_cycle(out_root=root, mode="smoke", max_cases=3)
    run_review_pass(cycle_dir)
    return cycle_dir


def test_six_reports_in_required_schema(reviewed_cycle: Path) -> None:
    reports_dir = reviewed_cycle / "review_reports"
    for name in REVIEWERS:
        report = ReviewReport.model_validate(read_yaml(reports_dir / f"{name}.yaml"))
        assert report.reviewer == name
        assert report.input_artifacts
        assert report.reviewer_model_or_mode == "deterministic_artifact_checks_v1"
        # every actionable finding carries an evidence path (model-enforced).
        for finding in report.actionable_findings:
            assert finding.evidence_path.strip()
        # forbidden context verified: truth labels never leaked into runs.
        assert report.forbidden_context_checked.known_truth_status_hidden_from_espresso_prompt


def test_planted_probe_caught_and_recorded(reviewed_cycle: Path) -> None:
    calibration = read_yaml(reviewed_cycle / "review_reports" / "reviewer_calibration.yaml")
    assert calibration["caught_by"] == {"source_applicability": True, "kernel_safety": True}
    assert calibration["all_duty_reviewers_caught"] is True
    # probe artifacts exist, are clearly marked, and live OUTSIDE the run dirs.
    probe_memo = reviewed_cycle / "planted_probe" / "probe_memo.md"
    assert "PLANTED PROBE" in probe_memo.read_text(encoding="utf-8")
    assert "planted_probe" not in str(list((reviewed_cycle / "runs").rglob("probe_*")))


def test_unevidenced_finding_is_rejected_by_schema() -> None:
    with pytest.raises(ValueError, match="evidence path"):
        ActionableFinding(
            finding_id="x", severity="P1", evidence_path="   ",
            what_happened="vibes", why_it_matters="vibes", recommended_fix="vibes",
        )


def test_blocker_verdict_requires_actionable_finding() -> None:
    with pytest.raises(ValueError, match="BLOCKER verdict requires"):
        ReviewReport(
            reviewer="math_logic", verdict="BLOCKER", input_artifacts=["x"],
            forbidden_context_checked={"known_truth_status_hidden_from_espresso_prompt": True},
            reviewer_model_or_mode="deterministic_artifact_checks_v1",
        )


def test_review_pass_is_read_only_over_cycle_records(tmp_path: Path) -> None:
    root = tmp_path / "loop_runs"
    cycle_dir = run_loop_cycle(out_root=root, mode="smoke", max_cases=2)
    before = {
        p: p.read_bytes()
        for p in cycle_dir.rglob("*")
        if p.is_file()
    }
    run_review_pass(cycle_dir)
    after_new = {
        p for p in cycle_dir.rglob("*")
        if p.is_file() and p not in before
    }
    # only review_reports/ and the marked probe were added; nothing mutated.
    for path in after_new:
        assert "review_reports" in path.parts or "planted_probe" in path.parts, path
    for path, payload in before.items():
        assert path.read_bytes() == payload, f"review pass mutated {path}"


def test_truth_label_leak_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "loop_runs"
    cycle_dir = run_loop_cycle(out_root=root, mode="smoke", max_cases=2)
    # simulate a leak: an Espresso-side file carrying the evaluator label.
    leak = next((cycle_dir / "runs").rglob("input.md"))
    leak.write_text(
        leak.read_text(encoding="utf-8") + "\nknown_truth_status: evaluator_only_false\n",
        encoding="utf-8",
    )
    run_review_pass(cycle_dir)
    for name in REVIEWERS:
        report = ReviewReport.model_validate(
            read_yaml(cycle_dir / "review_reports" / f"{name}.yaml")
        )
        assert (
            report.forbidden_context_checked.known_truth_status_hidden_from_espresso_prompt
            is False
        )


def test_status_overclaim_yields_math_logic_blocker(tmp_path: Path) -> None:
    root = tmp_path / "loop_runs"
    cycle_dir = run_loop_cycle(out_root=root, mode="smoke", max_cases=2)
    # plant an overclaim in the RECORD: grade allowed refuted while the
    # counterexample stayed unchecked.
    refutations = read_yaml(cycle_dir / "refutations.yaml")
    refutations["refutations"].append(
        {
            "case_id": "order6_abelian_false",
            "run_id": "synthetic",
            "claimed_by": "synthetic",
            "candidate_counterexample": "S3",
            "satisfies_all_locked_hypotheses": True,
            "conclusion_fails": None,
            "counterexample_checked": False,
            "espresso_allowed_refuted": True,
            "failure_class": "computation_unchecked",
            "is_proof_step_attack_only": False,
        }
    )
    write_yaml(cycle_dir / "refutations.yaml", refutations)
    run_review_pass(cycle_dir)
    report = ReviewReport.model_validate(
        read_yaml(cycle_dir / "review_reports" / "math_logic.yaml")
    )
    assert report.verdict == "BLOCKER"
    assert any(
        f.severity == "P0" and f.gap_kind == "status_overclaim"
        for f in report.actionable_findings
    )


def test_false_theorem_capability_gap_is_found(tmp_path: Path) -> None:
    # A false-theorem case whose counterexample bank stays empty must surface
    # as an evidenced math-capability finding (this is the loop's purpose).
    # order6 is now covered by the dihedral probe, so the gap is planted with
    # the still-uncovered order-21 case.
    cases_file = tmp_path / "cases.yaml"
    write_yaml(cases_file, GAP_CASES)
    cycle_dir = run_loop_cycle(
        out_root=tmp_path / "loop_runs", mode="smoke", cases_path=cases_file
    )
    run_review_pass(cycle_dir)
    report = ReviewReport.model_validate(
        read_yaml(cycle_dir / "review_reports" / "math_logic.yaml")
    )
    assert any(
        f.severity == "P1"
        and f.gap_kind == "math_capability"
        and "order21_abelian_false" in f.what_happened
        for f in report.actionable_findings
    )


def test_fixed_false_theorem_case_no_longer_flagged(reviewed_cycle: Path) -> None:
    # After the dihedral-probe fix, the canonical order-6 false case carries a
    # checked counterexample, so the math reviewer must NOT report the
    # zero-counterexample gap for it anymore.
    report = ReviewReport.model_validate(
        read_yaml(reviewed_cycle / "review_reports" / "math_logic.yaml")
    )
    assert not any(
        "order6_abelian_false" in f.what_happened
        and "zero counterexample tests" in f.what_happened
        for f in report.actionable_findings
    )
