"""Goal 54D: loop-closure scorecard + replay gate.

The scorecard turns Goals 54A-54C into a machine-checkable acceptance gate. These
tests pin: it PASSES on a real closed loop (a p^2-style run + a false-claim run);
it produces YAML + Markdown; and it FAILS closed on planted handoff/safety/
observability failures and on a replay that introduces a regression. Passing the
scorecard is a workflow replay pass — never a proof, never a status upgrade.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from espresso_proof.kernel.io import read_text, read_yaml, write_text, write_yaml
from espresso_proof.evaluation.loop_closure_scorecard import (
    SCORECARD_FILE,
    SUMMARY_FILE,
    compute_loop_closure_scorecard,
    render_summary,
    write_loop_closure_scorecard,
)
from espresso_proof.loop.loop_harness import run_loop_cycle
from espresso_proof.kernel.loop_models import LoopClosureScorecard


@pytest.fixture(scope="module")
def smoke_cycle(tmp_path_factory) -> Path:
    """One real deterministic dogfood cycle (p^2-true + order6-false). Built once;
    tests copy + mutate it in isolation."""
    out_root = tmp_path_factory.mktemp("g54d_loop")
    return run_loop_cycle(out_root=out_root, mode="smoke", max_cases=2)


def _copy_cycle(src: Path, dst: Path) -> Path:
    """Copy a cycle dir and rewrite the run_manifest run_dir paths into the copy
    so mutating the copy's runs is isolated from the shared fixture."""
    shutil.copytree(src, dst)
    manifest_path = dst / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    for run in manifest["runs"]:
        if run["run_dir"] != "not_run":
            run["run_dir"] = run["run_dir"].replace(str(src), str(dst), 1)
    write_yaml(manifest_path, manifest)
    return dst


def _run_dirs(cycle: Path) -> list[Path]:
    manifest = read_yaml(cycle / "run_manifest.yaml")
    return [Path(r["run_dir"]) for r in manifest["runs"] if r["run_dir"] != "not_run"]


# -- positive: a real closed loop passes --------------------------------------


def test_scorecard_passes_on_real_closed_loop(smoke_cycle: Path) -> None:
    sc = compute_loop_closure_scorecard(smoke_cycle)
    assert sc.workflow_gate_passed, sc.gate_failures
    assert sc.safety.clean and sc.safety.total == 0
    assert len(sc.cases) == 2  # p^2-true + order6-false
    case_ids = {c.case_id for c in sc.cases}
    assert "p2_groups_true" in case_ids and "order6_abelian_false" in case_ids
    for case in sc.cases:
        assert case.observability.events_present
        assert case.observability.timings_present
        assert case.observability.agent_events_present
        assert case.handoffs.target_subgoal_selected
        assert case.handoffs.prover_round_bound_to_subgoal
        assert case.handoffs.reviewer_objection_written_to_graph


def test_scorecard_writes_yaml_and_markdown(smoke_cycle: Path, tmp_path: Path) -> None:
    cycle = _copy_cycle(smoke_cycle, tmp_path / "cycle")
    sc = write_loop_closure_scorecard(cycle)
    yaml_path = cycle / SCORECARD_FILE
    md_path = cycle / SUMMARY_FILE
    assert yaml_path.exists() and md_path.exists()
    # YAML round-trips through the strict model.
    LoopClosureScorecard.model_validate(read_yaml(yaml_path))
    summary = read_text(md_path)
    assert "workflow replay" in summary.lower()
    # workflow language only — never "proof passed".
    assert "proof passed" not in summary.lower()
    assert sc.advisory_only is True


def test_summary_uses_workflow_replay_language_not_proof() -> None:
    sc = LoopClosureScorecard(cycle_id="c1", workflow_gate_passed=True, safety=_clean_safety())
    md = render_summary(sc)
    assert "NOT a proof" in md
    assert "upgrades no" in md.lower()


