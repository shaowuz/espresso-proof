"""Goal 43C: the latest-round ledger — explanatory, never evidence.

After a continue/resume, the cockpit shows what the round actually did:
selected strategy, target subgoal + local claim, effort, reviewer blockers,
stop reason, whether the subgoal graph changed, and what remains open.

This is a SIDECAR (``latest_round.yaml``) the kernel never reads as evidence.
Every label routes through the safe display vocabulary (``descriptions.py``),
the target subgoal is search-state only (no status upgrade), and the
``graph_changed`` signal is the cheapest honest one: the subgoal-graph builder
preserves its ``generated_at`` stamp when nothing changed, so a differing stamp
across the continue means the graph genuinely changed.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.ui.descriptions import effort_phrase, loop_stop_phrase
from espresso_proof.kernel.io import read_yaml, write_yaml

LEDGER_FILE = "latest_round.yaml"


def graph_stamp(run_dir: Path) -> str:
    """The subgoal-graph idempotency stamp, or '' when absent."""

    path = Path(run_dir) / "proof_goal_graph.yaml"
    if not path.exists():
        return ""
    try:
        return str(read_yaml(path).get("generated_at") or "")
    except Exception:  # noqa: BLE001
        return ""


def _prover_loop(run_dir: Path) -> dict | None:
    try:
        manifest = read_yaml(run_dir / "run_manifest.yaml")
    except Exception:  # noqa: BLE001
        return None
    rel = (manifest.get("optional_artifacts", {}) or {}).get("prover_loop")
    if not rel or not (run_dir / rel).exists():
        return None
    try:
        data = read_yaml(run_dir / rel)
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def write_latest_round_ledger(
    run_dir: Path,
    *,
    selected_strategy: str,
    prev_graph_stamp: str | None = None,
) -> dict:
    """Build and write the advisory latest-round ledger after a continue."""

    run_dir = Path(run_dir)
    loop = _prover_loop(run_dir) or {}
    attempts = loop.get("attempts") or []
    latest = attempts[-1] if attempts else {}
    budget = loop.get("budget") or {}

    # reviewer blockers: free-text attempt blockers, clipped (already scrubbed
    # product text, never a raw enum or status).
    blockers = [str(b)[:160] for b in (latest.get("blockers") or [])][:6]

    # stop reason: loop_decision -> safe phrase (loop_decision_* keys exist in
    # the display vocabulary). Falls back to a neutral phrase, never a token.
    loop_decision = str(loop.get("loop_decision") or "")
    stop_phrase = (
        loop_stop_phrase(f"loop_decision_{loop_decision}")
        if loop_decision
        else loop_stop_phrase(None)
    )

    # target subgoal (search-state only).
    from espresso_proof.kernel.subgoal_graph import assemble_subgoal_graph, select_next_subgoal

    graph = assemble_subgoal_graph(run_dir)
    target = select_next_subgoal(graph) or {}
    summary = (graph or {}).get("summary") or {}
    open_count = summary.get("open")

    stamp_now = graph_stamp(run_dir)
    graph_changed = (
        prev_graph_stamp is not None
        and stamp_now != ""
        and stamp_now != prev_graph_stamp
    )

    ledger = {
        "selected_strategy": str(selected_strategy),
        "target_subgoal_id": target.get("id"),
        "local_claim": (str(target.get("statement"))[:280] if target.get("statement") else None),
        "effort": effort_phrase(budget.get("approved_effort")),
        "reviewer_blockers": blockers,
        "stop_reason": stop_phrase,
        "rounds_used": budget.get("rounds_used"),
        "max_rounds": budget.get("max_rounds"),
        "graph_changed": bool(graph_changed),
        "open_subgoals": open_count if isinstance(open_count, int) else None,
        "advisory_only": True,
    }
    write_yaml(run_dir / LEDGER_FILE, ledger)
    return ledger


def load_latest_round(run_dir: Path) -> dict | None:
    """Read the advisory latest-round ledger, or None. Display only."""

    path = Path(run_dir) / LEDGER_FILE
    if not path.exists():
        return None
    try:
        data = read_yaml(path)
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None
