"""Orchestration-facing Update Inbox service boundary.

The kernel module owns the schema and low-level append/mark helpers. This
service is the runtime gate for late blockers: it accepts only fresh,
origin-grounded, scheduling-only events, then optionally asks Time Allocation to
consume those events into a valid scheduling plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from espresso_proof.kernel.io import read_yaml
from espresso_proof.kernel.models import UpdateInboxEntry, UpdateInboxReport
from espresso_proof.kernel.pressure_status import artifact_revision_hash
from espresso_proof.kernel.time_allocation import write_time_allocation_plan
from espresso_proof.kernel.update_inbox import (
    UPDATE_INBOX_FILENAME,
    append_update_inbox_event,
    load_update_inbox,
)
from espresso_proof.validation.manifest import validate_relative_artifact_path

TIME_ALLOCATION_FILENAME = "time_allocation.yaml"

_SELF_ORIGIN_ARTIFACTS = {
    UPDATE_INBOX_FILENAME,
    TIME_ALLOCATION_FILENAME,
}
_PROCESS_ONLY_ORIGIN_ARTIFACTS = {
    "stage_status.yaml",
}
_SELF_ORIGIN_STAGES = {
    "update_inbox",
    "time_allocation",
}


class UpdateInboxServiceError(ValueError):
    """Raised when an orchestration update event is not admissible."""


def _canonical_origin_path(raw_path: str) -> str:
    return validate_relative_artifact_path(
        "update_inbox.origin_artifact",
        raw_path,
        path_name="origin artifact",
    ).as_posix()


def _declared_origin_artifacts(run_dir: Path, manifest: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for section_name in ("artifacts", "optional_artifacts"):
        for raw_path in (manifest.get(section_name) or {}).values():
            paths.add(_canonical_origin_path(str(raw_path)))

    round_index_rel = (manifest.get("optional_artifacts") or {}).get("prover_round_index")
    if round_index_rel and (run_dir / round_index_rel).exists():
        round_index = read_yaml(run_dir / round_index_rel) or {}
        for raw_round in round_index.get("rounds") or []:
            if isinstance(raw_round, dict) and raw_round.get("artifact_path"):
                paths.add(_canonical_origin_path(str(raw_round["artifact_path"])))
    return paths


def append_scheduling_update(
    run_dir: Path,
    event: UpdateInboxEntry | dict[str, Any],
    *,
    timeout_seconds: float = 30.0,
) -> UpdateInboxReport:
    """Append one fresh scheduling event after origin validation.

    This is the intended orchestration entrypoint for late blockers. It rejects
    forged origins, stale origin hashes, self-originating inbox/allocation
    events, non-advisory payloads, and validator-failed events before the kernel
    append helper writes anything.
    """

    run_dir = Path(run_dir)
    entry = validate_scheduling_update(run_dir, event)
    return append_update_inbox_event(
        run_dir,
        entry,
        timeout_seconds=timeout_seconds,
    )


def validate_scheduling_update(
    run_dir: Path,
    event: UpdateInboxEntry | dict[str, Any],
) -> UpdateInboxEntry:
    """Return a schema-valid event if it is a fresh scheduling-only signal."""

    run_dir = Path(run_dir)
    data = event.model_dump(mode="json") if isinstance(event, UpdateInboxEntry) else dict(event)
    try:
        entry = UpdateInboxEntry.model_validate(data)
    except ValidationError as exc:
        raise UpdateInboxServiceError(
            "update inbox event failed scheduling-only schema validation"
        ) from exc
    if entry.effect != "scheduling_only" or entry.advisory_only is not True:
        raise UpdateInboxServiceError("update inbox events must be scheduling-only/advisory")
    if entry.validator_ok is not True:
        raise UpdateInboxServiceError("update inbox event origin must be validator_ok")
    if entry.origin_stage in _SELF_ORIGIN_STAGES:
        raise UpdateInboxServiceError("update inbox event cannot originate from scheduling services")
    if Path(entry.origin_artifact).name in _SELF_ORIGIN_ARTIFACTS:
        raise UpdateInboxServiceError("update inbox event cannot originate from scheduling artifacts")
    if Path(entry.origin_artifact).name in _PROCESS_ONLY_ORIGIN_ARTIFACTS:
        raise UpdateInboxServiceError("update inbox event cannot originate from process-only artifacts")

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    if entry.locked_claim_id != claim["id"]:
        raise UpdateInboxServiceError("update inbox event locked_claim_id does not match run claim")

    origin_rel = validate_relative_artifact_path(
        f"update_inbox.{entry.id}.origin_artifact",
        entry.origin_artifact,
        path_name="origin artifact",
    )
    if origin_rel.as_posix() not in _declared_origin_artifacts(run_dir, manifest):
        raise UpdateInboxServiceError(
            "update inbox event origin artifact is not a declared run artifact"
        )
    origin_path = run_dir / origin_rel
    if not origin_path.exists():
        raise UpdateInboxServiceError(
            f"update inbox event origin artifact is missing: {entry.origin_artifact}"
        )
    current_hash = artifact_revision_hash(run_dir, origin_rel)
    if entry.artifact_revision_or_hash != current_hash:
        raise UpdateInboxServiceError("update inbox event origin artifact hash is stale")
    return entry


def write_time_allocation_from_update_inbox(run_dir: Path):
    """Consume valid Update Inbox signals into a scheduling allocation plan.

    ``applied_at`` is stamped by the kernel writer only after the allocation
    artifact itself is written and validated. The stamp means "consumed by
    scheduling"; it is not a blocker-resolution or mathematical-trust status.
    Callers that are not in a temporary gate-transition state should still run
    full ``validate_run`` after their larger transaction settles.
    """

    return write_time_allocation_plan(run_dir)


def update_inbox_consumption_rows(run_dir: Path) -> list[dict[str, Any]]:
    """Display/service rows clarifying that applied_at is scheduling-consumed."""

    report = load_update_inbox(run_dir)
    if report is None:
        return []
    rows: list[dict[str, Any]] = []
    for entry in report.entries:
        rows.append(
            {
                "id": entry.id,
                "type": entry.type,
                "applied_at": entry.applied_at,
                "scheduling_consumed": entry.applied_at is not None,
                "resolved": False,
                "resolution_state": (
                    "consumed_by_scheduling"
                    if entry.applied_at is not None
                    else "pending_scheduling"
                ),
            }
        )
    return rows


__all__ = [
    "UpdateInboxServiceError",
    "append_scheduling_update",
    "update_inbox_consumption_rows",
    "validate_scheduling_update",
    "write_time_allocation_from_update_inbox",
]
