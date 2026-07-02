"""Goal 67C: a stub / deterministic-template run must never be presented as a
real prover/reviewer loop. The view-model carries a fail-closed template-fallback
signal derived from the recorded provider, and the labels do not lie.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from espresso_proof.kernel import observability as obs
from espresso_proof.orchestration.run_dashboard import (
    TEMPLATE_FALLBACK_NOTE,
    _continue_available,
    _proof_label,
    _real_prover_ran,
    _state_label,
    status_summary,
)
from espresso_proof.orchestration.orchestrator import orchestrate_run


# --- pure label functions ----------------------------------------------------

def test_state_label_template_run_is_not_reviewed():
    # the dishonest case: a stub COMPLETED run used to read "Reviewed"
    assert _state_label("COMPLETED_MEMO", repair_needed=True, template_fallback=True) \
        == "Template draft — no real prover ran"
    # a real run keeps the normal label
    assert _state_label("COMPLETED_MEMO", repair_needed=True, template_fallback=False) == "Reviewed"
    assert _state_label("COMPLETED_MEMO", repair_needed=False, template_fallback=False) == "Memo ready"


def test_proof_label_template_run_says_template_only():
    assert _proof_label({}, repair_needed=True, template_fallback=True) == "Template only — not a real attempt"
    assert _proof_label({}, repair_needed=True, template_fallback=False) == "Needs repair"


def test_continue_available_only_at_or_past_the_gate():
    # the UI's Continue affordance must mirror resume_run's boundary check:
    # before the prover human gate a continue can only fail.
    for state in ("", "SCAFFOLDED", "WAITING_CLAIM_CLARIFICATION", "FRONT_HALF_DONE",
                  "CLAIM_CLEAR", "LITERATURE_DONE", "NOT_A_STATE"):
        assert _continue_available(state) is False, state
    for state in ("WAITING_LLM_AGENT_HUMAN_GATE", "HUMAN_CHOICE_CONTINUE_SELECTED",
                  "AUTO_CHOICE_CONTINUE_SELECTED", "COMPLETED_MEMO"):
        assert _continue_available(state) is True, state


# --- _real_prover_ran: fail-closed provenance --------------------------------

def _run(tmp: Path) -> Path:
    d = tmp / "r"
    d.mkdir()
    return d


def test_real_prover_true_only_for_real_provider(tmp_path: Path):
    d = _run(tmp_path)
    obs.append_agent_event(d, agent_name="llm_prover_reviewers", provider="external",
                           adapter="external", stage="llm_prover_reviewers", agent_status="started")
    assert _real_prover_ran(d) is True


def test_real_prover_false_for_stub(tmp_path: Path):
    d = _run(tmp_path)
    obs.append_agent_event(d, agent_name="llm_prover_reviewers", provider="stub",
                           adapter="stub", stage="llm_prover_reviewers", agent_status="finished")
    assert _real_prover_ran(d) is False


def test_real_prover_false_when_absent_failclosed(tmp_path: Path):
    # no prover agent event at all -> fail closed to template fallback
    assert _real_prover_ran(_run(tmp_path)) is False


# --- integration: a stub run is flagged template_fallback --------------------

def test_stub_run_status_is_template_fallback(tmp_path: Path):
    claim = tmp_path / "c.md"
    claim.write_text("Every group of order p^2, where p is prime, is abelian.\n", encoding="utf-8")
    res = orchestrate_run(project="workbench", input_path=claim, output_root=tmp_path,
                          provider="stub", front_half="stub")
    run_dir = Path(res["run_dir"])
    summary = status_summary(run_dir)
    assert summary["template_fallback"] is True
    assert summary["template_fallback_note"] == TEMPLATE_FALLBACK_NOTE
    # the stub run is not presented as a reviewed real loop
    assert summary["state"] != "Reviewed"
    assert summary["proof_status"] == "Template only — not a real attempt"
    # this run reached the prover human gate, so a continue is offerable
    assert summary["continue_available"] is True
