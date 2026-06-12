"""Goal 42D: replay machinery, fix-gate guardrails, and the stop gate."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.loop_harness import load_cases, run_loop_cycle
from espresso_proof.loop_models import ReplayResult, StopGate
from espresso_proof.loop_replay import (
    run_replay,
    write_capability_delta,
    write_learning_delta,
    write_next_cycle_policy,
)
from espresso_proof.loop_reviewers import run_review_pass
from espresso_proof.loop_triage import run_triage
from tests.loop_gap_case import gap_cases

REPO_ROOT = Path(__file__).resolve().parents[1]


def _gap_cases_file(directory: Path) -> Path:
    # Planted order-21 gap (see tests/loop_gap_case.py for why order 21).
    path = directory / "cases.yaml"
    write_yaml(path, gap_cases())
    return path


@pytest.fixture(scope="module")
def triaged(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("loop")
    cycle_dir = run_loop_cycle(
        out_root=base / "loop_runs", mode="smoke", cases_path=_gap_cases_file(base)
    )
    run_review_pass(cycle_dir)
    run_triage(cycle_dir)
    return cycle_dir


def test_baseline_replay_records_unfixed_problem(triaged: Path) -> None:
    # the genuine smoke gap (zero counterexample tests on the out-of-probe-scope
    # false-theorem case) is NOT fixed: baseline replay must say fixed=False
    # and must not be successful.
    result = run_replay(
        triaged,
        checkpoint="baseline",
        probe="false_theorem_counterexample_present",
        triggering_case_id="order21_abelian_false",
        sibling_case_ids=["p2_groups_true"],
        holdout_cases=[c for c in load_cases(None) if c.case_id == "matrix_rank_scoped"],
        observed_problem="false-theorem case produces zero counterexample tests",
        before_evidence_path=str(triaged / "refutations.yaml"),
    )
    assert result.after.fixed is False
    assert result.successful is False
    saved = ReplayResult.model_validate(read_yaml(triaged / "replay_result_baseline.yaml"))
    assert saved.checkpoint == "baseline"


def test_pre_merge_replay_failure_blocks_merge(triaged: Path) -> None:
    with pytest.raises(RuntimeError, match="merge is blocked"):
        run_replay(
            triaged,
            checkpoint="pre_merge",
            probe="false_theorem_counterexample_present",
            triggering_case_id="order21_abelian_false",
            sibling_case_ids=["p2_groups_true"],
            observed_problem="false-theorem case produces zero counterexample tests",
            before_evidence_path=str(triaged / "refutations.yaml"),
        )


def test_replay_model_refuses_success_without_improvement_or_holdouts() -> None:
    base = {
        "cycle_id": "c", "selected_gap_id": "g", "checkpoint": "pre_merge",
        "before": {"evidence_path": "x", "observed_problem": "p"},
        "after": {"evidence_path": "y", "fixed": False},
        "successful": True,
    }
    with pytest.raises(ValueError, match="without improvement"):
        ReplayResult.model_validate(base)
    with pytest.raises(ValueError, match="sibling or holdout"):
        ReplayResult.model_validate({**base, "after": {"evidence_path": "y", "fixed": True}})
    with pytest.raises(ValueError, match="regression cannot be successful"):
        ReplayResult.model_validate(
            {
                **base,
                "after": {"evidence_path": "y", "fixed": True},
                "sibling_cases": [{"case_id": "s", "no_regression": False}],
            }
        )


def test_unknown_probe_and_case_fail_closed(triaged: Path) -> None:
    with pytest.raises(ValueError, match="unknown problem probe"):
        run_replay(
            triaged, checkpoint="baseline", probe="nonexistent",
            triggering_case_id="order6_abelian_false",
            observed_problem="x", before_evidence_path="y",
        )
    with pytest.raises(ValueError, match="unknown triggering case"):
        run_replay(
            triaged, checkpoint="baseline",
            probe="false_theorem_counterexample_present",
            triggering_case_id="no_such_case",
            observed_problem="x", before_evidence_path="y",
        )


def test_fix_gate_refuses_without_explicit_allow(triaged: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_loop_fix_gate.py"),
         "--cycle", str(triaged), "--base", "HEAD"],
        capture_output=True, text=True, cwd=REPO_ROOT, env=_git_env(),
    )
    assert completed.returncode == 2
    assert "read-only by default" in completed.stdout


def test_fix_gate_refuses_without_plan(triaged: Path, tmp_path: Path) -> None:
    empty_cycle = tmp_path / "cycle_x"
    empty_cycle.mkdir()
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_loop_fix_gate.py"),
         "--cycle", str(empty_cycle), "--base", "HEAD", "--allow-code-change"],
        capture_output=True, text=True, cwd=REPO_ROOT, env=_git_env(),
    )
    assert completed.returncode == 2
    assert "no autofix_plan.yaml" in completed.stdout


def _git_env() -> dict[str, str]:
    """Environment for tmp-repo git calls with every GIT_* variable scrubbed.

    Regression guard (Goal 42E cycle 9, incident first recorded in PR #129):
    when pytest runs under a git hook (e.g. the local pre-push release gate),
    git exports GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE into the hook's
    environment. A subprocess git call that inherits them operates on the
    REAL repository regardless of ``cwd`` — during a real push this created
    stray commits on the branch, overwrote the repo-level user.email, and
    unstaged tracked files. Scrubbing GIT_* confines every command below to
    the tmp repo it targets."""

    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _build_tiny_repo(repo: Path) -> str:
    """A two-commit tmp repo (protected + unplanned change); returns base sha."""

    env = _git_env()
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, env=env)
    (repo / "espresso_proof").mkdir(exist_ok=True)
    (repo / "espresso_proof" / "validators.py").write_text("x = 1\n")
    (repo / "espresso_proof" / "other.py").write_text("y = 1\n")
    subprocess.run(["git", "add", "espresso_proof"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True, env=env)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, env=env
    ).stdout.strip()
    (repo / "espresso_proof" / "validators.py").write_text("x = 2\n")
    (repo / "espresso_proof" / "other.py").write_text("y = 2\n")
    subprocess.run(["git", "add", "espresso_proof"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "change"], cwd=repo, check=True, env=env)
    return base


def test_tmp_repo_builder_ignores_hook_git_env(tmp_path: Path, monkeypatch) -> None:
    # Regression for the PR #129 incident: simulate the hook environment by
    # pointing GIT_DIR at a sentinel repository, then build the tiny repo.
    # The sentinel must be COMPLETELY untouched (no commits, no config, no
    # index changes) and the tiny repo must get its own two commits.
    env = _git_env()
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=sentinel, check=True, env=env)
    subprocess.run(
        ["git", "-C", str(sentinel), "-c", "user.email=s@s", "-c", "user.name=s",
         "commit", "-qm", "sentinel", "--allow-empty"],
        check=True, env=env,
    )
    sentinel_head = subprocess.run(
        ["git", "-C", str(sentinel), "rev-parse", "HEAD"],
        capture_output=True, text=True, env=env,
    ).stdout.strip()
    monkeypatch.setenv("GIT_DIR", str(sentinel / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(sentinel / ".git" / "index"))
    repo = tmp_path / "repo"
    _build_tiny_repo(repo)
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert (
        subprocess.run(
            ["git", "-C", str(sentinel), "rev-parse", "HEAD"],
            capture_output=True, text=True, env=env,
        ).stdout.strip()
        == sentinel_head
    ), "the sentinel repo must not gain commits from the tmp-repo builder"
    assert (
        subprocess.run(
            ["git", "-C", str(sentinel), "config", "--local", "user.email"],
            capture_output=True, text=True, env=env,
        ).stdout.strip()
        == ""
    ), "the sentinel repo's config must stay untouched"
    tiny_log = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%s"],
        capture_output=True, text=True, env=env,
    ).stdout.split()
    assert tiny_log == ["change", "base"]


def test_fix_gate_refuses_protected_and_unplanned_paths(tmp_path: Path) -> None:
    cycle = tmp_path / "cycle_y"
    cycle.mkdir()
    write_yaml(
        cycle / "autofix_plan.yaml",
        {
            "cycle_id": "cycle_y", "selected_gap_id": "g", "why_this_gap_now": "w",
            "files_expected_to_change": ["espresso_proof/agent_runner.py"],
            "max_files": 8, "max_effective_diff_lines": 800,
            "focused_tests": ["t"], "required_reviewers": ["Engineering", "Kernel Safety"],
            "replay_cases": ["c"], "abort_if": [],
        },
    )
    # build a tiny git repo with a protected + an unplanned change.
    base = _build_tiny_repo(tmp_path / "repo")
    repo = tmp_path / "repo"
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_loop_fix_gate.py"),
         "--cycle", str(cycle), "--base", base, "--allow-code-change",
         "--repo", str(repo)],
        capture_output=True, text=True, env=_git_env(),
    )
    assert completed.returncode == 1
    assert "protected safety surface touched" in completed.stdout
    assert "unplanned file changed" in completed.stdout


def test_delta_writers_validate_and_stay_advisory(triaged: Path) -> None:
    delta = write_learning_delta(
        triaged,
        {
            "observed_failure_mode": "no counterexample tests for a false claim",
            "old_strategy_or_behavior": "agents emit empty test banks in smoke mode",
            "failure_evidence_path": str(triaged / "refutations.yaml"),
            "new_strategy_or_behavior": "wire deterministic checkers into the agent",
            "allowed_scope": "finite-group claims only until rechecked",
            "forbidden_generalizations": ["does not transfer to analysis claims"],
            "required_rechecks": ["replay order6_abelian_false"],
        },
    )
    assert delta.advisory_only is True
    policy = write_next_cycle_policy(
        triaged, {"counterexample_checks_to_strengthen": ["finite group enumeration"]}
    )
    assert policy.advisory_only is True
    with pytest.raises(ValueError, match="before/after evidence"):
        write_capability_delta(
            triaged,
            {
                "selected_gap_id": "g", "capability": "counterexample_refutation",
                "before_evidence_path": " ", "after_evidence_path": "",
                "improved": True,
            },
        )


def test_stop_gate_running_and_budget_semantics(tmp_path: Path, monkeypatch) -> None:
    from espresso_proof import loop_stop_gate

    root = tmp_path / "loop_runs"
    cycle_dir = run_loop_cycle(
        out_root=root, mode="smoke", cases_path=_gap_cases_file(tmp_path)
    )
    run_review_pass(cycle_dir)
    # avoid the expensive scorecard/calibration subprocesses in unit tests.
    monkeypatch.setattr(
        loop_stop_gate, "_run_command_clean", lambda args, cwd: (True, "stubbed clean")
    )
    gate = loop_stop_gate.evaluate_stop_gate(root, repo_root=REPO_ROOT)
    assert gate.status == "RUNNING"  # one cycle, open P1 findings, no replay
    assert gate.dogfood_coverage_gate.passed is False  # 3 of 6 families so far
    assert gate.open_p0_or_p1_in_gap_ledger is True
    # budget exhaustion: never SUCCESS.
    raw = read_yaml(root / "stop_gate.yaml")
    raw["wall_time_hours_used"] = 99.0
    write_yaml(root / "stop_gate.yaml", raw)
    gate = loop_stop_gate.evaluate_stop_gate(root, repo_root=REPO_ROOT)
    assert gate.status == "STOP_BUDGET_EXHAUSTED"
    # and the model itself refuses SUCCESS with failing gates.
    with pytest.raises(ValueError):
        StopGate.model_validate({**read_yaml(root / "stop_gate.yaml"), "status": "SUCCESS"})
