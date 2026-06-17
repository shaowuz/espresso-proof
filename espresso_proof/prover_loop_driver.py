"""Prover multi-round continuation driver.

Chains the existing single-round ``llm_agents.append_prover_loop_round`` into a
BOUNDED multi-round Prover<->Reviewer loop behind a resolved human gate. The
kernel stays the trusted gate: every round is re-validated, nothing upgrades
proof / source / evidence status (the loop/round artifacts are StrictBaseModels
with no status fields, and ``make_prover_attempt`` pins ``needs_human``), and the
loop fails closed.

See docs/goals/PROVER_MULTI_ROUND_CONTINUATION_PLAN.md.
"""

from __future__ import annotations

from pathlib import Path

from .claim_clear import ensure_claim_clear_for_downstream
from .io import read_yaml, write_yaml
from .llm_agents import LLMClient, append_prover_loop_round
from .observability import append_agent_event, monotonic_ms, now_iso
from .run_events import append_event
from .run_lock import run_writer_lock
from .surface_warnings import refresh_final_report_disclosures
from .validators import validate_run

# How many consecutive rounds with an identical blocker set count as no-progress.
NO_PROGRESS_WINDOW = 2

# Closed set of stop reasons (advisory string; carries no status).
STOP_REASONS = {
    "loop_decision_ask_human",
    "loop_decision_stop",
    "budget_rounds",
    "budget_agent_calls",
    "no_progress",
    "clean",
    "validation_error",
    "time_allocation_ask_human",
    "time_allocation_stop",
}


def _snapshot_run(run_dir: Path) -> dict:
    """Capture the files a single ``append_prover_loop_round`` mutates, so a
    mid-loop validation failure can be rolled back to the last valid state."""

    snap: dict = {}
    for rel in (
        "prover_loop.yaml",
        "run_manifest.yaml",
        "time_allocation.yaml",
        "update_inbox.yaml",
        "final_report.md",
    ):
        p = run_dir / rel
        snap[rel] = p.read_bytes() if p.exists() else None
    rounds_dir = run_dir / "prover_rounds"
    snap["__rounds__"] = (
        {f.name: f.read_bytes() for f in rounds_dir.glob("*.yaml")}
        if rounds_dir.exists()
        else {}
    )
    return snap


def _restore_run(run_dir: Path, snap: dict) -> None:
    """Restore a snapshot taken by ``_snapshot_run`` (drop newly-written round
    artifacts; rewrite prover_loop/manifest/round files to their prior bytes)."""

    for rel, data in snap.items():
        if rel == "__rounds__":
            continue
        p = run_dir / rel
        if data is None:
            if p.exists():
                p.unlink()
        else:
            p.write_bytes(data)
    rounds_dir = run_dir / "prover_rounds"
    saved = snap.get("__rounds__", {})
    if rounds_dir.exists():
        for f in rounds_dir.glob("*.yaml"):
            if f.name not in saved:
                f.unlink()  # remove a round artifact written by the failed append
    if saved:
        rounds_dir.mkdir(exist_ok=True)
        for name, data in saved.items():
            (rounds_dir / name).write_bytes(data)


def _round_blockers(attempts: list[dict]) -> dict[int, list[str]]:
    """Map round_index -> sorted blocker set.

    The initial report has one attempt per top strategy, all sharing the same
    blockers, so we compare by ROUND (not per attempt) to avoid false
    no-progress on the initial portfolio.
    """

    by_round: dict[int, list[str]] = {}
    for attempt in attempts:
        ri = attempt.get("round_index")
        if ri is None:
            continue
        if ri not in by_round:
            by_round[ri] = sorted(str(b) for b in (attempt.get("blockers") or []))
    return by_round


def _failure_summary(run_dir: Path) -> str | None:
    """Advisory failure-classification summary (never sets any status)."""

    try:
        from .failure_classifier import build_failure_classification

        report = build_failure_classification(run_dir)
    except Exception:  # noqa: BLE001 - advisory only; never block the loop
        return None
    categories = report.get("classifications") or report.get("categories") or []
    if isinstance(categories, list) and categories:
        names = [str(c.get("category", c)) for c in categories if isinstance(c, dict)] or [
            str(c) for c in categories
        ]
        return f"failure categories: {', '.join(names[:5])}"
    return None


