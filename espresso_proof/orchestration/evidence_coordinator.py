"""Run a batch of on-demand evidence agents.

This is the EXPLICIT, on-demand batch path — it is NOT called by orchestrate_run.
The fast-core orchestrate pass still defers the evidence agents (they stay
``not_run`` at the human gate; the memo/dashboard say PRELIMINARY). A caller (CLI /
server / a future orchestrator step) invokes this to actually run a set of lanes.

Serial path: each requested lane is run via the proven
``run_single_agent`` (each writes disjoint artifacts, commits under the
``run_writer_lock``, re-validates the whole run, and converges regardless of order).
The fan-out path parallelizes the COMPUTE while keeping the COMMIT serial on the
coordinator thread. Nothing here touches ``current_state``; lane progress lives on
the ``agent_run_status`` board.
"""
from __future__ import annotations

import concurrent.futures as _cf
import os
import time
from pathlib import Path

from espresso_proof.agents.llm_agents import build_client
from espresso_proof.kernel.io import read_yaml
from espresso_proof.kernel.observability import (
    append_agent_event,
    monotonic_ms,
    now_iso,
    write_timings,
    usage_sink_for_run,
)
from espresso_proof.kernel.run_lock import (
    DEFAULT_TIMEOUT_SECONDS,
    RunLockedError,
    run_writer_lock,
)
from espresso_proof.kernel.stage_runner import StageRunner
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.agent_runner import (
    ON_DEMAND_AGENTS,
    compute_agent,
    run_single_agent,
)
from espresso_proof.kernel.pressure_lane_barrier import (
    PRESSURE_LANE_BARRIER_COMMIT_ORDER,
    PRESSURE_LANE_BARRIER_FILENAME,
    finalize_pressure_lane_barrier,
    record_pressure_lane_barrier_agent_result,
    record_pressure_lane_barrier_commit_result,
    record_pressure_lane_barrier_committing,
    record_pressure_lane_barrier_compute,
    seed_pressure_lane_barrier,
)

# Deterministic commit order. ``examples`` reconciles ``tests.yaml`` from the
# ``counterexamples`` report and both own ``tests.yaml``, so counterexamples MUST
# commit before examples; literature and failure_patterns own disjoint files and may
# go in any order. This order is what the parallel-compute / serial-commit
# coordinator must also honor for byte-identical convergence.
_COMMIT_ORDER = list(PRESSURE_LANE_BARRIER_COMMIT_ORDER)


def order_for_commit(agents: set[str]) -> list[str]:
    """Return the requested agents in the deterministic commit order."""
    ordered = [a for a in _COMMIT_ORDER if a in agents]
    ordered += [a for a in sorted(agents) if a not in _COMMIT_ORDER]
    return ordered


def _record_barrier_checked(run_dir: Path, stage_name: str, writer) -> dict:
    """Write one barrier transition under lock + StageRunner + validate_run."""

    report: dict = {}

    def _mutate() -> None:
        nonlocal report
        report = writer()

    with run_writer_lock(run_dir, action=stage_name):
        StageRunner(run_dir).run(
            name=stage_name,
            mutate=_mutate,
            validate=lambda: validate_run(run_dir),
            state_after=None,
            snapshot_paths=[PRESSURE_LANE_BARRIER_FILENAME],
        )
    return report


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


# --- Fan-out: parallel COMPUTE, serial COMMIT -------------------------------

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


