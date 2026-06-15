"""V2 Task 04: provisional Strategy Grader -> Time Allocation plan.

The plan is a manifest-registered optional sidecar. It is scheduling-only:
route scores, missing context, and effort hints can choose what to try next, but
cannot upgrade proof/source/evidence/theorem/refutation/grade status.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .io import read_yaml, write_yaml
from .models import TimeAllocationPlan

TIME_ALLOCATION_FILENAME = "time_allocation.yaml"
_GRADED_ROUTE_FIELDS = {
    "id",
    "score",
    "status",
    "main_risk",
    "source_status",
    "estimated_effort",
    "next_loop_plan",
}


def _read_artifact(run_dir: Path, manifest: dict[str, Any], key: str) -> dict[str, Any]:
    rel = manifest.get("artifacts", {}).get(key)
    if rel and (run_dir / rel).exists():
        return read_yaml(run_dir / rel)
    return {}


def _read_optional(run_dir: Path, manifest: dict[str, Any], key: str) -> dict[str, Any] | None:
    rel = manifest.get("optional_artifacts", {}).get(key)
    if rel and (run_dir / rel).exists():
        return read_yaml(run_dir / rel)
    return None


def _graded_routes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    routes = [route for route in value if isinstance(route, dict)]
    if not routes:
        return []
    if not all(_GRADED_ROUTE_FIELDS.issubset(route) for route in routes):
        return []
    return routes


def _route_source(
    run_dir: Path, manifest: dict[str, Any]
) -> tuple[list[dict[str, Any]], str | None, list[str]]:
    missing: list[str] = []
    brief = _read_artifact(run_dir, manifest, "strategy_brief")
    routes = _graded_routes(brief.get("top_strategies"))
    if routes:
        return routes, "strategy_brief", missing

    missing.append("strategy_brief")
    synthesis = _read_artifact(run_dir, manifest, "strategy_synthesis")
    routes = _graded_routes(synthesis.get("routes"))
    if routes:
        return routes, "strategy_synthesis", missing
    return [], None, missing


def _human_preference_missing(human_choice: dict[str, Any]) -> bool:
    return human_choice.get("status") != "selected" or not human_choice.get("selected_strategy")


def _source_support_missing(source_grounding: dict[str, Any]) -> bool:
    # Task 04 has no explicit route -> earned source-grounding step binding.
    # Source support is step-local, so neither a route label nor an unrelated
    # source_backed step can suppress the source-pinning allocation.
    return True


def _pressure_missing(pressure_status: dict[str, Any] | None, kind: str) -> bool:
    if not pressure_status:
        return True
    for entry in pressure_status.get("entries") or []:
        if not isinstance(entry, dict) or entry.get("kind") != kind:
            continue
        return entry.get("pressure_state") == "pending_not_run_or_partial"
    return True


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _route_action(route: dict[str, Any], *, source_support_missing: bool) -> tuple[str, str]:
    if source_support_missing:
        return (
            "source_pinning",
            "Pin exact source/theorem context before treating this route as supported.",
        )
    if route.get("failed_tests") or route.get("status") in {"likely_false", "refuted"}:
        return ("repair_attempt", "Repair or narrow the route before spending prover time.")
    if route.get("estimated_effort") == "quick_test":
        return ("cheap_check", "Run a cheap bounded check before deeper exploration.")
    return (
        "bounded_proof_exploration",
        "Explore this route under a bounded unchecked budget.",
    )


def _budget_for_action(action: str) -> str:
    return {
        "source_pinning": "small",
        "cheap_check": "tiny",
        "bounded_proof_exploration": "bounded",
        "repair_attempt": "small",
        "ask_human": "human",
        "stop": "none",
    }[action]


def build_time_allocation_plan(run_dir: Path) -> TimeAllocationPlan | None:
    """Build the first provisional allocation plan, if graded routes exist.

    Generator-only route lists fail closed: without graded route fields there is
    no plan. Missing strategy brief, human preference, source support, or pressure
    context is recorded but does not block the first allocation.
    """

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = _read_artifact(run_dir, manifest, "locked_claim")
    routes, route_artifact, missing_context = _route_source(run_dir, manifest)
    if not routes or route_artifact is None:
        return None

    human_choice = _read_artifact(run_dir, manifest, "human_choice")
    source_grounding = _read_artifact(run_dir, manifest, "source_grounding")
    pressure_status = _read_optional(run_dir, manifest, "pressure_status")
    source_missing = _source_support_missing(source_grounding)
    if _human_preference_missing(human_choice):
        missing_context.append("human_preference")
    if source_missing:
        missing_context.append("source_support")
    if _pressure_missing(pressure_status, "examples"):
        missing_context.append("examples_pressure")
    if _pressure_missing(pressure_status, "counterexamples"):
        missing_context.append("counterexamples_pressure")

    entries: list[dict[str, Any]] = []
    for index, route in enumerate(routes, start=1):
        action, reason = _route_action(route, source_support_missing=source_missing)
        entries.append(
            {
                "id": f"alloc_{index:03d}",
                "route_id": str(route["id"]),
                "action": action,
                "budget": _budget_for_action(action),
                "unchecked": True,
                "advisory_only": True,
                "effect": "scheduling_only",
                "reason": reason,
            }
        )
    if _human_preference_missing(human_choice):
        entries.append(
            {
                "id": f"alloc_{len(entries) + 1:03d}",
                "route_id": None,
                "action": "ask_human",
                "budget": "human",
                "unchecked": True,
                "advisory_only": True,
                "effect": "scheduling_only",
                "reason": "Optional route preference is missing; ask only for scheduling guidance.",
            }
        )
    if not entries:
        entries.append(
            {
                "id": "alloc_001",
                "route_id": None,
                "action": "stop",
                "budget": "none",
                "unchecked": True,
                "advisory_only": True,
                "effect": "scheduling_only",
                "reason": "No graded route is available for bounded scheduling.",
            }
        )

    return TimeAllocationPlan.model_validate(
        {
            "claim_id": str(claim.get("id", "")),
            "run_id": str(manifest.get("run_id", "")),
            "generated_at": datetime.now(UTC).isoformat(),
            "trigger": "strategy_grader",
            "allocation_state": "TIME_ALLOCATION_STARTED",
            "strategy_artifact": route_artifact,
            "advisory_only": True,
            "effect": "scheduling_only",
            "missing_context": _unique(missing_context),
            "entries": entries,
        }
    )


def write_time_allocation_plan(run_dir: Path) -> TimeAllocationPlan | None:
    plan = build_time_allocation_plan(run_dir)
    if plan is None:
        return None
    run_dir = Path(run_dir)
    write_yaml(run_dir / TIME_ALLOCATION_FILENAME, plan.model_dump(mode="json"))
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["time_allocation"] = TIME_ALLOCATION_FILENAME
    manifest.setdefault("dependencies", {})["time_allocation"] = {
        "depends_on": ["strategy_synthesis", "strategy_brief", "idea_grade"]
    }
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return plan


__all__ = [
    "TIME_ALLOCATION_FILENAME",
    "build_time_allocation_plan",
    "write_time_allocation_plan",
]
