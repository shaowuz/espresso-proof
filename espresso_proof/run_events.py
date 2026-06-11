"""Unified per-run event timeline ``events.jsonl`` (Goal 35B).

One append-only JSONL file per run recording what the runtime DID — stage
starts, validation results, rollbacks, job completion — so a developer can open
one file and reconstruct what happened to a run.

Diagnostic only, by construction:

- events carry no proof/source/evidence/grade/claim status and are never read
  by validators, renderers, or any status logic;
- writes are path-confined to ``events.jsonl`` inside the run directory;
- only explicit, short string fields are recorded — never the environment, an
  exception object's attributes, or arbitrary payloads;
- event writing must never break the runtime: failures to append are swallowed
  (the run artifacts, not the timeline, are the source of truth).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

EVENTS_FILENAME = "events.jsonl"
_DETAIL_MAX_CHARS = 400

EVENT_KINDS = (
    "stage_started",
    "validation_passed",
    "validation_failed",
    "rollback",
    "stage_ok",
    "job_done",
    "job_failed",
    "lock_denied",
)


def append_event(run_dir: Path, kind: str, *, stage: str = "", detail: str = "") -> None:
    """Append one event line. Diagnostic only; never raises into the runtime."""

    if kind not in EVENT_KINDS:
        return  # unknown kinds are dropped, not invented
    try:
        run_dir = Path(run_dir).resolve()
        path = run_dir / EVENTS_FILENAME
        if path.parent != run_dir:  # path confinement (defensive; EVENTS_FILENAME is fixed)
            return
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "kind": kind,
        }
        if stage:
            record["stage"] = str(stage)[:_DETAIL_MAX_CHARS]
        if detail:
            record["detail"] = str(detail)[:_DETAIL_MAX_CHARS]
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - the timeline must never break the runtime
        return


def read_events(run_dir: Path) -> list[dict]:
    """Read the timeline (for tests/diagnostics). Malformed lines are skipped."""

    path = Path(run_dir) / EVENTS_FILENAME
    if not path.exists():
        return []
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


__all__ = ["append_event", "read_events", "EVENTS_FILENAME", "EVENT_KINDS"]
