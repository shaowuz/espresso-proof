"""Control-flow fix (from the order-3-cyclic postmortem).

The failed run's ``loop_control.yaml`` said ``should_stop=true`` /
``next_action=ask_human`` ("claim too vague for productive looping"), yet
``orchestrate_run`` still launched the expensive, crash-prone
``llm_prover_reviewers`` external pass. The guard skips that pass when the front
half already asked to stop before proof search, halting at the human gate.

These tests cover the decision helper (all branches) and the orchestrator wiring
(prover skipped + run still valid + absent prover artifact tolerated).
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.orchestration import orchestrator
from espresso_proof.orchestration.orchestrator import _stop_before_prover, orchestrate_run
from espresso_proof.kernel.io import write_yaml
from espresso_proof.kernel.run_events import read_events
from espresso_proof.kernel.validators import validate_run
from tests.kernel.test_evidence_aware_grade import FakeClaude


# --- decision helper: all branches ------------------------------------------

def _write_lc(run_dir: Path, **fields) -> None:
    write_yaml(run_dir / "loop_control.yaml", {"claim_id": "c", "cycles_analyzed": 1, **fields})


def test_stop_before_prover_false_for_ask_human(tmp_path: Path):
    # ask_human runs the first pass BY DESIGN (human gate is post-pass; the signal
    # is emitted broadly, even for clear claims). It must NOT skip the pass.
    _write_lc(tmp_path, should_stop=True, next_action="ask_human")
    assert _stop_before_prover(tmp_path)["stop"] is False


def test_stop_before_prover_true_for_hard_stop(tmp_path: Path):
    _write_lc(tmp_path, should_stop=True, next_action="stop")
    assert _stop_before_prover(tmp_path)["stop"] is True


def test_stop_before_prover_false_for_run_strategy_brief(tmp_path: Path):
    # run_strategy_brief is the legitimate proceed-to-prover path; do NOT skip.
    _write_lc(tmp_path, should_stop=True, next_action="run_strategy_brief")
    assert _stop_before_prover(tmp_path)["stop"] is False


def test_stop_before_prover_false_when_not_stopping(tmp_path: Path):
    _write_lc(tmp_path, should_stop=False, next_action="continue_loop")
    assert _stop_before_prover(tmp_path)["stop"] is False


def test_stop_before_prover_fails_closed_when_missing(tmp_path: Path):
    # absent/unreadable loop_control => run the pass (preserve normal flow)
    assert _stop_before_prover(tmp_path) == {"stop": False, "next_action": None}


# --- orchestrator wiring -----------------------------------------------------

def _claim(tmp_path: Path) -> Path:
    c = tmp_path / "c.md"
    c.write_text("Every group of order p^2, where p is a prime, is abelian.\n", encoding="utf-8")
    return c


def test_normal_run_still_runs_prover(tmp_path: Path):
    """Regression: when the front half does not ask to stop, the prover pass runs
    exactly as before (no behavior change)."""
    res = orchestrate_run(
        project="flow", input_path=_claim(tmp_path), output_root=tmp_path,
        provider="stub", front_half="stub", front_half_client=FakeClaude(precision="precise"),
    )
    run_dir = Path(res["run_dir"])
    assert _stop_before_prover(run_dir)["stop"] is False
    assert (run_dir / "llm_agent_run.yaml").exists()
    assert res["state"] == "WAITING_LLM_AGENT_HUMAN_GATE"
    kinds = {e.get("kind") for e in read_events(run_dir)}
    assert "prover_pass_skipped" not in kinds
    assert "agent_call_started" in kinds


def test_prover_skipped_when_front_half_asks_to_stop(tmp_path: Path, monkeypatch):
    """Wiring: when the guard reports stop, orchestrate_run does NOT run the
    prover pass, records prover_pass_skipped, leaves no prover artifacts, stays at
    LITERATURE_DONE, and the run still validates (the absent llm_agent_run.yaml is
    tolerated by the guarded return read)."""
    monkeypatch.setattr(
        orchestrator, "_stop_before_prover",
        lambda run_dir: {"stop": True, "next_action": "ask_human"},
    )
    res = orchestrate_run(
        project="flow", input_path=_claim(tmp_path), output_root=tmp_path,
        provider="stub", front_half="stub", front_half_client=FakeClaude(precision="precise"),
    )
    run_dir = Path(res["run_dir"])
    assert not (run_dir / "llm_agent_run.yaml").exists()
    assert not (run_dir / "prover_loop.yaml").exists()
    assert res["state"] == "LITERATURE_DONE"
    assert res.get("human_gate") == {}
    kinds = {e.get("kind") for e in read_events(run_dir)}
    assert "prover_pass_skipped" in kinds
    # the prover external call never started
    assert not any(
        e.get("kind") == "agent_call_started" and e.get("stage") == "llm_prover_reviewers"
        for e in read_events(run_dir)
    )
    validate_run(run_dir)  # the run is still valid without prover artifacts
