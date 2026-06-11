"""Per-agent run-status board for a run (Goal 27).

The board (``agent_run_status.yaml``) records the PROCESS state of the evidence /
understanding agents so the fast core pass, the on-demand single-agent runner,
and the dashboard can show honestly which agents have run, are running, were
skipped, or failed -- without ever turning that process state into mathematical
evidence.

Hard invariants (enforced by the model + ``validate_run``):

- the report carries no source/evidence/proof/grade/claim status field;
- ``safe_to_use_as_evidence`` is a display gate (True only when ``status==done``)
  and never upgrades a kernel status;
- a running / failed / skipped / not-run / missing agent never counts as
  evidence (the dashboard/memo read evidence from the validated artifacts and
  gate positive wording on ``status == 'done'``).

This module only reads/writes the board. The kernel (``validate_run``) is the
gate; nothing here interprets mathematical status.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .io import read_yaml, write_yaml
from .models import AgentRunStatusReport

AGENT_RUN_STATUS_FILENAME = "agent_run_status.yaml"

# The agent kinds tracked on the board, in display order. ``claim_understanding``
# is part of the fast core pass; the rest are optional evidence agents that run
# on demand / in parallel.
AGENT_KINDS: list[str] = [
    "claim_understanding",
    "literature",
    "examples",
    "counterexamples",
    "failure_patterns",
]

# Human-readable card titles for the dashboard/memo.
AGENT_TITLES: dict[str, str] = {
    "claim_understanding": "Claim Understanding",
    "literature": "Literature Review",
    "examples": "Examples",
    "counterexamples": "Counterexamples",
    "failure_patterns": "Failure Pattern Audit",
}

# Map an agent to the run-relative artifact path it owns (relative to run_dir).
# Used to record ``artifact_path`` and (for evidence gating) to confirm the
# artifact actually exists. ``failure_patterns`` writes an optional artifact.
AGENT_ARTIFACTS: dict[str, str] = {
    "claim_understanding": "locked_claim.yaml",
    "literature": "literature_review.yaml",
    "examples": "examples.yaml",
    "counterexamples": "counterexamples.yaml",
    "failure_patterns": "failure_pattern_audit.yaml",
}

# Statuses that the dashboard/memo may treat as "this section may be shown as
# real evidence". ONLY ``done``. Everything else renders as Not run / Running /
# Skipped / Needs check and contributes no evidence.
EVIDENCE_READY_STATUS = "done"

# Human-readable status words (Goal 27: missing/running/skipped/failed render as
# Not run / Running / Skipped / Needs check -- never as an empty successful
# result). ``done`` is the only word that signals usable evidence.
STATUS_DISPLAY: dict[str, str] = {
    "not_run": "Not run",
    "queued": "Queued",
    "running": "Running",
    "done": "Done",
    "failed": "Needs check",
    "skipped": "Skipped",
}

# The optional evidence agents (claim_understanding is part of the fast core).
EVIDENCE_AGENTS: list[str] = ["literature", "examples", "counterexamples", "failure_patterns"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def status_path(run_dir: Path) -> Path:
    return Path(run_dir) / AGENT_RUN_STATUS_FILENAME


def has_status(run_dir: Path) -> bool:
    return status_path(run_dir).exists()


def init_agent_run_status(
    run_dir: Path,
    *,
    done: set[str] | None = None,
    skipped: set[str] | None = None,
    notes: list[str] | None = None,
) -> dict:
    """Create (or overwrite) the board for a run and register it as the optional
    manifest artifact ``agent_run_status``.

    Agents in ``done`` are recorded as ``done`` (and, if their artifact exists,
    ``safe_to_use_as_evidence=True``); agents in ``skipped`` as ``skipped``; all
    others as ``not_run``. ``claim_understanding`` is always at least ``done``
    in a scaffolded fast/full run.
    """

    run_dir = Path(run_dir)
    done = set(done or set())
    skipped = set(skipped or set())
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    now = _now()

    agents: list[dict] = []
    for agent in AGENT_KINDS:
        artifact_rel = AGENT_ARTIFACTS[agent]
        artifact_exists = (run_dir / artifact_rel).exists()
        if agent in done:
            status = "done"
        elif agent in skipped:
            status = "skipped"
        else:
            status = "not_run"
        agents.append(
            {
                "agent": agent,
                "status": status,
                "started_at": now if status == "done" else None,
                "finished_at": now if status == "done" else None,
                "artifact_path": artifact_rel if artifact_exists else None,
                "error_summary": None,
                "attempts": 1 if status == "done" else 0,
                "last_updated": now,
                "safe_to_use_as_evidence": status == "done" and artifact_exists,
            }
        )

    report = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": now,
        "last_updated": now,
        "agents": agents,
        "notes": notes
        or [
            "Process audit only; agent status never upgrades proof/source/evidence/grade status.",
            "Only an agent with status='done' may contribute evidence; the kernel still validates it.",
        ],
    }
    AgentRunStatusReport.model_validate(report)
    write_yaml(status_path(run_dir), report)
    manifest.setdefault("optional_artifacts", {})["agent_run_status"] = AGENT_RUN_STATUS_FILENAME
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return report


def load_agent_run_status(run_dir: Path) -> dict | None:
    """Return the board dict, or ``None`` if the run has no board (old runs)."""

    path = status_path(run_dir)
    if not path.exists():
        return None
    return read_yaml(path)


def agent_status(run_dir: Path, agent: str) -> str:
    """Return one agent's status (``not_run`` if the board/agent is absent)."""

    board = load_agent_run_status(run_dir)
    if board is None:
        return "not_run"
    for entry in board.get("agents", []):
        if entry.get("agent") == agent:
            return str(entry.get("status") or "not_run")
    return "not_run"


