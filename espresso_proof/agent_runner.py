"""On-demand / parallel single-agent runner (Goal 27).

``run_single_agent`` runs ONE evidence agent (examples / counterexamples /
literature / failure-pattern audit) against an existing run, writing only that
agent's artifact and updating ``agent_run_status.yaml``. It is the building block
for the on-demand and parallel evidence flow.

Safety / transaction discipline:

- the run must already be kernel-valid before an agent runs;
- the agent runs inside a ``StageRunner`` transaction that snapshots only the
  paths the agent owns (+ the board + the manifest) and rolls back on any
  mutation/validation failure, so a failed agent fails closed and the previous
  valid run is preserved intact;
- on success the board records ``done``; on failure it records ``failed`` with an
  ``error_summary`` (and the artifacts are the rolled-back, previously-valid
  ones);
- retries are allowed and recorded (``attempts`` is incremented per run);
- ``validate_run`` is the gate and is never bypassed -- the agent never upgrades
  proof/source/evidence/grade/claim status; the board is process audit only.

Completion order does not matter: each agent re-validates the whole run, so
examples / counterexamples / literature / failure-patterns can run in any order
or independently and converge to the same valid final state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .agent_run_status import (
    AGENT_KINDS,
    has_status,
    init_agent_run_status,
    set_agent_status,
)
from .failure_pattern_bank import audit_failure_patterns
from .front_half.registry import _STAGE_REGISTRY
from .io import read_text
from .literature_discovery import discover_literature as run_literature_discovery
from .llm_agents import build_client, load_agent_context
from .run_dashboard import write_run_dashboard
from .stage_runner import StageError, StageRunner
from .validators import validate_run

# Each on-demand evidence agent and the run-relative paths it may mutate. The
# transaction snapshots exactly these (plus the board + the manifest), so a
# rollback restores them and an agent never rewrites another agent's artifact.
AGENT_SNAPSHOT_PATHS: dict[str, list[str]] = {
    "examples": ["examples.yaml", "computation_checks.yaml", "tests.yaml"],
    "counterexamples": ["counterexamples.yaml", "tests.yaml"],
    "literature": ["literature_review.yaml"],
    "failure_patterns": ["failure_pattern_audit.yaml"],
}

# Agents that can be run on demand by this runner (claim_understanding is part of
# the fast core pass, not an on-demand evidence agent).
ON_DEMAND_AGENTS: list[str] = ["examples", "counterexamples", "literature", "failure_patterns"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _run_agent_body(run_dir: Path, agent: str, *, client) -> None:
    """Run the agent's actual work, writing only its owned artifact(s)."""

    if agent in ("examples", "counterexamples"):
        normalizer = _STAGE_REGISTRY[agent]
        context = load_agent_context(run_dir)
        normalizer(run_dir=run_dir, client=client, context=context)
        return
    if agent == "literature":
        from .io import read_yaml, write_yaml
        from .models import LiteratureReviewReport

        manifest = read_yaml(run_dir / "run_manifest.yaml")
        artifacts = manifest["artifacts"]
        project_dir = run_dir.parents[1]
        literature = run_literature_discovery(
            project_dir=project_dir,
            locked_claim_path=run_dir / artifacts["locked_claim"],
            hypothesis_ledger_path=run_dir / artifacts["hypothesis_ledger"],
            max_references=8,
            query=read_text(run_dir / "input.md"),
            mode="local",
            providers=None,
        )
        LiteratureReviewReport.model_validate(literature)
        write_yaml(run_dir / artifacts["literature_review"], literature)
        return
    if agent == "failure_patterns":
        audit_failure_patterns(run_dir)
        return
    raise ValueError(f"unknown on-demand agent: {agent!r}")


def skip_agent(run_dir: Path, agent: str, *, reason: str | None = None) -> dict:
    """Mark an agent ``skipped`` (contributes no evidence). Re-validates the run."""

    run_dir = Path(run_dir)
    if agent not in ON_DEMAND_AGENTS:
        raise ValueError(f"cannot skip agent {agent!r}; on-demand agents: {ON_DEMAND_AGENTS}")
    validate_run(run_dir)
    if not has_status(run_dir):
        init_agent_run_status(run_dir)
    set_agent_status(run_dir, agent, "skipped", error_summary=reason)
    validate_run(run_dir)
    _refresh_dashboard(run_dir)
    return {"agent": agent, "status": "skipped"}


def run_single_agent(
    run_dir: Path,
    agent: str,
    *,
    provider: str = "stub",
    model: str = "gpt-4.1",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    client=None,
) -> dict:
    """Run ONE evidence agent on an existing, valid run. Returns a status dict.

    Fails closed: a mutation/validation error rolls the run back to its previous
    valid state and records the agent as ``failed`` (the previous valid artifact
    is preserved). Never upgrades any mathematical status -- ``validate_run`` is
    the gate.
    """

    run_dir = Path(run_dir)
    if agent not in ON_DEMAND_AGENTS:
        raise ValueError(
            f"cannot run agent {agent!r}; on-demand agents: {ON_DEMAND_AGENTS}"
        )
    # Precondition: the run must already be kernel-valid.
    validate_run(run_dir)
    if not has_status(run_dir):
        init_agent_run_status(run_dir)

    # Mark running BEFORE the snapshot so the attempt count + start time survive a
    # rollback (retries are recorded even when the run fails).
    started_at = _now()
    set_agent_status(run_dir, agent, "running", started_at=started_at, increment_attempt=True)
    validate_run(run_dir)

    needs_client = agent in ("examples", "counterexamples")
    if needs_client and client is None:
        client = build_client(
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
        )

    snapshot_paths = AGENT_SNAPSHOT_PATHS[agent] + ["agent_run_status.yaml"]
    runner = StageRunner(run_dir)

    def _mutate() -> None:
        _run_agent_body(run_dir, agent, client=client)
        # Record done inside the transaction so the board + artifact commit or
        # roll back together (safe_to_use_as_evidence is recomputed from the real
        # artifact presence).
        set_agent_status(run_dir, agent, "done", finished_at=_now())

    try:
        runner.run(
            name=f"agent:{agent}",
            mutate=_mutate,
            validate=lambda: validate_run(run_dir),
            state_after=None,
            snapshot_paths=snapshot_paths,
        )
    except StageError as exc:
        # Rolled back to the previous valid state (running board + prior artifacts
        # restored). Record the failure OUTSIDE the transaction so it persists; the
        # previous valid artifact is intact.
        set_agent_status(
            run_dir,
            agent,
            "failed",
            finished_at=_now(),
            error_summary=str(exc)[:500],
        )
        validate_run(run_dir)
        _refresh_dashboard(run_dir)
        return {"agent": agent, "status": "failed", "error": str(exc)}

    _refresh_dashboard(run_dir)
    return {"agent": agent, "status": "done"}


def _refresh_dashboard(run_dir: Path) -> None:
    """Re-render the dashboard if one exists (incremental update). Never fabricates."""

    if (run_dir / "research_dashboard.html").exists():
        write_run_dashboard(run_dir)


__all__ = [
    "ON_DEMAND_AGENTS",
    "AGENT_SNAPSHOT_PATHS",
    "run_single_agent",
    "skip_agent",
]
