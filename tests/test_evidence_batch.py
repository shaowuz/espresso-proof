"""Subgoal 03: the explicit on-demand evidence BATCH coordinator (serial).

Proves the coordinator runs a set of lanes via the proven run_single_agent path and
converges, AND that it does NOT change current_state / does NOT make the fast-core
orchestrate pass auto-run evidence (the deferral contract is preserved — the batch
is opt-in).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.kernel import agent_run_status as ars
from espresso_proof.kernel.io import read_yaml
from espresso_proof.orchestration.agent_runner import ON_DEMAND_AGENTS
from espresso_proof.orchestration.evidence_coordinator import (
    order_for_commit,
    run_evidence_batch,
)
from espresso_proof.orchestration.orchestrator import orchestrate_run

CLAIM = "Every group of order p^2, where p is prime, is abelian."


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
    return result["run_dir"]


def _state(run_dir: Path) -> str:
    return read_yaml(run_dir / "run_manifest.yaml")["current_state"]


def test_commit_order_counterexamples_before_examples() -> None:
    ordered = order_for_commit({"examples", "counterexamples", "literature", "failure_patterns"})
    assert ordered.index("counterexamples") < ordered.index("examples")
    # all requested lanes present, no extras
    assert set(ordered) == {"examples", "counterexamples", "literature", "failure_patterns"}


def test_batch_runs_all_requested_lanes(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    # precondition: the fast pass deferred them
    for agent in ON_DEMAND_AGENTS:
        assert ars.agent_status(run_dir, agent) == "not_run"

    out = run_evidence_batch(run_dir)
    assert set(out["agents"]) == set(ON_DEMAND_AGENTS)
    for agent in ON_DEMAND_AGENTS:
        assert out["agents"][agent]["status"] == "done"
        assert ars.agent_status(run_dir, agent) == "done"


def test_batch_does_not_change_current_state(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    before = _state(run_dir)
    run_evidence_batch(run_dir, ["literature"])
    assert _state(run_dir) == before  # batch is process-level, not a lifecycle transition


def test_unknown_agent_rejected(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    with pytest.raises(ValueError):
        run_evidence_batch(run_dir, ["not_a_real_agent"])


def test_fast_core_still_defers_without_a_batch(tmp_path: Path) -> None:
    # Contract preserved: orchestrate fast core does NOT auto-run evidence; lanes
    # stay not_run until a batch / run-agent explicitly runs them.
    run_dir = _fast_run(tmp_path)
    for agent in ON_DEMAND_AGENTS:
        assert ars.agent_status(run_dir, agent) == "not_run"


def test_batch_converges_to_a_valid_run(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    run_evidence_batch(run_dir)
    # examples & counterexamples share tests.yaml; after the ordered batch the run
    # is valid and both are evidence-ready (validate_run ran after every commit).
    from espresso_proof.kernel.validators import validate_run

    validate_run(run_dir)
    assert ars.evidence_ready(run_dir, "examples")
    assert ars.evidence_ready(run_dir, "counterexamples")
