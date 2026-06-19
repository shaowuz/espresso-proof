"""Run a batch of on-demand evidence agents (Claude-reconstructs-Espresso-Proof
subgoals 03/05).

This is the EXPLICIT, on-demand batch path — it is NOT called by orchestrate_run.
The fast-core orchestrate pass still defers the evidence agents (they stay
``not_run`` at the human gate; the memo/dashboard say PRELIMINARY). A caller (CLI /
server / a future orchestrator step) invokes this to actually run a set of lanes.

Subgoal 03 (this version): SERIAL — each requested lane is run via the proven
``run_single_agent`` (each writes disjoint artifacts, commits under the
``run_writer_lock``, re-validates the whole run, and converges regardless of order).
Subgoal 05 parallelizes the COMPUTE while keeping the COMMIT serial on the
coordinator thread. Nothing here touches ``current_state``; lane progress lives on
the ``agent_run_status`` board.
"""
from __future__ import annotations

import concurrent.futures as _cf
import time
from pathlib import Path

from espresso_proof.agents.llm_agents import build_client
from espresso_proof.kernel.run_lock import DEFAULT_TIMEOUT_SECONDS, RunLockedError
from espresso_proof.orchestration.agent_runner import (
    ON_DEMAND_AGENTS,
    compute_agent,
    run_single_agent,
)

# Deterministic commit order. ``examples`` reconciles ``tests.yaml`` from the
# ``counterexamples`` report and both own ``tests.yaml``, so counterexamples MUST
# commit before examples; literature and failure_patterns own disjoint files and may
# go in any order. This order is what subgoal 05's parallel-compute / serial-commit
# coordinator must also honor for byte-identical convergence.
_COMMIT_ORDER = ["counterexamples", "examples", "literature", "failure_patterns"]


def order_for_commit(agents: set[str]) -> list[str]:
    """Return the requested agents in the deterministic commit order."""
    ordered = [a for a in _COMMIT_ORDER if a in agents]
    ordered += [a for a in sorted(agents) if a not in _COMMIT_ORDER]
    return ordered


def run_evidence_batch(
    run_dir: Path,
    agents: list[str] | None = None,
    *,
    provider: str = "stub",
    model: str = "gpt-4.1",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    client=None,
) -> dict:
    """Run a set of on-demand evidence agents serially; return their statuses.

    ``agents`` defaults to all ON_DEMAND_AGENTS. Each lane fails closed inside
    ``run_single_agent`` (rollback + ``failed`` status) without aborting the batch,
    so one lane's failure never corrupts another's committed artifact.
    """
    run_dir = Path(run_dir)
    requested = list(ON_DEMAND_AGENTS) if agents is None else list(agents)
    unknown = [a for a in requested if a not in ON_DEMAND_AGENTS]
    if unknown:
        raise ValueError(
            f"unknown evidence agents {unknown}; valid on-demand agents: {ON_DEMAND_AGENTS}"
        )

    results: dict[str, dict] = {}
    for agent in order_for_commit(set(requested)):
        results[agent] = run_single_agent(
            run_dir,
            agent,
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
            client=client,
        )
    return {"agents": results}


# --- Subgoal 05: parallel COMPUTE, serial COMMIT -------------------------------

# Hard cap on concurrent lane compute()s. There are only four context lanes, and
# a real provider charges per call, so the pool is bounded small by default.
DEFAULT_MAX_WORKERS = 4
# Retries reserved for RunLockedError ONLY (a racing foreign CLI/server writer);
# a lane compute/commit failure is NOT retried (it fails closed, as today).
DEFAULT_COMMIT_LOCK_RETRIES = 3

# Lanes whose compute() reads ONLY the claim + pre-batch state, so precomputing
# them off-lock in parallel yields the SAME inputs as the serial batch: in the
# serial commit order each of these computes before its partner lane commits
# anyway (counterexamples reads examples=absent; examples reads literature=absent;
# literature reads nothing cross-lane). `failure_patterns` is deliberately NOT
# here -- its compute (`build_failure_pattern_audit`) reads the OTHER lanes'
# committed artifacts (counterexamples / computation_checks), so it must compute
# AFTER they commit or it would audit empty evidence and diverge from the serial
# `run_evidence_batch`. Such lanes are run last via the inline compute+commit path.
_PARALLEL_COMPUTE_LANES = frozenset({"literature", "examples", "counterexamples"})


