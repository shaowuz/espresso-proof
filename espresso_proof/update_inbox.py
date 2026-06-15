"""V2 Task 05: write-safe, scheduling-only Update Inbox.

The inbox is an optional manifest artifact. It records late/advisory scheduling
signals only; it never owns proof/source/evidence/theorem/refutation/grade
status and is not consumed by prover/reviewer loops in this task.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .io import read_yaml, write_yaml
from .models import UpdateInboxEntry, UpdateInboxReport
from .run_lock import run_writer_lock

UPDATE_INBOX_FILENAME = "update_inbox.yaml"


class UpdateInboxConflictError(RuntimeError):
    """A dedupe key already exists with different event content."""


def _manifest_path(run_dir: Path) -> Path:
    return Path(run_dir) / "run_manifest.yaml"


def _empty_report(run_dir: Path) -> UpdateInboxReport:
    manifest = read_yaml(_manifest_path(run_dir))
    claim = read_yaml(Path(run_dir) / manifest["artifacts"]["locked_claim"])
    return UpdateInboxReport.model_validate(
        {
            "claim_id": str(claim.get("id", "")),
            "run_id": str(manifest.get("run_id", "")),
            "entries": [],
        }
    )


def canonical_dedupe_key(entry: UpdateInboxEntry | dict[str, Any]) -> str:
    data = entry.model_dump(mode="json") if isinstance(entry, UpdateInboxEntry) else dict(entry)
    route = data.get("route_id_or_step_id")
    route_key = "whole_claim" if route is None else str(route)
    parts = [
        str(data.get("type", "")),
        str(data.get("locked_claim_id", "")),
        str(data.get("origin_artifact", "")),
        str(data.get("origin_artifact_id_or_path", "")),
        str(data.get("artifact_revision_or_hash", "")),
        route_key,
    ]
    return "|".join(parts)


def _event_fingerprint(entry: UpdateInboxEntry) -> str:
    data = entry.model_dump(mode="json")
    for ignored in ("id", "created_at", "dedupe_key", "applied_at"):
        data.pop(ignored, None)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def load_update_inbox(run_dir: Path) -> UpdateInboxReport | None:
    manifest = read_yaml(_manifest_path(run_dir))
    rel = manifest.get("optional_artifacts", {}).get("update_inbox")
    if not rel:
        return None
    return UpdateInboxReport.model_validate(read_yaml(Path(run_dir) / rel))


def append_update_inbox_event(
    run_dir: Path,
    event: UpdateInboxEntry | dict[str, Any],
    *,
    timeout_seconds: float = 30.0,
) -> UpdateInboxReport:
    """Append one Update Inbox event under the run writer lock.

    Duplicate dedupe keys with identical payload are idempotent. The same dedupe
    key with different payload raises before writing. The inbox artifact is
    written before the manifest pointer, so a failed first append cannot advance
    manifest state.
    """

    run_dir = Path(run_dir)
    data = event.model_dump(mode="json") if isinstance(event, UpdateInboxEntry) else dict(event)
    data["dedupe_key"] = canonical_dedupe_key(data)
    entry = UpdateInboxEntry.model_validate(data)
    with run_writer_lock(run_dir, action="append update inbox", timeout_seconds=timeout_seconds):
        report = load_update_inbox(run_dir) or _empty_report(run_dir)
        if entry.locked_claim_id != report.claim_id:
            raise ValueError("update inbox event locked_claim_id does not match run claim")
        existing_by_key = {item.dedupe_key: item for item in report.entries}
        existing = existing_by_key.get(entry.dedupe_key)
        if existing is not None:
            if _event_fingerprint(existing) == _event_fingerprint(entry):
                return report
            raise UpdateInboxConflictError(
                f"update inbox dedupe_key already exists with different payload: {entry.dedupe_key}"
            )

        report = UpdateInboxReport.model_validate(
            {
                "schema_version": report.schema_version,
                "claim_id": report.claim_id,
                "run_id": report.run_id,
                "entries": [
                    *[item.model_dump(mode="json") for item in report.entries],
                    entry.model_dump(mode="json"),
                ],
            }
        )
        write_yaml(run_dir / UPDATE_INBOX_FILENAME, report.model_dump(mode="json"))
        manifest = read_yaml(_manifest_path(run_dir))
        manifest.setdefault("optional_artifacts", {})["update_inbox"] = UPDATE_INBOX_FILENAME
        manifest.setdefault("dependencies", {})["update_inbox"] = {"depends_on": []}
        write_yaml(_manifest_path(run_dir), manifest)
        return report


__all__ = [
    "UPDATE_INBOX_FILENAME",
    "UpdateInboxConflictError",
    "append_update_inbox_event",
    "canonical_dedupe_key",
    "load_update_inbox",
]
