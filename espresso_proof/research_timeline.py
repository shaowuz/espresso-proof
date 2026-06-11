"""Research timeline — a display-only, artifact-derived activity log (Goal 28).

It turns the run's already-validated artifacts and the per-agent status board
into a compact human-readable timeline ("Claim locked", "3 candidate references
found", "Counterexample attacks proof route only; not a claim refutation",
"Route continued with quick_test effort").

It is PURELY display: it reads artifacts, never writes, never sets or upgrades
any proof/source/evidence/grade/claim status, and a skipped/failed/running agent
is shown as skipped/failed/running — never as done/checked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent_run_status import AGENT_TITLES, load_agent_run_status
from .io import read_yaml

# tone -> for the renderer's colour discipline. "earned" is the only green tone
# and is used ONLY for genuinely earned, kernel-checked evidence.
# info/neutral = grey-blue, warn = amber, skip = grey, fail = red.


def _hhmm(iso: str | None) -> str:
    if not iso or not isinstance(iso, str) or "T" not in iso:
        return ""
    clock = iso.split("T", 1)[1]
    return clock[:5]  # HH:MM


def _event(time: str | None, text: str, tone: str = "info") -> dict[str, Any]:
    return {"time": _hhmm(time), "text": text, "tone": tone}


def build_timeline(run_dir: Path) -> list[dict[str, Any]]:
    """Build the ordered, display-only timeline for a run."""

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts", {})

    def art(name: str) -> dict[str, Any]:
        rel = artifacts.get(name)
        return read_yaml(run_dir / rel) if rel and (run_dir / rel).exists() else {}

    claim = art("locked_claim")
    brief = art("strategy_brief")
    human_choice = art("human_choice")
    literature = art("literature_review")
    examples = art("examples")
    counterexamples = art("counterexamples")

    board = load_agent_run_status(run_dir)
    entries = {e.get("agent"): e for e in (board or {}).get("agents", [])}

    events: list[tuple[str, dict[str, Any]]] = []

    def add(sort_key: str | None, text: str, tone: str = "info") -> None:
        events.append((sort_key or "", _event(sort_key, text, tone)))

    # Claim + routes (front-half).
    cu = entries.get("claim_understanding", {})
    if claim.get("locked"):
        add(cu.get("finished_at") or cu.get("last_updated"), "Claim locked.", "info")
    routes = brief.get("top_strategies") or []
    if routes:
        add(cu.get("finished_at") or cu.get("last_updated"),
            f"{len(routes)} proof route(s) generated (preliminary).", "info")

    # Evidence agents — honest per status.
    def agent_events(agent: str) -> None:
        e = entries.get(agent)
        if not e:
            return
        title = AGENT_TITLES.get(agent, agent)
        status = e.get("status")
        started = e.get("started_at")
        finished = e.get("finished_at") or e.get("last_updated")
        if status == "running":
            add(started, f"{title} started…", "warn")
            return
        if status == "skipped":
            add(finished, f"{title} skipped (contributes no evidence).", "skip")
            return
        if status == "failed":
            add(finished, f"{title} failed — needs check.", "fail")
            return
        if status != "done":
            return
        # done: describe what was actually recorded (never overclaim).
        if agent == "literature":
            n = len(literature.get("candidate_references") or [])
            add(finished, f"Literature review: {n} candidate source(s) found "
                          "(proposed, not reference support yet).", "info")
        elif agent == "examples":
            n = len(examples.get("examples") or [])
            add(finished, f"Examples recorded: {n} (tested on instances, not a universal proof).", "info")
        elif agent == "counterexamples":
            tests = counterexamples.get("tests") or []
            admissible = any(
                t.get("refutes_claim") and t.get("satisfies_all_claim_hypotheses") is True
                for t in tests
            )
            if admissible:
                add(finished, "Counterexample search: an admissible checked counterexample refutes the claim.", "fail")
            elif tests:
                add(finished, f"Counterexample search: {len(tests)} test(s); none refute the claim.", "info")
            else:
                add(finished, "Counterexample search finished; no admissible refutation recorded.", "info")
        elif agent == "failure_patterns":
            add(finished, f"{title} completed (advisory; not a refutation or a proof).", "info")
        else:
            add(finished, f"{title} completed.", "info")

    for agent in ("literature", "examples", "counterexamples", "failure_patterns"):
        agent_events(agent)

    # Route continuation (human choice). Effort renders as words (Goal 31).
    from .descriptions import effort_phrase

    selected = human_choice.get("selected_strategy")
    if selected and selected != "none":
        effort = human_choice.get("effort") or "quick_test"
        add(None, f"Route continued with {effort_phrase(effort)} effort.", "info")

    # Stable order: by timestamp where present, otherwise keep insertion order.
    indexed = list(enumerate(events))
    indexed.sort(key=lambda pair: (pair[1][0], pair[0]))
    return [ev for _, (_, ev) in indexed]


__all__ = ["build_timeline"]