def _timed_compute(run_dir: Path, agent: str, **kwargs) -> dict:
    """Time one lane's pure compute on a worker thread. NEVER raises -- a compute
    failure is captured so the coordinator can fall back to inline recompute. The
    agent_events record is written later, on the COORDINATOR thread, to avoid
    concurrent writers to agent_events.jsonl.
    """
    started_at = now_iso()
    t0 = monotonic_ms()
    try:
        candidate = compute_agent(run_dir, agent, **kwargs)
        return {
            "agent": agent,
            "candidate": candidate,
            "started_at": started_at,
            "duration_ms": monotonic_ms() - t0,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - captured for inline fallback
        return {
            "agent": agent,
            "candidate": None,
            "started_at": started_at,
            "duration_ms": monotonic_ms() - t0,
            "error": str(exc)[:500],
        }


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
    ``run_dir`` -- proven by the compute/commit split tests) run concurrently on a
    bounded pool; then
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

    # One shared client across all compute threads. The stub client is
    # stateless; real clients now UPDATE self.model per call from the executor
    # envelope (same executor -> benign) and keep usage attribution in
    # thread-local context, with the usage sink itself lock-serialized.
    if client is None and any(a in ("examples", "counterexamples") for a in precompute):
        client = build_client(
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
            usage_sink=usage_sink_for_run(run_dir),
        )

    run_id = (read_yaml(run_dir / "run_manifest.yaml") or {}).get("run_id")

    # SEED -- persist the deterministic scheduling plan before any worker
    # computes. Re-entering after a crash overwrites any partial barrier with the
    # same deterministic plan, so resume is coherent even though worker
    # candidates remain in memory only.
    _record_barrier_checked(
        run_dir,
        "pressure_lane_barrier:seed",
        lambda: seed_pressure_lane_barrier(run_dir, ordered),
    )

    # PHASE 1 -- parallel COMPUTE (off the lock) of the precompute lanes only. A
    # lane whose compute raises is NOT dropped: it is committed via the inline
    # recompute path below (candidate stays None), reproducing the serial
    # fail-closed behavior exactly. Timing is captured per lane and the
    # observability event is written later on the coordinator thread.
    timed: dict[str, dict] = {}
    if precompute:
        pool_size = max(1, min(max_workers, len(precompute)))
        with _cf.ThreadPoolExecutor(max_workers=pool_size) as executor:
            futures = {
                executor.submit(
                    _timed_compute,
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
                info = future.result()  # _timed_compute never raises
                timed[info["agent"]] = info

    # RUN -- workers are done and have not written shared status. The coordinator
    # records only timing/error summaries; candidates stay in memory until the
    # serial commit below.
    _record_barrier_checked(
        run_dir,
        "pressure_lane_barrier:run",
        lambda: record_pressure_lane_barrier_compute(run_dir, timed),
    )

    # PHASE 2 -- serial COMMIT on the coordinator thread, in deterministic order.
    # Precompute lanes commit their candidate; any deferred lane (failure_patterns)
    # has candidate=None, so run_single_agent recomputes it inline at THIS point --
    # after the precompute lanes have committed -- exactly like the serial batch,
    # where it reads the now-committed counterexamples / computation_checks.
    # Per-lane compute + commit timing is recorded to agent_events.jsonl (which is
    # snapshot-excluded, so it survives StageRunner rollbacks) on this thread.
    results: dict[str, dict] = {}
    for agent in ordered:
        info = timed.get(agent)
        if info is not None:
            append_agent_event(
                run_dir,
                agent_name=agent,
                provider=provider,
                adapter="evidence_batch",
                stage="evidence_compute",
                agent_status="failed" if info["error"] else "finished",
                started_at=info["started_at"],
                duration_ms=info["duration_ms"],
                model=model,
                run_id=run_id,
                error_summary=info["error"],
            )

        _record_barrier_checked(
            run_dir,
            f"pressure_lane_barrier:joining:{agent}",
            lambda agent=agent: record_pressure_lane_barrier_committing(run_dir, agent),
        )
        commit_started = now_iso()
        c0 = monotonic_ms()
        try:
            result = _commit_lane_with_lock_retry(
                run_dir,
                agent,
                info["candidate"] if info is not None else None,
                provider=provider,
                model=model,
                api_key_env=api_key_env,
                external_agent_command_env=external_agent_command_env,
                retries=commit_retries,
                backoff_seconds=commit_backoff_seconds,
            )
        except Exception as exc:
            _record_barrier_checked(
                run_dir,
                f"pressure_lane_barrier:failed:{agent}",
                lambda agent=agent, exc=exc: record_pressure_lane_barrier_agent_result(
                    run_dir,
                    agent,
                    status="failed",
                    error_summary=str(exc),
                ),
            )
            _record_barrier_checked(
                run_dir,
                "pressure_lane_barrier:finalize",
                lambda: finalize_pressure_lane_barrier(run_dir),
            )
            raise
        append_agent_event(
            run_dir,
            agent_name=agent,
            provider=provider,
            adapter="evidence_batch",
            stage="evidence_commit",
            agent_status="failed" if result.get("status") == "failed" else "finished",
            started_at=commit_started,
            duration_ms=monotonic_ms() - c0,
            model=model,
            run_id=run_id,
            error_summary=result.get("error"),
        )
        _record_barrier_checked(
            run_dir,
            f"pressure_lane_barrier:committed:{agent}",
            lambda agent=agent, result=result: record_pressure_lane_barrier_commit_result(
                run_dir,
                agent,
                result,
            ),
        )
        results[agent] = result

    _record_barrier_checked(
        run_dir,
        "pressure_lane_barrier:finalize",
        lambda: finalize_pressure_lane_barrier(run_dir),
    )
    # Aggregate the per-lane timings into timings.yaml (diagnostic; fails closed).
    write_timings(run_dir)
    return {"agents": results}


# Default-on feature flag: the batch entrypoints use the parallel coordinator by
# default and fall back to the serial path when this is turned off. Both paths
# produce a byte-identical run (proven by the fan-out tests), so the flag is purely an
# operational escape hatch.
PARALLEL_ENV = "ESPRESSO_EVIDENCE_PARALLEL"
_FLAG_OFF = {"false", "0", "no", "off"}


def evidence_parallel_enabled() -> bool:
    """Whether the batch entrypoints use the parallel coordinator (default true)."""
    return os.environ.get(PARALLEL_ENV, "true").strip().lower() not in _FLAG_OFF


def run_evidence_batch_auto(
    run_dir: Path,
    agents: list[str] | None = None,
    *,
    provider: str = "stub",
    model: str = "gpt-4.1",
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> dict:
    """Run the evidence batch via the parallel coordinator by default, or the
    serial path when ``ESPRESSO_EVIDENCE_PARALLEL`` is off. This is the single
    entrypoint the CLI / server should call. Both paths converge to the same run.
    """
    if evidence_parallel_enabled():
        return run_evidence_batch_parallel(
            run_dir,
            agents,
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            external_agent_command_env=external_agent_command_env,
            max_workers=max_workers,
        )
    return run_evidence_batch(
        run_dir,
        agents,
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
    )


__all__ = [
    "run_evidence_batch",
    "run_evidence_batch_parallel",
    "run_evidence_batch_auto",
    "evidence_parallel_enabled",
    "order_for_commit",
    "PARALLEL_ENV",
]
