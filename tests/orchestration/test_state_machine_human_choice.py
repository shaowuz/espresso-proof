"""Regression: the HUMAN_CHOICE_* states the CLI persists to current_state must be
in ORCHESTRATION_STATES, so resume_run's human-gate boundary check does not falsely
reject a re-resume of a run interrupted between recording the choice and the memo.

Also locks in the (correct) FRONT_HALF_DONE-before-CLAIM_CLEAR order so a future
"fix" cannot reorder it and silently stop CLAIM_CLEAR from ever being set.

Subgoal 01 of the Claude-reconstructs-Espresso-Proof plan.
"""
from __future__ import annotations

from espresso_proof.cli import _state_for_human_decision
from espresso_proof.kernel.models import ContinueDecision
from espresso_proof.orchestration.orchestrator import (
    ORCHESTRATION_STATES,
    _state_index,
)

GATE = "WAITING_LLM_AGENT_HUMAN_GATE"
HUMAN_CHOICE_STATES = (
    "HUMAN_CHOICE_CONTINUE_SELECTED",
    "HUMAN_CHOICE_REVISE_SELECTED",
    "HUMAN_CHOICE_STOP_SELECTED",
)


def test_cli_persisted_decision_states_are_known_to_the_state_machine() -> None:
    # Every current_state the CLI's choose-strategy can write (cli.py:1561) must be
    # a recognized orchestration state; otherwise _state_index returns -1.
    for decision in ContinueDecision:
        state = _state_for_human_decision(decision)
        assert state in ORCHESTRATION_STATES, f"{state} missing from ORCHESTRATION_STATES"
        assert _state_index(state) >= 0


def test_human_choice_states_sit_after_gate_and_before_memo() -> None:
    gate_idx = _state_index(GATE)
    memo_idx = _state_index("COMPLETED_MEMO")
    for state in HUMAN_CHOICE_STATES:
        idx = _state_index(state)
        assert idx >= gate_idx, f"{state} must be >= the human gate"
        assert idx < memo_idx, f"{state} must precede COMPLETED_MEMO"


def test_resume_boundary_does_not_falsely_reject_human_choice() -> None:
    # resume_run rejects when `_state_index(state) < _state_index(GATE)`. A run that
    # already recorded a human choice must NOT trip that check.
    for state in HUMAN_CHOICE_STATES:
        assert not (_state_index(state) < _state_index(GATE))


def test_completed_memo_is_the_terminal_state() -> None:
    memo_idx = _state_index("COMPLETED_MEMO")
    assert memo_idx == max(_state_index(s) for s in ORCHESTRATION_STATES)


def test_front_half_done_precedes_claim_clear() -> None:
    # The runtime sets FRONT_HALF_DONE (post front-half pass) BEFORE CLAIM_CLEAR
    # (post claim-clear gate). Reordering would make `if not _reached(CLAIM_CLEAR)`
    # short-circuit and CLAIM_CLEAR would never be set. Lock the order in.
    assert _state_index("FRONT_HALF_DONE") < _state_index("CLAIM_CLEAR")


# Pivot PR1: AUTO_CHOICE_CONTINUE_SELECTED is the honest auto-provenance peer of
# HUMAN_CHOICE_CONTINUE_SELECTED. Nothing writes it yet (the auto-continue writer
# is a later PR), but the state machinery must already accept it with identical
# ordering semantics so a persisted run carrying it validates and resumes.
AUTO_CHOICE_STATE = "AUTO_CHOICE_CONTINUE_SELECTED"


def test_auto_choice_state_is_known_to_the_state_machine() -> None:
    assert AUTO_CHOICE_STATE in ORCHESTRATION_STATES
    assert _state_index(AUTO_CHOICE_STATE) >= 0


def test_auto_choice_state_is_a_kernel_run_state() -> None:
    # RunManifest.current_state is a closed RunState enum: the kernel must
    # accept the auto state or a persisted run carrying it fails validation.
    from espresso_proof.kernel.models import RunState

    assert RunState(AUTO_CHOICE_STATE).value == AUTO_CHOICE_STATE


def test_auto_choice_state_orders_like_its_human_peer() -> None:
    # Identical ordering semantics to HUMAN_CHOICE_CONTINUE_SELECTED: after the
    # gate, before COMPLETED_MEMO, and never falsely rejected by the resume
    # boundary check (`_state_index(state) < _state_index(GATE)`).
    gate_idx = _state_index(GATE)
    memo_idx = _state_index("COMPLETED_MEMO")
    idx = _state_index(AUTO_CHOICE_STATE)
    assert idx >= gate_idx
    assert idx < memo_idx
    assert not (idx < gate_idx)