def _commit_lane_with_lock_retry(
    run_dir: Path,
    agent: str,
    candidate,
    *,
    provider: str,
    model: str,
    api_key_env: str,
    external_agent_command_env: str,
    retries: int,
    backoff_seconds: float,
) -> dict:
    """Commit ONE lane on the coordinator thread, retrying ONLY on
    ``RunLockedError``.

    A racing foreign writer (a CLI/gate holding the run writer lock) must not turn
    a lane into ``status=failed`` -- so lock contention is retried with bounded
    backoff. Every other failure is left to ``run_single_agent``'s fail-closed
    path. ``candidate=None`` falls back to the inline recompute (used when this
    lane's parallel ``compute()`` raised), so no lane is ever silently dropped.
    The total wait is bounded (``retries`` * the lock's own ``DEFAULT_TIMEOUT_
    SECONDS`` + backoff), so an unbounded foreign lock degrades to a friendly
    timeout, never an infinite hang.
    """
    attempt = 0
    while True:
        try:
            return run_single_agent(
                run_dir,
                agent,
                provider=provider,
                model=model,
                api_key_env=api_key_env,
                external_agent_command_env=external_agent_command_env,
                candidate=candidate,
            )
        except RunLockedError:
            attempt += 1
            if attempt > retries:
                raise
            time.sleep(backoff_seconds * attempt)


def run_evidence_batch_parallel(
    run_dir: Path,
    agents: list[str] | None = None,
    *,
    provider: str = "stub",
    model: str = "gpt-4.1",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    client=None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    commit_retries: int = DEFAULT_COMMIT_LOCK_RETRIES,
    commit_backoff_seconds: float = DEFAULT_TIMEOUT_SECONDS / 6,
) -> dict:
    """Run a set of on-demand evidence agents with PARALLEL compute + SERIAL commit.

    The four context lanes' ``compute_agent`` (pure, off-lock, writes nothing under
    ``run_dir`` -- proven in subgoal 04) run concurrently on a bounded pool; then
    each candidate is committed SERIALLY on THIS (the coordinator) thread via the
    unchanged ``run_single_agent`` -- because ``run_writer_lock`` re-entrancy is
    ``threading.local``, a worker-thread commit would self-contend the flock. The
    commit order is the deterministic ``order_for_commit`` (counterexamples THEN
    examples, since they share ``tests.yaml``), so the final run is byte-identical
    regardless of which lane's compute finished first. Same result as the serial
    ``run_evidence_batch`` -- only the compute is parallelized.
    """
    run_dir = Path(run_dir)
    requested = list(ON_DEMAND_AGENTS) if agents is None else list(agents)
    unknown = [a for a in requested if a not in ON_DEMAND_AGENTS]
    if unknown:
        raise ValueError(
            f"unknown evidence agents {unknown}; valid on-demand agents: {ON_DEMAND_AGENTS}"
        )

    ordered = order_for_commit(set(requested))
    if not ordered:
        return {"agents": {}}

    precompute = [a for a in ordered if a in _PARALLEL_COMPUTE_LANES]

    # One shared client across all compute threads: the stub client is stateless
    # and the real clients are immutable after construction (provider preflight
    # happens once here, honoring the existing build_client contract).
    if client is None and any(a in ("examples", "counterexamples") for a in precompute):
        client = build_client(
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
        )

    # PHASE 1 -- parallel COMPUTE (off the lock) of the precompute lanes only. A
    # lane whose compute raises is NOT dropped: it is committed via the inline
    # recompute path below (candidate stays None), reproducing the serial
    # fail-closed behavior exactly.
    candidates: dict[str, object] = {}
    if precompute:
        pool_size = max(1, min(max_workers, len(precompute)))
        with _cf.ThreadPoolExecutor(max_workers=pool_size) as executor:
            futures = {
                executor.submit(
                    compute_agent,
                    run_dir,
                    agent,
                    provider=provider,
                    model=model,
                    api_key_env=api_key_env,
                    external_agent_command_env=external_agent_command_env,
                    client=client,
                ): agent
                for agent in precompute
            }
            for future in _cf.as_completed(futures):
                agent = futures[future]
                try:
                    candidates[agent] = future.result()
                except Exception:
                    # compute failed off-lock; fall back to inline commit (recompute)
                    candidates[agent] = None

    # PHASE 2 -- serial COMMIT on the coordinator thread, in deterministic order.
    # Precompute lanes commit their candidate; any deferred lane (failure_patterns)
    # has candidate=None, so run_single_agent recomputes it inline at THIS point --
    # after the precompute lanes have committed -- exactly like the serial batch,
    # where it reads the now-committed counterexamples / computation_checks.
    results: dict[str, dict] = {}
    for agent in ordered:
        results[agent] = _commit_lane_with_lock_retry(
            run_dir,
            agent,
            candidates.get(agent),
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
            retries=commit_retries,
            backoff_seconds=commit_backoff_seconds,
        )
    return {"agents": results}


__all__ = [
    "run_evidence_batch",
    "run_evidence_batch_parallel",
    "order_for_commit",
]
