"""provisional Strategy Grader -> Time Allocation plan.

The plan is a manifest-registered optional sidecar. It is scheduling-only:
route scores, missing context, and effort hints can choose what to try next, but
cannot upgrade proof/source/evidence/theorem/refutation/grade status.
"""

from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import tempfile
from typing import Any

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import (
    Claim,
    RunManifest,
    StrategyBrief,
    StrategySynthesis,
    TimeAllocationPlan,
)
from espresso_proof.kernel.run_lock import run_writer_lock
from espresso_proof.kernel.update_inbox import mark_update_inbox_entries_applied
from espresso_proof.validation.errors import KernelValidationError

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


def _snapshot_files(paths: list[Path]) -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.exists() else None for path in paths}


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _restore_files(snapshot: dict[Path, bytes | None]) -> None:
    for path, content in snapshot.items():
        if content is None:
            if path.exists():
                path.unlink()
            continue
        _write_bytes_atomic(path, content)


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


def _validate_written_time_allocation(run_dir: Path) -> None:
    from espresso_proof.kernel.validators import (
        _validate_time_allocation,
        validate_yaml_model,
    )

    manifest = validate_yaml_model(run_dir / "run_manifest.yaml", RunManifest)
    claim = validate_yaml_model(
        run_dir / manifest.artifacts["locked_claim"],
        Claim,
    )
    strategy_synthesis = validate_yaml_model(
        run_dir / manifest.artifacts["strategy_synthesis"],
        StrategySynthesis,
    )
    strategy_brief = validate_yaml_model(
        run_dir / manifest.artifacts["strategy_brief"],
        StrategyBrief,
    )
    time_allocation = validate_yaml_model(
        run_dir / manifest.optional_artifacts["time_allocation"],
        TimeAllocationPlan,
    )
    _validate_time_allocation(
        time_allocation,
        claim,
        manifest,
        run_dir,
        strategy_synthesis,
        strategy_brief,
    )


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
    # There is no explicit route -> earned source-grounding step binding.
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


# Both kinds of answer release a route's objection count. They are different
# TYPES so provenance stays visible everywhere else; for the purpose of "has
# this question been answered", machinery answering it counts.
_SCHEDULING_ANSWER_TYPES = {"human_scheduling_decision", "auto_scheduling_decision"}


def _answered_routes(
    update_inbox: dict[str, Any] | None,
    *,
    types: frozenset[str] = frozenset(_SCHEDULING_ANSWER_TYPES),
) -> set[str]:
    """Routes whose scheduler question a human has answered.

    The objection count is deliberately monotone — consumption is not
    resolution — which is correct for automated rounds and wrong once a person
    has actually answered. Without this, a route that reached ask_human could
    never leave it: the rebuilt plan asked the same question forever and the
    remaining approved budget was unreachable. A recorded
    ``human_scheduling_decision`` supersedes the objection rounds for its route
    (scheduling only; it can never touch a mathematical status).
    """

    if not update_inbox:
        return set()
    answered: set[str] = set()
    for entry in update_inbox.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") not in types:
            continue
        if (
            entry.get("effect") != "scheduling_only"
            or entry.get("advisory_only") is not True
            or entry.get("validator_ok") is not True
        ):
            continue
        route = entry.get("route_id_or_step_id")
        if route:
            answered.add(str(route))
    return answered


def _reviewer_objection_round_counts(update_inbox: dict[str, Any] | None) -> dict[str, int]:
    """Count distinct reviewer-objection rounds per route from Update Inbox.

    Multiple reviewer entries from the same round still represent one scheduling
    round. The objection prose is not copied into the inbox and is not inspected
    here; only validator-approved scheduling pointers count. A route the human
    has answered restarts at zero — see :func:`_human_answered_routes`.
    """

    if not update_inbox:
        return {}
    answered = _answered_routes(update_inbox)
    rounds_by_route: dict[str, set[tuple[str, str]]] = {}
    for entry in update_inbox.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "reviewer_objection":
            continue
        if (
            entry.get("effect") != "scheduling_only"
            or entry.get("advisory_only") is not True
        ):
            continue
        if entry.get("validator_ok") is not True:
            continue
        route = entry.get("route_id_or_step_id")
        if not route:
            continue
        rounds_by_route.setdefault(str(route), set()).add(
            (
                str(entry.get("origin_artifact", "")),
                str(entry.get("artifact_revision_or_hash", "")),
            )
        )
    return {
        route: (0 if route in answered else len(rounds))
        for route, rounds in rounds_by_route.items()
    }


