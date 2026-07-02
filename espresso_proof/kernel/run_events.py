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

Deliberate omission: there is no per-file ``artifact_written`` event. Stages
mutate whole snapshot scopes, so per-artifact granularity is recovered from the
run manifest + artifacts on disk; the timeline records the stage bracketing
(``stage_started`` .. ``stage_ok`` / ``validation_failed`` + ``rollback``).
"""

from __future__ import annotations

import json
import time
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
    # Goal 54A additive kinds (backward-compatible; consumers reading only the
    # original stage_*/validation_* kinds are unaffected).
    "agent_call_started",
    "agent_call_finished",
    "agent_call_failed",
    "server_job",
    "replay",
    "note",
    # Goal 65 control-flow: the Prover pass was intentionally not run because the
    # front half asked to stop before proof search (loop_control stop/ask_human).
    "prover_pass_skipped",
    # Pivot PR2: machinery (not a human) selected the strategy at the prover
    # human gate; the artifacts record selected_by/resolved_by='auto' with
    # human_confirmed=false. Diagnostic timeline entry only.
    "auto_choice_selected",
)


def append_event(run_dir: Path, kind: str, *, stage: str = "", detail: str = "") -> None:
    """Append one event line. Diagnostic only; never raises into the runtime.

    Goal 54A: every record also carries a monotonic_ms stamp (integer ms from a
    monotonic clock) so stage-duration / silent-gap math does not depend on
    wall-clock skew. Additive field; consumers reading kind/stage/detail are
    unaffected.
    """

    if kind not in EVENT_KINDS:
        return  # unknown kinds are dropped, not invented
    try:
        run_dir = Path(run_dir).resolve()
        path = run_dir / EVENTS_FILENAME
        if path.parent != run_dir:  # path confinement (defensive; EVENTS_FILENAME is fixed)
            return
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "monotonic_ms": time.monotonic_ns() // 1_000_000,
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
