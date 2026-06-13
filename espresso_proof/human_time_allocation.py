"""Goal 54C: human feedback drives deterministic, advisory time allocation.

The human's strategy feedback (score + priority + note) must actually influence
the NEXT reasoning round — route ordering, the per-attempt allocation mode, and
prompt context — while staying strictly advisory. A high score or a "focus"
priority buys attention/compute; it never buys mathematical status.

Hard contract (this layer is scheduling arithmetic, NEVER evidence):

- it reads the advisory ``strategy_feedback.yaml`` sidecar (already strict /
  status-free) plus the kernel ``strategy_brief`` + ``human_choice`` artifacts,
  and derives a ``human_time_allocation_context`` packet;
- the packet is itself a SIDECAR (``human_time_allocation_context.yaml``), NOT a
  kernel run artifact, so ``validate_run`` never reads it as evidence;
- it can only reorder candidate routes, recommend an allocation mode, and feed
  prompt context. It can never set proof/source/evidence/grade/claim status;
- feedback naming an unknown/stale strategy is ignored fail-closed (it can never
  create a route);
- a "reduce" priority must never hide the only valid route.
"""

from __future__ import annotations

from pathlib import Path

from .io import read_yaml, write_yaml
from .models import HumanTimeAllocationContext, RouteAllocationEntry
from .strategy_feedback import feedback_summary, latest_by_strategy, load_feedback

CONTEXT_FILE = "human_time_allocation_context.yaml"

# Exact deterministic scheduling weights (Goal 54C spec). These tune attention
# and compute only; they are not, and can never become, an evidence score.
PRIORITY_BONUS: dict[str, int] = {"focus": 30, "repair": 20, "normal": 0, "reduce": -40}
SELECTED_ROUTE_BONUS = 25
RECENT_FEEDBACK_BONUS = 10
_DEFAULT_BASE = 50


def _clamp(value: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, value))


def advisory_scheduling_score(
    *, base: int, priority: str, is_selected: bool, is_recent: bool
) -> int:
    """Goal 54C deterministic scheduling score:

        advisory_score = clamp(base
                               + priority_bonus[priority]
                               + (25 if human-selected else 0)
                               + (10 if newest feedback item else 0), 0, 100)

    Advisory attention/budget only — never a proof/grade/evidence score."""
    score = base + PRIORITY_BONUS.get(priority, 0)
    if is_selected:
        score += SELECTED_ROUTE_BONUS
    if is_recent:
        score += RECENT_FEEDBACK_BONUS
    return _clamp(score)


def recommended_allocation_for_priority(
    priority: str, *, has_blockers: bool, only_valid_route: bool
) -> str:
    """Map a route's human priority to a per-attempt allocation mode.

    Reviewer objections outrank taste: unresolved blockers always force
    ``repair``. Otherwise ``repair`` -> repair-oriented; ``reduce`` ->
    ``cheap_check`` UNLESS it is the only valid route (a 'reduce' must never hide
    the only route); ``focus`` / ``normal`` -> ``expand``."""
    if has_blockers:
        return "repair"
    if priority == "repair":
        return "repair"
    if priority == "reduce":
        return "expand" if only_valid_route else "cheap_check"
    return "expand"  # focus, normal


def _read_artifact(run_dir: Path, manifest: dict, key: str) -> dict:
    rel = manifest.get("artifacts", {}).get(key)
    if rel and (run_dir / rel).exists():
        return read_yaml(run_dir / rel)
    return {}


def _read_optional(run_dir: Path, manifest: dict, key: str) -> dict | None:
    rel = manifest.get("optional_artifacts", {}).get(key)
    if rel and (run_dir / rel).exists():
        return read_yaml(run_dir / rel)
    return None


def _unresolved_blockers_by_strategy(prover_loop: dict | None) -> dict[str, int]:
    """Latest recorded open-blocker count per strategy (advisory tie-break only)."""
    counts: dict[str, int] = {}
    for attempt in (prover_loop or {}).get("attempts", []) or []:
        sid = str(attempt.get("strategy_id"))
        counts[sid] = len(attempt.get("blockers") or [])
    return counts


def _selected_strategy(human_choice: dict) -> str | None:
    if human_choice.get("status") != "selected":
        return None
    selected = human_choice.get("selected_strategy")
    if not selected or selected == "none":
        return None
    return str(selected)