def _grant_escalation_round(run_dir: Path, loop_path: str, attempts: list[dict]) -> None:
    """Grant exactly one extra targeted repair round by bumping the prover-loop
    budget caps by one round's worth.

    ``ProverLoopBudget`` requires ``rounds_used <= max_rounds`` and
    ``agent_calls_used <= max_agent_calls``; a ``quick_test`` budget (1 round, 4
    agent calls) is fully spent after round 1, so the caps must be raised before the
    extra round is appended or the artifact would be invalid. This is a scheduling /
    time-allocation grant ONLY -- it cannot and does not upgrade any
    proof/source/evidence/grade/refutation/claim status, and ``validate_run``
    re-checks the run after the bump and again after the appended round.
    """
    loop = read_yaml(run_dir / loop_path)
    budget = loop["budget"]
    # One round costs 1 prover call + N reviewer calls; size the headroom from the
    # most recent attempt's reviewer set (default 3 = the Espresso reviewer trio).
    n_reviewers = len((attempts[-1].get("reviewer_ids") if attempts else None) or []) or 3
    budget["max_rounds"] = budget["max_rounds"] + 1
    budget["max_agent_calls"] = budget["max_agent_calls"] + 1 + n_reviewers
    loop["budget"] = budget
    write_yaml(run_dir / loop_path, loop)
    validate_run(run_dir)


def _selected_route_allocation_stop(
    run_dir: Path, selected_strategy_id: str | None
) -> tuple[str, str] | None:
    """Return a Time Allocation stop signal for the selected route, if present."""

    if selected_strategy_id is None:
        return None
    try:
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        rel = manifest.get("optional_artifacts", {}).get("time_allocation")
        if not rel:
            return None
        plan = read_yaml(run_dir / rel)
    except Exception:  # noqa: BLE001 - advisory schedule must fail closed elsewhere
        return None
    for entry in plan.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("route_id") != selected_strategy_id:
            continue
        action = entry.get("action")
        if action in {"ask_human", "stop"}:
            return str(action), str(entry.get("reason") or "")
    return None


def _record_time_allocation_stop(
    run_dir: Path, *, loop_path: str, action: str, reason: str
) -> None:
    """Persist a scheduler stop without changing any attempt/math status."""

    loop = read_yaml(run_dir / loop_path)
    loop["loop_decision"] = action
    loop["decision_reason"] = (
        "Time Allocation selected "
        f"{action} for the current route after reviewer-objection scheduling signals."
        + (f" {reason}" if reason else "")
    )
    write_yaml(run_dir / loop_path, loop)
    validate_run(run_dir)


def continue_prover_loop(
    *,
    run_dir: Path,
    provider: str = "stub",
    model: str = "gpt-4.1",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    reviewer_set: str = "espresso",
    max_rounds: int | None = None,
    client: LLMClient | None = None,
) -> dict:
    """Run bounded Prover<->Reviewer rounds until a stop condition.

    Returns ``{"loop_report", "stop_reason", "rounds_run", "notes"}``. Requires
    the run to already have a resolved human gate + ``prover_loop.yaml`` (the
    orchestrator's resume step or the CLI ``resolve-agent-gate`` builds these).

    Holds the cross-process run writer lock (re-entrant under ``resume_run``),
    so two concurrent loop continuations cannot interleave or duplicate prover
    rounds (Goal 35B).
    """

    run_dir = Path(run_dir)
    with run_writer_lock(run_dir, action="prover loop"):
        return _continue_prover_loop_locked(
            run_dir=run_dir,
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
            reviewer_set=reviewer_set,
            max_rounds=max_rounds,
            client=client,
        )


