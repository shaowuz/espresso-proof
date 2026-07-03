"""Goal 54A: per-run runtime observability.

Append-only, path-confined, diagnostic-only per-run provenance so a developer can
answer "which stage/agent stalled, for how long, what did it last write, did
validation/rollback happen?" from artifacts alone — without terminal scrollback.

Hard safety contract (this module is provenance, NEVER evidence):

- Nothing written here can upgrade proof/source/evidence/grade/claim/refutation
  status. Records are scrubbed of any status-bearing key (see ``_FORBIDDEN_KEYS``)
  and are never read by validators, renderers, or status logic.
- Writing must never break the runtime: every public writer fails closed
  (swallows its own errors) so a logging fault cannot corrupt a run.
- Paths are confined to the run directory; absolute/``..`` escapes are rejected.
- No environment dumps, secrets, or full private prompts are written — only
  short explicit fields and digests.

Artifacts produced (all OPTIONAL — old runs validate without them):

- ``events.jsonl``        — the run timeline (see ``run_events``; this module
                            only enriches it with monotonic timing + new kinds).
- ``agent_events.jsonl``  — one record per agent/model call.
- ``timings.yaml``        — aggregated stage/agent durations + stall signals.

Deliberate, backward-compatible scope note on ``events.jsonl``: the Goal-54A spec
sketches a rich per-event schema (event_id, event_type, validation_status, state
labels, ...). The existing ``events.jsonl`` is intentionally the *minimal canonical
timeline* (kind/stage/detail) consumed by local diagnostics; rewriting it to
the full schema would break that consumer. Instead, this layer (a) adds
``monotonic_ms`` + the agent_call_*/server_job/replay/note kinds to ``events.jsonl``,
and (b) carries the rich per-call fields (provider/adapter/model/duration/status/
fallback/validation/timeout/run_id/...) in ``agent_events.jsonl``. Correlation across
the three files is by run directory + stage + ``monotonic_ms`` (+ best-effort run_id).
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.run_events import EVENTS_FILENAME, append_event, read_events

AGENT_EVENTS_FILENAME = "agent_events.jsonl"
TIMINGS_FILENAME = "timings.yaml"

_STR_MAX = 400
_SCHEMA_VERSION = 1

# Keys that would let a diagnostic record masquerade as a status upgrade. Any
# record carrying one of these (or a *_status that names one) is rejected before
# it can be written. This is the structural guarantee that logs are not evidence.
_FORBIDDEN_KEYS = frozenset(
    {
        "source_backed",
        "proved",
        "proof_complete",
        "proof_status",
        "grade",
        "idea_grade",
        "refuted",
        "checked",
        "source_checked",
        "computation_checked",
        "instance_checked",
        "examples_checked",
        "evidence_status",
        "human_confirmed",
    }
)

# Substrings that flag a likely secret/credential field name (defensive: callers
# should never pass these, but we drop them rather than risk writing a secret).
_SECRETISH = ("api_key", "apikey", "secret", "token", "password", "authorization", "bearer")


def now_iso() -> str:
    """UTC ISO-8601 timestamp."""
    return datetime.now(UTC).isoformat()


def monotonic_ms() -> int:
    """Monotonic clock in integer milliseconds (for durations/gaps; not wall time)."""
    return time.monotonic_ns() // 1_000_000


def _confined_path(run_dir: Path, filename: str) -> Path | None:
    """Resolve ``run_dir/filename`` and confirm it stays directly inside run_dir.
    Returns None (caller fails closed) on any escape."""
    try:
        run_dir = Path(run_dir).resolve()
        path = (run_dir / filename).resolve()
        if path.parent != run_dir:
            return None
        return path
    except Exception:  # noqa: BLE001 - never raise into the runtime
        return None


def _clean_value(value):
    """Coerce a field value to a short, safe, JSON-serializable scalar/None."""
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    return str(value)[:_STR_MAX]


def _scrub_record(fields: dict) -> dict | None:
    """Drop secret-ish keys, reject forbidden status keys, and bound string sizes.
    Returns None if the record carries a forbidden status-upgrade key (caller
    fails closed)."""
    record: dict = {}
    for key, value in fields.items():
        k = str(key)
        lk = k.lower()
        if k in _FORBIDDEN_KEYS:
            return None  # a status-upgrade field must never reach a log line
        if any(s in lk for s in _SECRETISH):
            continue  # silently drop secret-looking fields
        # A *_status field naming a forbidden status is also rejected.
        if lk.endswith("_status") and isinstance(value, str) and value in _FORBIDDEN_KEYS:
            return None
        record[k] = _clean_value(value)
    return record


def _append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_agent_event(
    run_dir: Path,
    *,
    agent_name: str,
    provider: str,
    adapter: str,
    stage: str,
    agent_status: str,
    started_at: str | None = None,
    finished_at: str | None = None,
    duration_ms: int | None = None,
    model: str | None = None,
    run_id: str | None = None,
    output_artifact_path: str | None = None,
    fallback_used: bool = False,
    fallback_reason: str | None = None,
    validation_status: str = "not_run",
    timeout_ms: int | None = None,
    timed_out: bool = False,
    retry_index: int = 0,
    prompt_digest: str | None = None,
    error_summary: str | None = None,
    **extra,
) -> None:
    """Append one ``agent_events.jsonl`` record. Diagnostic only; fails closed.

    ``agent_status`` is one of: started | finished | failed | timed_out | fallback.
    Never raises into the runtime; a forbidden status-upgrade key drops the line.
    """
    try:
        path = _confined_path(run_dir, AGENT_EVENTS_FILENAME)
        if path is None:
            return
        fields = {
            "schema_version": _SCHEMA_VERSION,
            "run_id": run_id,
            "agent_name": agent_name,
            "provider": provider,
            "adapter": adapter,
            "model": model,
            "stage": stage,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "agent_status": agent_status,
            "output_artifact_path": output_artifact_path,
            "fallback_used": bool(fallback_used),
            "fallback_reason": fallback_reason,
            "validation_status": validation_status,
            "timeout_ms": timeout_ms,
            "timed_out": bool(timed_out),
            "retry_index": int(retry_index),
            "prompt_digest": prompt_digest,
            "error_summary": error_summary,
        }
        fields.update(extra)
        record = _scrub_record(fields)
        if record is None:
            return
        _append_jsonl(path, record)
    except Exception:  # noqa: BLE001 - observability must never break a run
        return


@contextmanager
def observed_agent_call(
    run_dir: Path,
    *,
    agent_name: str,
    stage: str,
    provider: str,
    model: str | None = None,
    adapter: str | None = None,
    output_artifact_path: str | None = None,
    client=None,
    **extra,
):
    """Wrap ONE external agent call in paired Goal-65 observability records.

    Enter: an ``agent_call_started`` events.jsonl line plus a ``started``
    agent_events.jsonl record. Success: a matching ``finished`` record with
    duration. Failure: a ``failed`` (or ``timed_out`` when the exception
    carries a ``timeout_seconds`` attribute — duck-typed so this module never
    imports the agents layer) record plus an ``agent_call_failed`` event, then
    the exception is re-raised unchanged.

    Diagnostic only: records go through ``_scrub_record`` (fail closed), and
    nothing here can create, upgrade, or preserve any trusted status.
    """

    common = {
        "agent_name": agent_name,
        "provider": provider,
        "adapter": adapter or "",
        "stage": stage,
        "model": model,
        "output_artifact_path": output_artifact_path,
        **extra,
    }
    started_wall = time.monotonic()
    append_event(run_dir, "agent_call_started", stage=stage, detail=agent_name)
    append_agent_event(
        run_dir, agent_status="started", started_at=now_iso(), **common
    )
    try:
        yield
    except Exception as exc:
        duration_ms = int((time.monotonic() - started_wall) * 1000)
        timeout_s = getattr(exc, "timeout_seconds", None)
        append_agent_event(
            run_dir,
            agent_status="timed_out" if timeout_s is not None else "failed",
            finished_at=now_iso(),
            duration_ms=duration_ms,
            timed_out=timeout_s is not None,
            timeout_ms=int(timeout_s * 1000) if timeout_s is not None else None,
            error_summary=str(exc)[:500],
            attempts=getattr(client, "last_attempts", None),
            **common,
        )
        append_event(run_dir, "agent_call_failed", stage=stage, detail=agent_name)
        raise
    duration_ms = int((time.monotonic() - started_wall) * 1000)
    append_agent_event(
        run_dir,
        agent_status="finished",
        finished_at=now_iso(),
        duration_ms=duration_ms,
        attempts=getattr(client, "last_attempts", None),
        **common,
    )


def read_agent_events(run_dir: Path) -> list[dict]:
    """Read agent_events.jsonl (tests/diagnostics). Malformed lines are skipped."""
    path = Path(run_dir) / AGENT_EVENTS_FILENAME
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def _agent_call_key(record: dict) -> tuple:
    """Identity for pairing a ``started`` agent-event with its terminal record.
    Same (stage, agent_name, retry_index, round_index) = same call."""
    return (
        record.get("stage"),
        record.get("agent_name"),
        record.get("retry_index", 0),
        record.get("round_index"),
    )


# Terminal statuses that close an open ``started`` agent call.
_TERMINAL_AGENT_STATUSES = frozenset({"finished", "failed", "timed_out"})


def latest_unfinished_agent_call(run_dir: Path) -> dict | None:
    """Goal 65B: return the most recent agent call that recorded ``started`` but
    no matching terminal (``finished``/``failed``/``timed_out``) record, or None.

    This is the reconstruction primitive for a mid-call PROCESS death: when the
    worker dies *inside* an external agent call (as in the order-3 postmortem),
    no terminal record is ever written, so the dangling ``started`` record is the
    only evidence of *where* the run died. Diagnostic only; fails closed."""
    try:
        open_calls: dict[tuple, dict] = {}
        order: list[tuple] = []
        for rec in read_agent_events(run_dir):
            status = rec.get("agent_status")
            key = _agent_call_key(rec)
            if status == "started":
                open_calls[key] = rec
                order.append(key)
            elif status in _TERMINAL_AGENT_STATUSES:
                open_calls.pop(key, None)
        for key in reversed(order):
            if key in open_calls:
                return open_calls[key]
        return None
    except Exception:  # noqa: BLE001 - diagnostics must never break a run
        return None


def write_timings(run_dir: Path, *, run_started_at: str | None = None) -> dict | None:
    """Aggregate ``events.jsonl`` + ``agent_events.jsonl`` into ``timings.yaml``.

    Derives stage durations (paired ``stage_started`` → ``stage_ok`` /
    ``validation_failed`` by monotonic_ms when present), agent-call durations,
    total duration, time-to-first-artifact, heartbeat count, max silent gap, and
    timeout count. Diagnostic only; fails closed (returns None on any error)."""
    try:
        path = _confined_path(run_dir, TIMINGS_FILENAME)
        if path is None:
            return None
        events = read_events(run_dir)
        agent_events = read_agent_events(run_dir)

        # Monotonic stamps (preferred) for gap/duration math; fall back to count-only.
        mono = [e["monotonic_ms"] for e in events if isinstance(e.get("monotonic_ms"), int)]
        stage_durations: dict[str, int] = {}
        open_stage: dict[str, int] = {}
        first_artifact_ms: int | None = None
        run_start_mono = mono[0] if mono else None
        for e in events:
            kind = e.get("kind")
            stage = e.get("stage") or ""
            m = e.get("monotonic_ms")
            if not isinstance(m, int):
                continue
            if kind == "stage_started" and stage:
                open_stage[stage] = m
            elif kind in {"stage_ok", "validation_failed"} and stage in open_stage:
                stage_durations[stage] = stage_durations.get(stage, 0) + (m - open_stage.pop(stage))
            if first_artifact_ms is None and kind in {"stage_ok", "validation_passed"} and run_start_mono is not None:
                first_artifact_ms = m - run_start_mono

        agent_durations: dict[str, int] = {}
        timeout_count = 0
        for a in agent_events:
            d = a.get("duration_ms")
            name = a.get("agent_name") or a.get("stage") or "agent"
            if isinstance(d, int):
                agent_durations[name] = agent_durations.get(name, 0) + d
            if a.get("timed_out"):
                timeout_count += 1

        max_gap = None
        if len(mono) >= 2:
            max_gap = max(b - a for a, b in zip(mono, mono[1:]))
        total = (mono[-1] - mono[0]) if len(mono) >= 2 else None

        timings = {
            "schema_version": _SCHEMA_VERSION,
            "run_started_at": run_started_at,
            "run_finished_at": now_iso(),
            "total_duration_ms": total,
            "stage_durations_ms": stage_durations,
            "agent_call_durations_ms": agent_durations,
            "queue_wait_ms": None,
            "validation_ms": None,
            "rollback_ms": None,
            "time_to_first_artifact_ms": first_artifact_ms,
            "time_to_first_route_card_ms": None,
            "last_progress_at": now_iso() if events else None,
            "heartbeat_count": len(events),
            "max_silent_gap_ms": max_gap,
            "timeout_count": timeout_count,
        }
        write_yaml(path, timings)
        return timings
    except Exception:  # noqa: BLE001 - observability must never break a run
        return None


def register_observability_artifacts(run_dir: Path) -> None:
    """Register the per-run observability files in the manifest's
    ``optional_artifacts`` (only those that exist on disk). Optional + fail-closed
    so old runs are unaffected and a logging fault never corrupts the manifest."""
    try:
        run_dir = Path(run_dir)
        manifest_path = run_dir / "run_manifest.yaml"
        if not manifest_path.exists():
            return
        manifest = read_yaml(manifest_path)
        optional = dict(manifest.get("optional_artifacts") or {})
        changed = False
        for key, filename in (
            ("events", EVENTS_FILENAME),
            ("agent_events", AGENT_EVENTS_FILENAME),
            ("timings", TIMINGS_FILENAME),
        ):
            if (run_dir / filename).exists() and optional.get(key) != filename:
                optional[key] = filename
                changed = True
        if changed:
            manifest["optional_artifacts"] = optional
            write_yaml(manifest_path, manifest)
    except Exception:  # noqa: BLE001 - never break a run
        return


__all__ = [
    "AGENT_EVENTS_FILENAME",
    "TIMINGS_FILENAME",
    "now_iso",
    "monotonic_ms",
    "append_agent_event",
    "read_agent_events",
    "latest_unfinished_agent_call",
    "write_timings",
    "register_observability_artifacts",
]