def build_time_allocation_context(run_dir: Path) -> HumanTimeAllocationContext:
    """Derive the advisory time-allocation context for a run (pure read).

    Routes come from the strategy brief (kernel-valid candidates only); feedback
    that names an unknown strategy is ignored fail-closed. Deterministic order:
    the human-selected route first, then higher advisory_score, then fewer
    unresolved blockers, then stable strategy id."""

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief = _read_artifact(run_dir, manifest, "strategy_brief")
    human_choice = _read_artifact(run_dir, manifest, "human_choice")
    prover_loop = _read_optional(run_dir, manifest, "prover_loop")

    selected = _selected_strategy(human_choice)
    latest = latest_by_strategy(run_dir)
    log_items = load_feedback(run_dir).items
    most_recent_sid = log_items[-1].strategy_id if log_items else None
    blocker_counts = _unresolved_blockers_by_strategy(prover_loop)

    routes_in = brief.get("top_strategies") or []
    valid_ids = [str(item.get("id")) for item in routes_in if item.get("id")]
    only_valid_route = len(valid_ids) == 1

    entries: list[RouteAllocationEntry] = []
    for item in routes_in:
        sid = str(item.get("id") or "")
        if not sid:
            continue
        feedback = latest.get(sid)
        priority = feedback.priority if feedback else "normal"
        base = _clamp(int(item.get("score", _DEFAULT_BASE)))
        is_selected = sid == selected
        is_recent = sid == most_recent_sid
        blockers = blocker_counts.get(sid, 0)
        entries.append(
            RouteAllocationEntry(
                strategy_id=sid,
                base_score=base,
                priority=priority,
                is_selected=is_selected,
                is_most_recent_feedback=is_recent,
                advisory_score=advisory_scheduling_score(
                    base=base, priority=priority, is_selected=is_selected, is_recent=is_recent
                ),
                unresolved_blockers=blockers,
                recommended_allocation=recommended_allocation_for_priority(
                    priority, has_blockers=blockers > 0, only_valid_route=only_valid_route
                ),
            )
        )

    entries.sort(
        key=lambda r: (not r.is_selected, -r.advisory_score, r.unresolved_blockers, r.strategy_id)
    )

    hint = ""
    if entries:
        top = entries[0]
        hint = (
            f"Advisory steering: spend the next round on route {top.strategy_id} "
            f"({top.recommended_allocation}). This orders attention only and never "
            f"earns evidence; routes stay candidate until the kernel earns a label."
        )

    return HumanTimeAllocationContext(
        selected_strategy=selected, routes=entries, derived_allocation_hint=hint
    )


def selected_route_priority(context: HumanTimeAllocationContext) -> str:
    """The human priority on the selected route ('normal' if none)."""
    for route in context.routes:
        if route.is_selected:
            return route.priority
    return "normal"


def priority_for_strategy(context: HumanTimeAllocationContext, strategy_id: str | None) -> str:
    """The human priority on a specific route ('normal' if unknown)."""
    for route in context.routes:
        if route.strategy_id == strategy_id:
            return route.priority
    return "normal"


def only_valid_route(context: HumanTimeAllocationContext) -> bool:
    return len(context.routes) == 1


def write_time_allocation_context(run_dir: Path) -> HumanTimeAllocationContext:
    """Build + persist the advisory context to a SIDECAR (not a kernel artifact).
    Fail-closed: never raises into the runtime."""
    context = build_time_allocation_context(run_dir)
    try:
        write_yaml(Path(run_dir) / CONTEXT_FILE, context.model_dump(mode="json"))
    except Exception:  # noqa: BLE001 - advisory sidecar must never break a run
        pass
    return context


def load_time_allocation_context(run_dir: Path) -> HumanTimeAllocationContext | None:
    """Read the persisted sidecar (advisory display). None if absent/malformed."""
    path = Path(run_dir) / CONTEXT_FILE
    if not path.exists():
        return None
    try:
        return HumanTimeAllocationContext.model_validate(read_yaml(path))
    except Exception:  # noqa: BLE001 - advisory sidecar fails closed to absent
        return None


def time_allocation_prompt_context(
    run_dir: Path, selected_strategy: str | None = None
) -> str:
    """A concise advisory steering summary for the next prover/reviewer prompt:
    the human feedback summary plus the derived allocation hint. Advisory only;
    the kernel re-validates every round output, so this can never upgrade a
    status."""
    summary = feedback_summary(run_dir, selected_strategy)
    if not summary:
        return ""
    context = build_time_allocation_context(run_dir)
    if context.derived_allocation_hint:
        summary = f"{summary}\n{context.derived_allocation_hint}"
    return summary


__all__ = [
    "CONTEXT_FILE",
    "PRIORITY_BONUS",
    "SELECTED_ROUTE_BONUS",
    "RECENT_FEEDBACK_BONUS",
    "advisory_scheduling_score",
    "recommended_allocation_for_priority",
    "build_time_allocation_context",
    "write_time_allocation_context",
    "load_time_allocation_context",
    "time_allocation_prompt_context",
    "selected_route_priority",
    "priority_for_strategy",
    "only_valid_route",
]