def evidence_ready(run_dir: Path, agent: str) -> bool:
    """True iff ``agent`` finished (``status == 'done'``).

    The single helper the dashboard/memo use to decide whether a positive
    evidence phrasing for ``agent`` may be shown. Conservative: if the board is
    absent the answer depends on the caller (callers fall back to the legacy
    "render whatever the artifact says" only when no board exists at all).
    """

    return agent_status(run_dir, agent) == EVIDENCE_READY_STATUS


def display_rows(run_dir: Path) -> list[dict]:
    """Per-agent display rows for the dashboard/memo, or ``[]`` if no board.

    Each row: ``agent``, ``title``, ``status``, ``status_word`` (human),
    ``evidence_ready`` (True only when done), ``attempts``, ``error_summary``.
    Order follows ``AGENT_KINDS``.
    """

    board = load_agent_run_status(run_dir)
    if board is None:
        return []
    by_agent = {e.get("agent"): e for e in board.get("agents", [])}
    rows: list[dict] = []
    for agent in AGENT_KINDS:
        entry = by_agent.get(agent, {})
        status = str(entry.get("status") or "not_run")
        rows.append(
            {
                "agent": agent,
                "title": AGENT_TITLES.get(agent, agent),
                "status": status,
                "status_word": STATUS_DISPLAY.get(status, "Not run"),
                "evidence_ready": status == EVIDENCE_READY_STATUS,
                "attempts": int(entry.get("attempts") or 0),
                "error_summary": entry.get("error_summary"),
            }
        )
    return rows


def evidence_incomplete(run_dir: Path) -> bool:
    """True iff some core evidence agent (literature/examples/counterexamples) is
    not yet ``done`` -- i.e. route scores / the route table are PRELIMINARY.

    Returns False when there is no board (legacy runs are rendered as before).
    """

    board = load_agent_run_status(run_dir)
    if board is None:
        return False
    for agent in ("literature", "examples", "counterexamples"):
        if agent_status(run_dir, agent) != EVIDENCE_READY_STATUS:
            return True
    return False


def set_agent_status(
    run_dir: Path,
    agent: str,
    status: str,
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
    error_summary: str | None = None,
    increment_attempt: bool = False,
) -> dict:
    """Update one agent's entry on the board and persist it.

    Creates the board first (all agents ``not_run``) if it does not exist. The
    artifact_path / safe_to_use_as_evidence flags are recomputed from the actual
    artifact presence and the new status, so a status change can never claim an
    absent artifact as evidence.
    """

    run_dir = Path(run_dir)
    if agent not in AGENT_KINDS:
        raise ValueError(f"unknown agent: {agent!r}")
    if not has_status(run_dir):
        init_agent_run_status(run_dir)
    board = load_agent_run_status(run_dir)
    assert board is not None
    now = _now()
    artifact_rel = AGENT_ARTIFACTS[agent]
    artifact_exists = (run_dir / artifact_rel).exists()

    for entry in board["agents"]:
        if entry.get("agent") != agent:
            continue
        if increment_attempt:
            entry["attempts"] = int(entry.get("attempts") or 0) + 1
        if started_at is not None:
            entry["started_at"] = started_at
        if finished_at is not None:
            entry["finished_at"] = finished_at
        entry["status"] = status
        entry["error_summary"] = error_summary
        entry["artifact_path"] = artifact_rel if artifact_exists else None
        entry["safe_to_use_as_evidence"] = status == EVIDENCE_READY_STATUS and artifact_exists
        entry["last_updated"] = now
        break
    board["last_updated"] = now
    AgentRunStatusReport.model_validate(board)
    write_yaml(status_path(run_dir), board)
    return board


__all__ = [
    "AGENT_RUN_STATUS_FILENAME",
    "AGENT_KINDS",
    "AGENT_TITLES",
    "AGENT_ARTIFACTS",
    "EVIDENCE_READY_STATUS",
    "EVIDENCE_AGENTS",
    "STATUS_DISPLAY",
    "init_agent_run_status",
    "load_agent_run_status",
    "agent_status",
    "evidence_ready",
    "evidence_incomplete",
    "display_rows",
    "set_agent_status",
    "has_status",
    "status_path",
]