def _clean_safety():
    from espresso_proof.kernel.loop_models import ScorecardSafety

    return ScorecardSafety()


# -- planted SAFETY failures --------------------------------------------------


def test_unauthorized_human_confirmed_fails(smoke_cycle: Path, tmp_path: Path) -> None:
    cycle = _copy_cycle(smoke_cycle, tmp_path / "cycle")
    run_dir = _run_dirs(cycle)[0]
    claim = read_yaml(run_dir / "locked_claim.yaml")
    claim["human_confirmed"] = True  # the gate must NEVER confirm the claim
    write_yaml(run_dir / "locked_claim.yaml", claim)
    sc = compute_loop_closure_scorecard(cycle)
    assert sc.safety.unauthorized_human_confirmed_count >= 1
    assert not sc.workflow_gate_passed


def test_proof_complete_language_fails(smoke_cycle: Path, tmp_path: Path) -> None:
    cycle = _copy_cycle(smoke_cycle, tmp_path / "cycle")
    run_dir = _run_dirs(cycle)[0]
    report = run_dir / "final_report.md"
    text = read_text(report) if report.exists() else "# report\n"
    write_text(report, text + "\nWe have proved the theorem; proof-close.\n")
    sc = compute_loop_closure_scorecard(cycle)
    assert sc.safety.proof_complete_language_count >= 1
    assert not sc.workflow_gate_passed


def test_fabricated_proved_grade_fails(smoke_cycle: Path, tmp_path: Path) -> None:
    cycle = _copy_cycle(smoke_cycle, tmp_path / "cycle")
    run_dir = _run_dirs(cycle)[0]
    grade = read_yaml(run_dir / "idea_grade.yaml")
    grade["status"] = "proved"  # never a valid product status
    write_yaml(run_dir / "idea_grade.yaml", grade)
    sc = compute_loop_closure_scorecard(cycle)
    assert sc.safety.unauthorized_proved_count >= 1
    assert not sc.workflow_gate_passed


# -- planted OBSERVABILITY failure --------------------------------------------


def test_missing_timings_fails_observability(smoke_cycle: Path, tmp_path: Path) -> None:
    cycle = _copy_cycle(smoke_cycle, tmp_path / "cycle")
    run_dir = _run_dirs(cycle)[0]
    (run_dir / "timings.yaml").unlink()
    sc = compute_loop_closure_scorecard(cycle)
    bad = next(c for c in sc.cases if Path(c.run_dir) == run_dir)
    assert bad.observability.timings_present is False
    assert not sc.workflow_gate_passed


# -- planted HANDOFF failures -------------------------------------------------


def test_no_target_subgoal_fails_handoff(smoke_cycle: Path, tmp_path: Path) -> None:
    cycle = _copy_cycle(smoke_cycle, tmp_path / "cycle")
    run_dir = _run_dirs(cycle)[0]
    loop = read_yaml(run_dir / "prover_loop.yaml")
    for attempt in loop["attempts"]:
        attempt["target_subgoal_id"] = None
        attempt["target_local_claim"] = ""
    write_yaml(run_dir / "prover_loop.yaml", loop)
    sc = compute_loop_closure_scorecard(cycle)
    bad = next(c for c in sc.cases if Path(c.run_dir) == run_dir)
    assert bad.handoffs.target_subgoal_selected is False
    assert not sc.workflow_gate_passed


