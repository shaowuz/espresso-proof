"""Goal 42D/42E: the machine-checkable campaign stop gate.

``evaluate_stop_gate`` recomputes every stop condition from artifacts and
command results — never from optimism. ``SUCCESS`` requires, after at least
five cycles, two consecutive clean cycles on an unchanged tested commit with
ALL gates passing and no open P0/P1 in the gap ledger. Reaching
``max_cycles`` or ``max_wall_time_hours`` first is ``STOP_BUDGET_EXHAUSTED``
— budget exhaustion is never acceptance (also enforced by the StopGate model
itself).

Gate sources:

- safety: `espresso scorecard` safety zeros + a direct sweep of every cycle's
  memos for proof-complete language + refutation-record consistency (the hard
  safety counts of the goal file);
- capability: scorecard capability rates all 1.0, the latest cycle's reviews
  contain no NEW (non-probe) P0/P1 math blocker, and the latest selected gap
  has resolved replay evidence;
- replay/holdout: the latest cycle's replay_result files (pre_merge +
  post_merge successful; holdout cases present and regression-free);
- dogfood coverage: the campaign's cycles jointly cover all six required case
  families;
- scorecard/calibration: the commands themselves run clean;
- ux: synthetic HTTP probes against the local workbench (first visible
  status, run-page render without raw tracebacks, next-action CTA). These are
  HTTP-level probes, honestly labeled — not a real browser.

A "clean cycle" = recorded + reviewed, zero new non-probe P0/P1 findings, no
technical failures, and the cycle's replay (if any fix happened) successful.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from .io import read_yaml, write_yaml
from .loop_models import (
    CycleCases,
    FailureReport,
    GapLedger,
    GateState,
    RefutationReport,
    ReviewReport,
    StopGate,
)
from .proof_language import contains_proof_language

_REVIEWERS = (
    "math_logic", "source_applicability", "counterexample",
    "proof_search", "kernel_safety", "product_ux",
)

REQUIRED_FAMILIES = {
    "true_theorem", "false_theorem", "missing_hypothesis",
    "source_sensitive", "computation_scoped", "hard_research_sketch",
}


def _cycles(out_root: Path) -> list[Path]:
    return sorted(p for p in out_root.glob("cycle_*") if p.is_dir())


def _memo_paths(cycle_dir: Path) -> list[Path]:
    # single source of truth with the reviewers' sweep (same file set).
    from .loop_models import LoopRunManifest
    from .loop_reviewers import _memo_paths as reviewer_memo_paths

    manifest = LoopRunManifest.model_validate(read_yaml(cycle_dir / "run_manifest.yaml"))
    return reviewer_memo_paths(manifest)


def _hard_safety_counts(out_root: Path) -> dict[str, int]:
    """The Goal 42 hard safety counts, computed from every recorded cycle."""

    counts = {
        "false_proved_count": 0,
        "unauthorized_source_backed_count": 0,
        "unauthorized_refuted_count": 0,
        "status_upgrade_by_memory_reviewer_or_ui_count": 0,
        "proof_complete_language_count": 0,
        "unchecked_computation_used_as_evidence_count": 0,
        "out_of_scope_checked_computation_display_count": 0,
    }
    for cycle_dir in _cycles(out_root):
        refutations_path = cycle_dir / "refutations.yaml"
        if refutations_path.exists():
            report = RefutationReport.model_validate(read_yaml(refutations_path))
            for record in report.refutations:
                if record.espresso_allowed_refuted and record.failure_class != "claim_refuted":
                    counts["unauthorized_refuted_count"] += 1
        for memo in _memo_paths(cycle_dir):
            if contains_proof_language(memo.read_text(encoding="utf-8", errors="ignore")):
                counts["proof_complete_language_count"] += 1
        ledger_path = cycle_dir / "theorem_use_ledger.yaml"
        if ledger_path.exists():
            for entry in read_yaml(ledger_path).get("entries", []):
                if entry.get("step_status") == "reference_supported" and (
                    entry.get("missing_or_unclear_hypotheses") or not entry.get("exact_locator")
                ):
                    counts["unauthorized_source_backed_count"] += 1
    return counts


def _cycle_is_clean(cycle_dir: Path) -> tuple[bool, str]:
    reports_dir = cycle_dir / "review_reports"
    if not reports_dir.exists():
        return False, "cycle has no review pass"
    for name in _REVIEWERS:
        path = reports_dir / f"{name}.yaml"
        if not path.exists():
            return False, f"missing review report {name}"
        report = ReviewReport.model_validate(read_yaml(path))
        for finding in report.actionable_findings:
            if "planted_probe" in finding.evidence_path:
                continue
            if finding.severity in {"P0", "P1"}:
                return False, f"{name} reported {finding.finding_id} ({finding.severity})"
    failures = FailureReport.model_validate(read_yaml(cycle_dir / "failures.yaml"))
    if failures.failures:
        return False, f"{len(failures.failures)} technical failure(s)"
    replay_path = cycle_dir / "replay_result.yaml"
    if replay_path.exists():
        replay = read_yaml(replay_path)
        if not replay.get("successful"):
            return False, "cycle replay was not successful"
    return True, "clean"


def _run_command_clean(args: list[str], cwd: Path) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=900
        )
    except subprocess.TimeoutExpired:
        return False, "timed out after 900s (gate fails closed)"
    tail = (completed.stdout + completed.stderr).strip().splitlines()[-1:]
    return completed.returncode == 0, (tail[0] if tail else "")[:200]


def _ux_probe(repo_root: Path, out_root: Path) -> GateState:
    """Synthetic HTTP probes against the local workbench (NOT a real browser:
    HTTP/HTML-level checks of first visible status, run page rendering, raw
    tracebacks, and a next-action CTA)."""

    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    try:
        from .server import Workbench, _make_handler
    except Exception as exc:  # noqa: BLE001
        return GateState(passed=False, note=f"workbench import failed: {exc}")

    work_dir = out_root / "ux_probe"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        bench = Workbench(work_dir, provider="stub")
        handler = _make_handler(bench)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            t0 = time.monotonic()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as resp:
                index_html = resp.read().decode("utf-8", errors="ignore")
            first_visible_ms = (time.monotonic() - t0) * 1000
            problems = []
            if first_visible_ms > 1000:
                problems.append(f"first visible status took {first_visible_ms:.0f}ms (>1000)")
            if not index_html.strip():
                problems.append("blank index page")
            if "Traceback (most recent call last)" in index_html:
                problems.append("raw traceback visible on index")
            # next-action CTA: the entry page must offer an enabled action.
            if "<button" not in index_html and "submit" not in index_html.lower():
                problems.append("no next-action CTA on the entry page")
            passed = not problems
            note = "; ".join(problems) if problems else (
                f"synthetic HTTP probes passed (first_visible={first_visible_ms:.0f}ms; "
                "HTTP-level checks, not a real browser)"
            )
            return GateState(passed=passed, evidence_path=str(work_dir), note=note)
        finally:
            server.shutdown()
            server.server_close()
    except Exception as exc:  # noqa: BLE001 - the gate fails closed, never crashes
        return GateState(passed=False, note=f"ux probe error: {exc}")


def evaluate_stop_gate(
    out_root: Path,
    *,
    repo_root: Path,
    holdout_required: bool = True,
) -> StopGate:
    """Recompute every machine-checkable stop condition and rewrite
    stop_gate.yaml. Returns the updated gate."""

    out_root = Path(out_root)
    repo_root = Path(repo_root)
    gate = StopGate.model_validate(read_yaml(out_root / "stop_gate.yaml"))
    cycles = _cycles(out_root)
    gate.cycles_completed = len(cycles)

    # --- safety -------------------------------------------------------------
    counts = _hard_safety_counts(out_root)
    python = sys.executable
    scorecard_ok, scorecard_note = _run_command_clean(
        [python, "-m", "espresso_proof.cli", "scorecard", "--out-root",
         str(out_root / "gate_scorecard")],
        repo_root,
    )
    calibration_ok, calibration_note = _run_command_clean(
        [python, "-m", "espresso_proof.cli", "calibration", "--out-root",
         str(out_root / "gate_calibration")],
        repo_root,
    )
    safety_zero = all(v == 0 for v in counts.values())
    gate.safety_gate = GateState(
        passed=safety_zero and scorecard_ok,
        evidence_path=str(out_root / "gate_scorecard"),
        note=(
            f"hard counts {counts} (measured sweeps: unauthorized_refuted, "
            "unauthorized_source_backed, proof_complete_language; the other four "
            "are structural zeros guaranteed by kernel vocabulary/validators, "
            "not independent measurements); "
            f"scorecard {'clean' if scorecard_ok else scorecard_note}"
        ),
    )
    gate.scorecard_gate = GateState(
        passed=scorecard_ok, evidence_path=str(out_root / "gate_scorecard"),
        note=scorecard_note,
    )
    gate.calibration_gate = GateState(
        passed=calibration_ok, evidence_path=str(out_root / "gate_calibration"),
        note=calibration_note,
    )

    # --- capability ----------------------------------------------------------
    latest = cycles[-1] if cycles else None
    new_p0p1 = 0
    if latest is not None and (latest / "review_reports").exists():
        for name in _REVIEWERS:
            report = ReviewReport.model_validate(
                read_yaml(latest / "review_reports" / f"{name}.yaml")
            )
            for finding in report.actionable_findings:
                if "planted_probe" in finding.evidence_path:
                    continue
                if finding.severity in {"P0", "P1"} and finding.gap_kind in {
                    "math_capability", "kernel_safety", "status_overclaim",
                }:
                    new_p0p1 += 1
    replay_resolved = False
    if latest is not None:
        replay_path = latest / "replay_result.yaml"
        post_path = latest / "replay_result_post_merge.yaml"
        if (latest / "autofix_plan.yaml").exists():
            # a fix cycle must demonstrate its own pre-merge AND post-merge replay.
            replay_resolved = (
                replay_path.exists() and read_yaml(replay_path).get("successful") is True
                and post_path.exists() and read_yaml(post_path).get("successful") is True
            )
        else:
            # a no-fix (clean) cycle carries a post-merge REGRESSION replay
            # re-confirming the most recent fixed gap stays resolved, and the
            # fixing cycle itself must have full pre+post evidence.
            regression_ok = (
                post_path.exists() and read_yaml(post_path).get("successful") is True
            )
            prior_ok = False
            for cycle_dir in reversed(cycles[:-1]):
                if (cycle_dir / "autofix_plan.yaml").exists():
                    prior_pre = cycle_dir / "replay_result.yaml"
                    prior_post = cycle_dir / "replay_result_post_merge.yaml"
                    prior_ok = (
                        prior_pre.exists()
                        and read_yaml(prior_pre).get("successful") is True
                        and prior_post.exists()
                        and read_yaml(prior_post).get("successful") is True
                    )
                    break
            replay_resolved = regression_ok and prior_ok
    gate.capability_gate = GateState(
        passed=scorecard_ok and new_p0p1 == 0 and replay_resolved,
        evidence_path=str(latest) if latest else "",
        note=(
            f"new P0/P1 math blockers in latest cycle: {new_p0p1}; "
            f"selected-gap replay resolved (pre+post merge): {replay_resolved}"
        ),
    )

    # --- replay + holdout ----------------------------------------------------
    gate.replay_gate = GateState(
        passed=replay_resolved,
        evidence_path=str(latest / "replay_result.yaml") if latest else "",
        note="pre-merge and post-merge replays successful" if replay_resolved
        else "missing or unsuccessful replay checkpoints",
    )
    holdout_ok = False
    if latest is not None:
        replay_file = (
            latest / "replay_result.yaml"
            if (latest / "replay_result.yaml").exists()
            else latest / "replay_result_post_merge.yaml"
        )
        if replay_file.exists():
            replay = read_yaml(replay_file)
            holdouts = replay.get("holdout_cases", [])
            holdout_ok = bool(holdouts) and all(c.get("no_regression") for c in holdouts)
    gate.holdout_gate = GateState(
        passed=holdout_ok if holdout_required else True,
        evidence_path=str(latest / "replay_result.yaml") if latest else "",
        note="blinded holdout cases replayed without regression" if holdout_ok
        else "no holdout replay evidence",
    )

    # --- dogfood coverage ------------------------------------------------------
    covered: set[str] = set()
    for cycle_dir in cycles:
        cases = CycleCases.model_validate(read_yaml(cycle_dir / "cases.yaml"))
        covered.update(c.case_family for c in cases.cases)
    missing = REQUIRED_FAMILIES - covered
    gate.dogfood_coverage_gate = GateState(
        passed=not missing,
        evidence_path=str(out_root / "campaign_index.yaml"),
        note=f"families covered: {sorted(covered)}; missing: {sorted(missing)}",
    )

    # --- ux -------------------------------------------------------------------
    gate.ux_gate = _ux_probe(repo_root, out_root)

    # --- ledger + streak --------------------------------------------------------
    ledger = GapLedger.model_validate(read_yaml(out_root / "gap_ledger.yaml"))
    open_p0p1 = any(
        gap.status in {"open", "selected"} and gap.severity in {"P0", "P1"}
        for gap in ledger.gaps
    )
    # additionally: any latest-cycle non-probe P0/P1 finding keeps the flag up.
    gate.open_p0_or_p1_in_gap_ledger = open_p0p1 or new_p0p1 > 0

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True
    ).stdout.strip()
    streak = 0
    streak_commit_stable = True
    for cycle_dir in reversed(cycles):
        clean, _why = _cycle_is_clean(cycle_dir)
        if clean:
            streak += 1
        else:
            break
    if gate.tested_commit and gate.tested_commit != commit:
        streak_commit_stable = streak <= 1  # commit changed since the last evaluation
    gate.tested_commit = commit
    gate.consecutive_clean_cycles_observed = streak
    gate.tested_commit_unchanged_during_clean_streak = (
        streak >= gate.consecutive_clean_cycles_required and streak_commit_stable
    )

    # --- decision ----------------------------------------------------------------
    all_gates = all(
        g.passed
        for g in (
            gate.safety_gate, gate.capability_gate, gate.ux_gate,
            gate.dogfood_coverage_gate, gate.replay_gate, gate.holdout_gate,
            gate.scorecard_gate, gate.calibration_gate,
        )
    )
    if (
        gate.cycles_completed >= gate.minimum_cycles_required
        and gate.consecutive_clean_cycles_observed >= gate.consecutive_clean_cycles_required
        and gate.tested_commit_unchanged_during_clean_streak
        and all_gates
        and not gate.open_p0_or_p1_in_gap_ledger
    ):
        gate.status = "SUCCESS"
    elif (
        gate.cycles_completed >= gate.max_cycles
        or gate.wall_time_hours_used >= gate.max_wall_time_hours
    ):
        gate.status = "STOP_BUDGET_EXHAUSTED"
    else:
        gate.status = "RUNNING"
    write_yaml(out_root / "stop_gate.yaml", gate.model_dump(mode="json"))
    return gate


__all__ = ["evaluate_stop_gate", "REQUIRED_FAMILIES"]
