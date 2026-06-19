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

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.kernel.agent_run_status import (
    AGENT_KINDS,
    has_status,
    init_agent_run_status,
    set_agent_status,
)
from espresso_proof.claim.claim_clear import (
    CLAIM_CLEAR_BLOCKED_ON_DEMAND_AGENTS,
    ensure_claim_clear_for_downstream,
)
from espresso_proof.claim.persisted_claim_normalization import normalize_persisted_claim_ledger
from espresso_proof.kernel.failure_pattern_bank import (
    audit_failure_patterns,
    build_failure_pattern_audit,
    persist_failure_pattern_audit,
)
from espresso_proof.front_half.registry import _STAGE_REGISTRY
from espresso_proof.front_half.stages.examples import compute_examples, persist_examples
from espresso_proof.front_half.stages.counterexamples import (
    compute_counterexamples,
    persist_counterexamples,
)
from espresso_proof.kernel.io import read_text, read_yaml, write_yaml
from espresso_proof.kernel.models import LiteratureReviewReport
from espresso_proof.literature.literature_discovery import discover_literature as run_literature_discovery
from espresso_proof.agents.llm_agents import build_client, load_agent_context
from espresso_proof.kernel.pressure_status import PRESSURE_STATUS_FILENAME, build_pressure_status
from espresso_proof.orchestration.run_dashboard import write_run_dashboard
from espresso_proof.kernel.run_lock import run_writer_lock
from espresso_proof.kernel.stage_runner import StageError, StageRunner
from espresso_proof.kernel.validators import validate_run

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


@dataclass(frozen=True)
class AgentCandidate:
    """A precomputed, in-memory result of an evidence lane's pure ``compute()``
    (subgoal 04 compute/commit split). It is produced OFF the run writer lock,
    writes NOTHING under ``run_dir``, and is later committed by
    ``run_single_agent(run_dir, agent, candidate=<this>)`` through the unchanged
    lock + StageRunner transaction + ``validate_run`` machinery.

    ``artifacts`` maps each owned artifact KEY to the validated dict to write.
    (failure_patterns also registers its ``optional_artifacts`` entry, but that
    happens inside ``persist_failure_pattern_audit`` against the LIVE manifest at
    commit time, so it needs no extra field here.)
    """

    agent: str
    artifacts: dict


def _compute_literature(run_dir: Path) -> dict:
    """Pure compute for the literature lane: discover + validate, no run_dir writes."""
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
    return literature


def compute_agent(
    run_dir: Path,
    agent: str,
    *,
    provider: str = "stub",
    model: str = "gpt-4.1",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    client=None,
) -> AgentCandidate:
    """Pure COMPUTE half (subgoal 04): build an ``AgentCandidate`` writing NOTHING
    under ``run_dir``. Reads are unrestricted (they take no lock). Commit it via
    ``run_single_agent(run_dir, agent, candidate=<result>)``. This is the unit
    subgoal 05 runs in parallel on a bounded pool before serial commit.
    """
    run_dir = Path(run_dir)
    if agent not in ON_DEMAND_AGENTS:
        raise ValueError(
            f"cannot compute agent {agent!r}; on-demand agents: {ON_DEMAND_AGENTS}"
        )
    if agent in ("examples", "counterexamples"):
        if client is None:
            client = build_client(
                provider=provider,
                model=model,
                api_key_env=api_key_env,
                external_agent_command_env=external_agent_command_env,
            )
        context = load_agent_context(run_dir)
        compute = compute_examples if agent == "examples" else compute_counterexamples
        result = compute(run_dir=run_dir, client=client, context=context)
        return AgentCandidate(agent=agent, artifacts=result["artifacts"])
    if agent == "literature":
        literature = _compute_literature(run_dir)
        return AgentCandidate(agent="literature", artifacts={"literature_review": literature})
    if agent == "failure_patterns":
        report = build_failure_pattern_audit(run_dir)
        return AgentCandidate(
            agent="failure_patterns",
            artifacts={"failure_pattern_audit": report},
        )
    raise ValueError(f"unknown on-demand agent: {agent!r}")


def _commit_candidate(run_dir: Path, agent: str, candidate: AgentCandidate) -> None:
    """Write a precomputed candidate's artifacts. Called from INSIDE the unchanged
    ``_mutate()`` closure, so every write is under the run writer lock, inside the
    StageRunner snapshot scope, and rolls back atomically on a validation failure.
    Reuses the exact same per-lane persist helpers the inline path uses, so the
    on-disk result is byte-identical to a candidate-free run.
    """
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    if agent == "examples":
        claim_id = candidate.artifacts["examples"]["claim_id"]
        persist_examples(run_dir, artifacts, candidate.artifacts, claim_id)
        return
    if agent == "counterexamples":
        persist_counterexamples(run_dir, artifacts, candidate.artifacts)
        return
    if agent == "literature":
        write_yaml(
            run_dir / artifacts["literature_review"],
            candidate.artifacts["literature_review"],
        )
        return
    if agent == "failure_patterns":
        persist_failure_pattern_audit(run_dir, candidate.artifacts["failure_pattern_audit"])
        return
    raise ValueError(f"unknown on-demand agent: {agent!r}")