def test_free_prose_objections_fail_required_subgoal_binding(smoke_cycle: Path, tmp_path: Path) -> None:
    # Strip every reviewer's objections down to route scope (free prose, not bound
    # to a subgoal). With reviewer_objection_bound_to_subgoal REQUIRED, the gate fails.
    cycle = _copy_cycle(smoke_cycle, tmp_path / "cycle")
    run_dir = _run_dirs(cycle)[0]

    def _routeify(scoped: list[dict]) -> list[dict]:
        out = []
        for so in scoped:
            so = dict(so)
            so.update(scope="route", subgoal_id=None, proof_step_id=None,
                      scope_source="legacy_fallback", scope_inferred=False, inference_reason="")
            out.append(so)
        return out

    agent_run = read_yaml(run_dir / "llm_agent_run.yaml")
    for review in agent_run.get("verifier_reviews", []):
        review["scoped_objections"] = _routeify(review.get("scoped_objections", []))
    write_yaml(run_dir / "llm_agent_run.yaml", agent_run)
    rounds = run_dir / "prover_rounds"
    for rp in rounds.glob("round_*.yaml"):
        data = read_yaml(rp)
        for review in data.get("verifier_reviews", []):
            review["scoped_objections"] = _routeify(review.get("scoped_objections", []))
        write_yaml(rp, data)

    # default gate (no subgoal-binding required) still passes structurally...
    base = compute_loop_closure_scorecard(cycle)
    bad = next(c for c in base.cases if Path(c.run_dir) == run_dir)
    assert bad.handoffs.reviewer_objection_bound_to_subgoal is False
    # ...but a gate that REQUIRES the subgoal binding fails.
    strict = compute_loop_closure_scorecard(
        cycle, required_handoffs=["target_subgoal_selected", "reviewer_objection_bound_to_subgoal"]
    )
    assert not strict.workflow_gate_passed


# -- replay before/after ------------------------------------------------------


def test_replay_regression_fails(smoke_cycle: Path, tmp_path: Path) -> None:
    # Baseline is clean; the replay introduces a new proof-language safety
    # violation -> regression / new P0-P1 -> gate fails.
    before = _copy_cycle(smoke_cycle, tmp_path / "before")
    after = _copy_cycle(smoke_cycle, tmp_path / "after")
    run_dir = _run_dirs(after)[0]
    report = run_dir / "final_report.md"
    text = read_text(report) if report.exists() else "# report\n"
    write_text(report, text + "\nThis completes the proof. proof-close.\n")
    sc = compute_loop_closure_scorecard(after, before_cycle_dir=before)
    assert sc.before_after is not None
    assert sc.before_after.regression_found or sc.before_after.new_p0_p1_count > 0
    assert not sc.workflow_gate_passed


def test_replay_fix_is_recorded_as_improved(smoke_cycle: Path, tmp_path: Path) -> None:
    # Baseline has a handoff gap (no target subgoal); the replay restores it.
    before = _copy_cycle(smoke_cycle, tmp_path / "before")
    bad_run = _run_dirs(before)[0]
    loop = read_yaml(bad_run / "prover_loop.yaml")
    for attempt in loop["attempts"]:
        attempt["target_subgoal_id"] = None
        attempt["target_local_claim"] = ""
    write_yaml(bad_run / "prover_loop.yaml", loop)

    after = _copy_cycle(smoke_cycle, tmp_path / "after")  # unmodified = the fix
    sc = compute_loop_closure_scorecard(after, before_cycle_dir=before)
    assert sc.before_after is not None
    assert sc.before_after.selected_workflow_gap_fixed_or_improved is True
    assert sc.before_after.regression_found is False
    assert sc.workflow_gate_passed


# -- kernel safety: the scorecard never upgrades status -----------------------


def test_scorecard_is_advisory_and_not_a_kernel_artifact(smoke_cycle: Path, tmp_path: Path) -> None:
    cycle = _copy_cycle(smoke_cycle, tmp_path / "cycle")
    write_loop_closure_scorecard(cycle)
    # the per-run kernel manifests never reference the scorecard as an artifact.
    for run_dir in _run_dirs(cycle):
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        names = set((manifest.get("artifacts") or {}).values()) | set(
            (manifest.get("optional_artifacts") or {}).values()
        )
        assert SCORECARD_FILE not in names and SUMMARY_FILE not in names
        # the kernel run still validates with the scorecard sitting in the cycle.
        from espresso_proof.kernel.validators import validate_run

        validate_run(run_dir)