def _continue_prover_loop_locked(
    *,
    run_dir: Path,
    provider: str,
    model: str,
    api_key_env: str,
    external_agent_command_env: str,
    reviewer_set: str,
    max_rounds: int | None,
    client: LLMClient | None,
) -> dict:
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    loop_path = manifest.get("optional_artifacts", {}).get("prover_loop")
    if not loop_path:
        raise RuntimeError("run has no prover_loop.yaml (resolve the human gate first)")

    notes: list[str] = []
    rounds_run = 0
    stop_reason = "clean"
    escalated = False  # Goals 44-53 option A: at most one budget-granted repair round

    while True:
        loop = read_yaml(run_dir / loop_path)
        budget = loop["budget"]
        decision = loop.get("loop_decision")
        attempts = loop.get("attempts", [])

        # Blocker state per round (drives the clean / no-progress / escalation stops).
        by_round = _round_blockers(attempts)
        latest = max(by_round) if by_round else None
        latest_has_blockers = latest is not None and bool(by_round[latest])
        window = sorted(by_round)[-NO_PROGRESS_WINDOW:] if by_round else []
        no_progress = (
            latest is not None
            and len(window) >= NO_PROGRESS_WINDOW
            and all(by_round[r] == by_round[latest] for r in window)
        )

        # --- STOP checks (before appending another round) ---
        if decision in {"ask_human", "stop"}:
            stop_reason = f"loop_decision_{decision}"
            break

        cap = budget["max_rounds"] if max_rounds is None else min(max_rounds, budget["max_rounds"])
        budget_rounds_hit = budget["rounds_used"] >= cap
        budget_calls_hit = budget["agent_calls_used"] >= budget["max_agent_calls"]

        # Goals 44-53 option A: when the round/agent-call budget is the ONLY thing
        # stopping a loop that still has unresolved blocking objections (which have
        # become blocked_* subgoals), grant EXACTLY ONE more targeted round to attack
        # the highest-value blocked subgoal -- append_prover_loop_round selects and
        # NAMES that subgoal. The grant only bumps the time-allocation budget (never
        # a math status); the kernel re-validates the appended round.
        escalate_now = (
            (budget_rounds_hit or budget_calls_hit)
            and not escalated
            and latest_has_blockers
            and not no_progress
            # Only to REACH round 2 (where the named-subgoal attack happens): a
            # quick_test-style 1-round effort budget. Never beyond round 2, so
            # normal_pass+ (which already reaches round 2) is unaffected.
            and budget["rounds_used"] < 2
            # Only when the EFFORT budget is the cap -- never override an explicit
            # caller-supplied max_rounds (e.g. a deliberate single-round probe).
            and max_rounds is None
        )
        if (budget_rounds_hit or budget_calls_hit) and not escalate_now:
            stop_reason = "budget_rounds" if budget_rounds_hit else "budget_agent_calls"
            break

        if latest is not None and not by_round[latest]:
            stop_reason = "clean"
            break
        if no_progress:
            stop_reason = "no_progress"
            break

        allocation_stop = _selected_route_allocation_stop(
            run_dir, loop.get("selected_strategy_id")
        )
        if allocation_stop is not None:
            action, reason = allocation_stop
            _record_time_allocation_stop(
                run_dir, loop_path=loop_path, action=action, reason=reason
            )
            stop_reason = f"time_allocation_{action}"
            break

        if escalate_now:
            # Fail closed: if granting the round's budget cannot keep the run valid,
            # stop the loop cleanly rather than propagate (the run is never left
            # over-budget / invalid by a partial grant).
            try:
                _grant_escalation_round(run_dir, loop_path, attempts)
            except Exception as exc:  # noqa: BLE001
                stop_reason = "budget_rounds"
                notes.append(f"escalation grant aborted, stopping: {exc}")
                break
            escalated = True
            notes.append(
                "escalated one targeted repair round: unresolved blocking objections "
                "remained at the effort budget (Goals 44-53 option A)"
            )

        # --- append exactly one round; fail closed AND roll back on error ---
        # Goal 54A: bracket each prover-loop round with provenance so a stall in a
        # deep proof-search round (model call vs validation) is visible. _snapshot_run
        # does not capture agent_events.jsonl, so a failed round's record survives.
        snapshot = _snapshot_run(run_dir)
        _round_started = now_iso()
        _round_t0 = monotonic_ms()
        round_index = rounds_run + 1
        append_event(run_dir, "agent_call_started", stage="prover_loop_round", detail=f"round {round_index}")
        # Goal 65B: record a "started" agent_event before the round so a mid-round
        # process death leaves a dangling started record (see
        # observability.latest_unfinished_agent_call).
        append_agent_event(
            run_dir, agent_name="prover_round", provider=provider, adapter=provider,
            model=model, stage="prover_loop_round", agent_status="started",
            started_at=_round_started, round_index=round_index,
        )
        try:
            append_prover_loop_round(
                run_dir=run_dir,
                provider=provider,
                model=model,
                api_key_env=api_key_env,
                external_agent_command_env=external_agent_command_env,
                reviewer_set=reviewer_set,
                client=client,
            )
            refresh_final_report_disclosures(run_dir)
            validate_run(run_dir)
        except Exception as exc:  # noqa: BLE001 - fail closed + roll back
            # Restore the last valid state so a mid-loop failure never leaves a
            # partially-mutated / invalid run on disk.
            _restore_run(run_dir, snapshot)
            append_agent_event(
                run_dir, agent_name="prover_round", provider=provider, adapter=provider,
                model=model, stage="prover_loop_round", agent_status="failed",
                started_at=_round_started, finished_at=now_iso(),
                duration_ms=monotonic_ms() - _round_t0, round_index=round_index,
                error_summary=str(exc),
            )
            append_event(run_dir, "agent_call_failed", stage="prover_loop_round", detail=str(exc))
            stop_reason = "validation_error"
            notes.append(str(exc))
            break
        append_agent_event(
            run_dir, agent_name="prover_round", provider=provider, adapter=provider,
            model=model, stage="prover_loop_round", agent_status="finished",
            started_at=_round_started, finished_at=now_iso(),
            duration_ms=monotonic_ms() - _round_t0, round_index=round_index,
            output_artifact_path="prover_loop.yaml",
        )
        append_event(run_dir, "agent_call_finished", stage="prover_loop_round", detail=f"round {round_index}")
        rounds_run += 1
        summary = _failure_summary(run_dir)
        if summary:
            notes.append(summary)

    final_loop = read_yaml(run_dir / loop_path)
    return {
        "loop_report": final_loop,
        "stop_reason": stop_reason,
        "rounds_run": rounds_run,
        "notes": notes,
    }
