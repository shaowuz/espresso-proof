"""Parallel-COMPUTE / serial-COMMIT evidence fan-out coordinator.

Proves the coordinator's invariants:
- Convergence: the run is BYTE-IDENTICAL to the serial batch regardless of which
  lane's compute() finishes first (the order-independence property).
- Retry-not-fail: a racing foreign writer (RunLockedError) is retried with bounded
  backoff, never flips a lane to status=failed.
- Pool bound: compute() runs on a bounded pool (no unbounded parallel LLM calls).
- The commit happens serially on the coordinator thread in the fixed
  counterexamples-then-examples order (shared tests.yaml).
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from espresso_proof.agents.llm_agents import build_client
from espresso_proof.kernel import agent_run_status as ars
from espresso_proof.kernel.run_lock import RunLockedError, run_writer_lock
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration import evidence_coordinator as ec
from espresso_proof.orchestration.agent_runner import ON_DEMAND_AGENTS
from espresso_proof.orchestration.evidence_coordinator import (
    run_evidence_batch,
    run_evidence_batch_parallel,
)
from espresso_proof.orchestration.orchestrator import orchestrate_run

CLAIM = "Every group of order p^2, where p is prime, is abelian."

_EVIDENCE_FILES = [
    "examples.yaml",
    "computation_checks.yaml",
    "counterexamples.yaml",
    "tests.yaml",
    "literature_review.yaml",
]


def _fast_run(tmp_path: Path, name: str = "p2") -> Path:
    claim_path = tmp_path / f"{name}.md"
    claim_path.write_text(CLAIM, encoding="utf-8")
    result = orchestrate_run(
        project=name,
        input_path=claim_path,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    return Path(result["run_dir"])


def test_parallel_batch_runs_all_lanes(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    for agent in ON_DEMAND_AGENTS:
        assert ars.agent_status(run_dir, agent) == "not_run"

    out = run_evidence_batch_parallel(run_dir)
    assert set(out["agents"]) == set(ON_DEMAND_AGENTS)
    for agent in ON_DEMAND_AGENTS:
        assert out["agents"][agent]["status"] == "done"
        assert ars.agent_status(run_dir, agent) == "done"
    validate_run(run_dir)
    assert ars.evidence_ready(run_dir, "examples")
    assert ars.evidence_ready(run_dir, "counterexamples")


def test_parallel_equals_serial_byte_identical(tmp_path: Path) -> None:
    base = _fast_run(tmp_path)
    proj = base.parents[1]
    shutil.copytree(proj, tmp_path / "S" / proj.name)
    shutil.copytree(proj, tmp_path / "P" / proj.name)
    serial_dir = tmp_path / "S" / proj.name / "runs" / base.name
    parallel_dir = tmp_path / "P" / proj.name / "runs" / base.name

    run_evidence_batch(serial_dir)
    run_evidence_batch_parallel(parallel_dir, max_workers=4)

    # convergence: the parallel run is byte-identical to the serial run for every
    # evidence artifact, regardless of which lane's compute finished first
    for name in _EVIDENCE_FILES:
        a = serial_dir / name
        b = parallel_dir / name
        assert a.exists() == b.exists(), f"{name}: presence differs"
        if a.exists():
            assert a.read_bytes() == b.read_bytes(), f"{name}: serial vs parallel bytes differ"

    # failure_pattern_audit (reads the other lanes' committed artifacts) and
    # pressure_status (rebuilt on examples/counterexamples commit) must converge
    # too; only their wall-clock generated_at may differ. The audit converging is
    # what forces failure_patterns to compute AFTER the evidence lanes commit.
    from espresso_proof.kernel.io import read_yaml

    for name in ("failure_pattern_audit.yaml", "pressure_status.yaml"):
        sa = read_yaml(serial_dir / name)
        pa = read_yaml(parallel_dir / name)
        sa.pop("generated_at", None)
        pa.pop("generated_at", None)
        assert sa == pa, f"{name} diverged between serial and parallel"

    validate_run(serial_dir)
    validate_run(parallel_dir)


def test_failure_patterns_is_deferred_not_precomputed(tmp_path: Path, monkeypatch) -> None:
    # failure_patterns' compute reads the other lanes' committed artifacts, so it
    # must be committed via the inline recompute path (candidate=None) AFTER the
    # precompute lanes commit -- never precomputed in the parallel wave (which
    # would audit empty evidence and diverge from the serial batch).
    run_dir = _fast_run(tmp_path)
    real = ec._commit_lane_with_lock_retry
    candidate_present: dict[str, bool] = {}

    def spy(run_dir, agent, candidate, **kw):
        candidate_present[agent] = candidate is not None
        return real(run_dir, agent, candidate, **kw)

    monkeypatch.setattr(ec, "_commit_lane_with_lock_retry", spy)
    run_evidence_batch_parallel(run_dir)

    assert candidate_present["counterexamples"] is True
    assert candidate_present["examples"] is True
    assert candidate_present["literature"] is True
    assert candidate_present["failure_patterns"] is False  # deferred, inline recompute


def test_compute_failure_falls_back_to_inline_recompute(tmp_path: Path, monkeypatch) -> None:
    # If a lane's parallel compute() raises, it must NOT be dropped: candidate
    # becomes None and the lane is committed via the inline recompute path. Make
    # counterexamples' compute raise once; the batch must still commit it done.
    run_dir = _fast_run(tmp_path)
    real_compute = ec.compute_agent

    def flaky_compute(run_dir, agent, **kw):
        if agent == "counterexamples":
            raise RuntimeError("simulated off-lock compute failure")
        return real_compute(run_dir, agent, **kw)

    monkeypatch.setattr(ec, "compute_agent", flaky_compute)
    out = run_evidence_batch_parallel(run_dir)

    # the lane recomputed inline (no candidate) and still committed done
    assert out["agents"]["counterexamples"]["status"] == "done"
    assert ars.agent_status(run_dir, "counterexamples") == "done"
    for agent in ON_DEMAND_AGENTS:
        assert out["agents"][agent]["status"] == "done"
    validate_run(run_dir)


def test_commit_order_counterexamples_before_examples(tmp_path: Path, monkeypatch) -> None:
    # The serial commit must apply counterexamples before examples (shared
    # tests.yaml). Record the actual commit order on the coordinator thread.
    run_dir = _fast_run(tmp_path)
    real = ec._commit_lane_with_lock_retry
    commit_order: list[str] = []

    def spy(run_dir, agent, candidate, **kw):
        commit_order.append(agent)
        return real(run_dir, agent, candidate, **kw)

    monkeypatch.setattr(ec, "_commit_lane_with_lock_retry", spy)
    run_evidence_batch_parallel(run_dir)

    assert commit_order.index("counterexamples") < commit_order.index("examples")
    # failure_patterns commits last (after the lanes it audits)
    assert commit_order[-1] == "failure_patterns"


def test_commit_retries_on_runlocked_then_succeeds(monkeypatch) -> None:
    calls = {"n": 0}

    def fake_run_single_agent(run_dir, agent, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RunLockedError("agent run")
        return {"agent": agent, "status": "done"}

    monkeypatch.setattr(ec, "run_single_agent", fake_run_single_agent)
    out = ec._commit_lane_with_lock_retry(
        Path("/tmp/nonexistent"),
        "literature",
        None,
        provider="stub",
        model="m",
        api_key_env="K",
        external_agent_command_env="E",
        retries=3,
        backoff_seconds=0.0,
    )
    assert out["status"] == "done"
    assert calls["n"] == 3  # two lock denials retried, third succeeds


def test_commit_gives_up_after_bounded_retries(monkeypatch) -> None:
    calls = {"n": 0}

    def always_locked(run_dir, agent, **kw):
        calls["n"] += 1
        raise RunLockedError("agent run")

    monkeypatch.setattr(ec, "run_single_agent", always_locked)
    with pytest.raises(RunLockedError):
        ec._commit_lane_with_lock_retry(
            Path("/tmp/nonexistent"),
            "literature",
            None,
            provider="stub",
            model="m",
            api_key_env="K",
            external_agent_command_env="E",
            retries=2,
            backoff_seconds=0.0,
        )
    assert calls["n"] == 3  # initial attempt + 2 bounded retries, then raise


def test_racing_writer_does_not_fail_lane(tmp_path: Path) -> None:
    # A foreign writer holds the run lock; the lane must WAIT (real contention)
    # then commit done -- never get flipped to failed. Deterministic: the holder
    # signals AFTER it owns the lock, so the batch provably contends for it.
    run_dir = _fast_run(tmp_path)
    acquired = threading.Event()
    release_now = threading.Event()
    released = threading.Event()

    def hold_lock() -> None:
        with run_writer_lock(run_dir, action="foreign"):
            acquired.set()
            release_now.wait(timeout=5)  # hold until the main thread is contending
        released.set()

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert acquired.wait(timeout=5)  # foreign writer definitely owns the lock now

    # let the foreign writer go just after we start contending; the lane's
    # run_writer_lock waits (well within its 30s deadline), then commits.
    release_now.set()
    out = run_evidence_batch_parallel(run_dir, ["literature"])
    holder.join(timeout=5)

    assert released.is_set()
    assert out["agents"]["literature"]["status"] == "done"
    assert ars.agent_status(run_dir, "literature") == "done"


def test_pool_bound_is_honored(tmp_path: Path, monkeypatch) -> None:
    run_dir = _fast_run(tmp_path)
    real_compute = ec.compute_agent
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0}
    # Deterministic rendezvous instead of a sleep window: a compute thread blocks
    # until two lanes are concurrently active (or a generous timeout), so genuine
    # overlap is observed without timing luck. An Event (not a Barrier) avoids
    # deadlocking the odd third lane.
    two_active = threading.Event()

    def counting_compute(run_dir, agent, **kw):
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            if state["active"] >= 2:
                two_active.set()
        try:
            two_active.wait(timeout=5)
            return real_compute(run_dir, agent, **kw)
        finally:
            with lock:
                state["active"] -= 1

    monkeypatch.setattr(ec, "compute_agent", counting_compute)
    out = run_evidence_batch_parallel(run_dir, max_workers=2)

    # bound honored: never more than max_workers lanes computing at once
    assert state["max_active"] <= 2
    # parallelism actually happened (3 precompute lanes, pool of 2 → cap reached)
    assert two_active.is_set()
    assert state["max_active"] == 2
    for agent in ON_DEMAND_AGENTS:
        assert out["agents"][agent]["status"] == "done"


def test_pool_bound_one_serializes(tmp_path: Path, monkeypatch) -> None:
    run_dir = _fast_run(tmp_path)
    real_compute = ec.compute_agent
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0}

    def counting_compute(run_dir, agent, **kw):
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        try:
            time.sleep(0.02)
            return real_compute(run_dir, agent, **kw)
        finally:
            with lock:
                state["active"] -= 1

    monkeypatch.setattr(ec, "compute_agent", counting_compute)
    run_evidence_batch_parallel(run_dir, max_workers=1)
    assert state["max_active"] == 1  # max_workers=1 fully serializes compute