def _consumed_reviewer_objection_entry_ids(
    update_inbox: dict[str, Any] | None,
    allocation_route_ids: set[str],
) -> set[str]:
    """Reviewer-objection entries consumed by this allocation snapshot.

    Applied entries still count on future recomputation. The stamp records that
    a valid scheduling pointer fed a persisted allocation, not that the
    objection was resolved.
    """

    if not update_inbox or not allocation_route_ids:
        return set()
    consumed: set[str] = set()
    for entry in update_inbox.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "reviewer_objection":
            continue
        if (
            entry.get("effect") != "scheduling_only"
            or entry.get("advisory_only") is not True
        ):
            continue
        if entry.get("validator_ok") is not True:
            continue
        route = entry.get("route_id_or_step_id")
        if not route or str(route) not in allocation_route_ids:
            continue
        entry_id = str(entry.get("id", "")).strip()
        if entry_id:
            consumed.add(entry_id)
    return consumed


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _route_action(
    route: dict[str, Any],
    *,
    source_support_missing: bool,
    reviewer_objection_rounds: int,
) -> tuple[str, str]:
    if reviewer_objection_rounds >= 3:
        return (
            "ask_human",
            "Repeated reviewer objections reached the scheduler; ask human before "
            "another automated round.",
        )
    if reviewer_objection_rounds >= 1:
        return (
            "repair_attempt",
            "Reviewer objection reached Update Inbox; schedule a bounded repair attempt.",
        )
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
    update_inbox = _read_optional(run_dir, manifest, "update_inbox")
    reviewer_objection_rounds = _reviewer_objection_round_counts(update_inbox)
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
        action, reason = _route_action(
            route,
            source_support_missing=source_missing,
            reviewer_objection_rounds=reviewer_objection_rounds.get(str(route["id"]), 0),
        )
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
    run_dir = Path(run_dir)
    with run_writer_lock(run_dir, action="write time allocation"):
        plan = build_time_allocation_plan(run_dir)
        if plan is None:
            return None
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        update_inbox = _read_optional(run_dir, manifest, "update_inbox")
        allocation_route_ids = {
            entry.route_id for entry in plan.entries if entry.route_id is not None
        }
        consumed_update_ids = _consumed_reviewer_objection_entry_ids(
            update_inbox,
            allocation_route_ids,
        )
        snapshot = _snapshot_files(
            [
                run_dir / TIME_ALLOCATION_FILENAME,
                run_dir / "run_manifest.yaml",
                run_dir / "update_inbox.yaml",
            ]
        )
        try:
            write_yaml(run_dir / TIME_ALLOCATION_FILENAME, plan.model_dump(mode="json"))
            manifest = read_yaml(run_dir / "run_manifest.yaml")
            manifest.setdefault("optional_artifacts", {})["time_allocation"] = TIME_ALLOCATION_FILENAME
            depends_on = ["strategy_synthesis", "strategy_brief", "idea_grade"]
            if "update_inbox" in manifest.get("optional_artifacts", {}):
                depends_on.append("update_inbox")
            manifest.setdefault("dependencies", {})["time_allocation"] = {
                "depends_on": depends_on
            }
            write_yaml(run_dir / "run_manifest.yaml", manifest)
            _validate_written_time_allocation(run_dir)
            if "update_inbox" in manifest.get("optional_artifacts", {}):
                mark_update_inbox_entries_applied(run_dir, consumed_update_ids)
                _validate_written_time_allocation(run_dir)
        except Exception:
            _restore_files(snapshot)
            raise
        return plan


def ensure_prover_time_allocation_plan(run_dir: Path) -> TimeAllocationPlan:
    """Return the manifest-registered V2 allocation plan required by Prover.

    Older runs can still validate without ``time_allocation.yaml``. Starting a
    new Prover pass is stricter: if the plan is absent we build it from graded
    Strategy output; generator-only route lists fail closed because
    ``build_time_allocation_plan`` returns ``None`` for ungraded routes.
    """

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    rel = manifest.get("optional_artifacts", {}).get("time_allocation")
    if rel:
        if rel != TIME_ALLOCATION_FILENAME:
            raise KernelValidationError("prover requires canonical time_allocation.yaml")
        return TimeAllocationPlan.model_validate(read_yaml(run_dir / rel))

    plan = write_time_allocation_plan(run_dir)
    if plan is None:
        raise KernelValidationError(
            "prover requires time_allocation.yaml from graded Strategy output"
        )
    return plan


def time_allocation_prompt_payload(plan: TimeAllocationPlan) -> dict[str, Any]:
    """Compact scheduling payload for Prover prompts; status-free by schema."""

    return {
        "artifact": TIME_ALLOCATION_FILENAME,
        "allocation_state": plan.allocation_state,
        "advisory_only": plan.advisory_only,
        "effect": plan.effect,
        "missing_context": list(plan.missing_context),
        "entries": [entry.model_dump(mode="json") for entry in plan.entries],
    }


__all__ = [
    "TIME_ALLOCATION_FILENAME",
    "build_time_allocation_plan",
    "ensure_prover_time_allocation_plan",
    "time_allocation_prompt_payload",
    "write_time_allocation_plan",
]


def _human_answered_routes(update_inbox: dict[str, Any] | None) -> set[str]:
    """Routes a PERSON answered. Distinct from :func:`_answered_routes`, which
    also counts machinery's answer: both release the objection count, but only
    one of them is somebody's decision, and no surface may confuse the two."""

    return _answered_routes(
        update_inbox, types=frozenset({"human_scheduling_decision"})
    )