def _run_agent_body(run_dir: Path, agent: str, *, client, candidate: AgentCandidate | None = None) -> None:
    """Run the agent's actual work, writing only its owned artifact(s).

    If ``candidate`` is supplied (subgoal 04 compute/commit split), persist it
    instead of recomputing -- the writes are identical, just sourced from the
    off-lock ``compute_agent``. Otherwise the existing inline path computes AND
    writes in one step (full back-compat).
    """
    if candidate is not None:
        _commit_candidate(run_dir, agent, candidate)
        return

    if agent in ("examples", "counterexamples"):
        normalizer = _STAGE_REGISTRY[agent]
        context = load_agent_context(run_dir)
        normalizer(run_dir=run_dir, client=client, context=context)
        return
    if agent == "literature":
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        artifacts = manifest["artifacts"]
        literature = _compute_literature(run_dir)
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
    with run_writer_lock(run_dir, action="skip"):
        validate_run(run_dir)
        if not has_status(run_dir):
            init_agent_run_status(run_dir)
        set_agent_status(run_dir, agent, "skipped", error_summary=reason)
        if agent in ("examples", "counterexamples"):
            build_pressure_status(run_dir)
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
    candidate: AgentCandidate | None = None,
) -> dict:
    """Run ONE evidence agent on an existing, valid run. Returns a status dict.

    Fails closed: a mutation/validation error rolls the run back to its previous
    valid state and records the agent as ``failed`` (the previous valid artifact
    is preserved). Never upgrades any mathematical status -- ``validate_run`` is
    the gate. Holds the cross-process run writer lock while mutating (Goal 35B).

    ``candidate`` (subgoal 04 compute/commit split): if a precomputed
    ``AgentCandidate`` from ``compute_agent`` is supplied, this call is the COMMIT
    half -- it persists the candidate's artifacts under the lock instead of
    recomputing. ``validate_run`` still runs in-transaction, so a candidate
    computed against a now-stale run fails closed and rolls back.
    """

    run_dir = Path(run_dir)
    if agent not in ON_DEMAND_AGENTS:
        raise ValueError(
            f"cannot run agent {agent!r}; on-demand agents: {ON_DEMAND_AGENTS}"
        )
    if candidate is not None and candidate.agent != agent:
        raise ValueError(
            f"candidate is for agent {candidate.agent!r}, not {agent!r}"
        )
    with run_writer_lock(run_dir, action="agent run"):
        return _run_single_agent_locked(
            run_dir,
            agent,
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
            client=client,
            candidate=candidate,
        )


def _run_single_agent_locked(
    run_dir: Path,
    agent: str,
    *,
    provider: str,
    model: str,
    api_key_env: str,
    external_agent_command_env: str,
    client,
    candidate: AgentCandidate | None = None,
) -> dict:
    # Precondition: the run must already be kernel-valid.
    validate_run(run_dir)
    if not has_status(run_dir):
        init_agent_run_status(run_dir)
    if agent in CLAIM_CLEAR_BLOCKED_ON_DEMAND_AGENTS:
        # Phase 2: normalize an old / external persisted run before the claim-clear
        # gate so an on-demand evidence agent sees the consolidated hypotheses. We
        # hold the run writer lock here; the helper is idempotent + claim-clear
        # NEUTRAL (never flips precision / unblocks a blocked claim).
        normalize_persisted_claim_ledger(run_dir)
        ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)

    # Build the client BEFORE marking the agent running: a misconfigured provider
    # then fails with the board untouched (no permanently-"running" card and the
    # "no mathematical results were written" error copy stays literally true).
    # A precomputed candidate already consumed the client during compute, so the
    # commit half needs none.
    needs_client = agent in ("examples", "counterexamples") and candidate is None
    if needs_client and client is None:
        client = build_client(
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
        )

    # Mark running BEFORE the snapshot so the attempt count + start time survive a
    # rollback (retries are recorded even when the run fails).
    started_at = _now()
    set_agent_status(run_dir, agent, "running", started_at=started_at, increment_attempt=True)
    if agent in ("examples", "counterexamples"):
        build_pressure_status(run_dir)
    validate_run(run_dir)

    snapshot_paths = AGENT_SNAPSHOT_PATHS[agent] + ["agent_run_status.yaml", PRESSURE_STATUS_FILENAME]
    runner = StageRunner(run_dir)

    def _mutate() -> None:
        _run_agent_body(run_dir, agent, client=client, candidate=candidate)
        # Record done inside the transaction so the board + artifact commit or
        # roll back together (safe_to_use_as_evidence is recomputed from the real
        # artifact presence).
        set_agent_status(run_dir, agent, "done", finished_at=_now())
        if agent in ("examples", "counterexamples"):
            build_pressure_status(run_dir)

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
        if agent in ("examples", "counterexamples"):
            build_pressure_status(run_dir)
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
    "AgentCandidate",
    "compute_agent",
    "run_single_agent",
    "skip_agent",
]
