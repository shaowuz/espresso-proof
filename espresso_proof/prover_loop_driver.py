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

from .io import read_yaml
from .llm_agents import LLMClient, append_prover_loop_round
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
}


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
    """

    run_dir = Path(run_dir)
    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    loop_path = manifest.get("optional_artifacts", {}).get("prover_loop")
    if not loop_path:
        raise RuntimeError("run has no prover_loop.yaml (resolve the human gate first)")

    notes: list[str] = []
    rounds_run = 0
    stop_reason = "clean"

    while True:
        loop = read_yaml(run_dir / loop_path)
        budget = loop["budget"]
        decision = loop.get("loop_decision")
        attempts = loop.get("attempts", [])

        # --- STOP checks (before appending another round) ---
        if decision in {"ask_human", "stop"}:
            stop_reason = f"loop_decision_{decision}"
            break
        cap = budget["max_rounds"] if max_rounds is None else min(max_rounds, budget["max_rounds"])
        if budget["rounds_used"] >= cap:
            stop_reason = "budget_rounds"
            break
        if budget["agent_calls_used"] >= budget["max_agent_calls"]:
            stop_reason = "budget_agent_calls"
            break

        by_round = _round_blockers(attempts)
        if by_round:
            latest = max(by_round)
            if not by_round[latest]:
                stop_reason = "clean"
                break
            # no-progress: the last NO_PROGRESS_WINDOW rounds share one blocker set.
            window = sorted(by_round)[-NO_PROGRESS_WINDOW:]
            if len(window) >= NO_PROGRESS_WINDOW and all(
                by_round[r] == by_round[latest] for r in window
            ):
                stop_reason = "no_progress"
                break

        # --- append exactly one round; fail closed on validation error ---
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
            validate_run(run_dir)
        except Exception as exc:  # noqa: BLE001 - fail closed
            stop_reason = "validation_error"
            notes.append(str(exc))
            break
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
