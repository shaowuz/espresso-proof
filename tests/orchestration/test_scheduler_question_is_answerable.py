"""A scheduler question must be answerable, not terminal.

Found by a fourth hostile review and observed live twice. When reviewer
objections repeat on a route, the Time Allocation scheduler stops the loop with
`ask_human` — the right call. But resume finalization then marked the run
COMPLETED_MEMO unconditionally, and every re-entry path refuses a completed run
(`_resume_idempotent_memo` says "already complete", `append_prover_loop_round`
raises on `loop_decision=ask_human`, `resolve_agent_gate` raises "already
resolved"). Even if re-entry had existed, the rebuilt plan would ask the same
question forever: the objection-round count is deliberately monotone.

Net effect: the machinery asked a question the human could not answer, and the
unspent part of a purchased budget was forfeited — deep_exploration dead-ended
after 2 of 3 new rounds, and the only way forward was to re-pay the entire
13-call front half in a new run.

The rule now: the terminal state records WHY the loop stopped. A run stopped to
ask a human waits in WAITING_TIME_ALLOCATION_DECISION, and
`espresso answer-scheduler` records the answer (scheduling-only) and spends the
budget the question was holding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.kernel.models import RunState, UpdateInboxEntry
from espresso_proof.kernel.time_allocation import (
    _human_answered_routes,
    _reviewer_objection_round_counts,
    _route_action,
)
from espresso_proof.orchestration.orchestrator import (
    ASK_HUMAN_STOP_REASONS,
    ORCHESTRATION_STATES,
    terminal_state_for_stop_reason,
)


def _objection(index: int, route: str = "strategy_route_001") -> dict:
    return {
        "id": f"obj_{index}",
        "type": "reviewer_objection",
        "effect": "scheduling_only",
        "advisory_only": True,
        "validator_ok": True,
        "route_id_or_step_id": route,
        "origin_artifact": "llm_agent_run.yaml",
        "artifact_revision_or_hash": f"hash_{index}",
    }


def _answer(route: str = "strategy_route_001") -> dict:
    return {
        "id": "human_sched_1",
        "type": "human_scheduling_decision",
        "effect": "scheduling_only",
        "advisory_only": True,
        "validator_ok": True,
        "route_id_or_step_id": route,
        "origin_artifact": "human_choice.yaml",
        "artifact_revision_or_hash": "hash_hc",
    }


# --- the state machine must distinguish "finished" from "waiting on you"


def test_an_ask_human_stop_does_not_complete_the_run() -> None:
    for reason in sorted(ASK_HUMAN_STOP_REASONS):
        assert terminal_state_for_stop_reason(reason) == "WAITING_TIME_ALLOCATION_DECISION"


@pytest.mark.parametrize(
    "reason", ["clean", "budget_rounds", "budget_agent_calls", "no_progress", "validation_error"]
)
def test_a_real_finish_still_completes(reason: str) -> None:
    assert terminal_state_for_stop_reason(reason) == "COMPLETED_MEMO"
    assert terminal_state_for_stop_reason(None) == "COMPLETED_MEMO"


def test_the_waiting_state_is_ordered_after_the_gate_and_before_completion() -> None:
    # Resume's boundary checks are index comparisons; a state outside the
    # ordered list returns -1 and would falsely read as "before the gate".
    assert RunState.WAITING_TIME_ALLOCATION_DECISION.value in ORCHESTRATION_STATES
    index = ORCHESTRATION_STATES.index("WAITING_TIME_ALLOCATION_DECISION")
    assert index > ORCHESTRATION_STATES.index("WAITING_LLM_AGENT_HUMAN_GATE")
    assert index > ORCHESTRATION_STATES.index("HUMAN_CHOICE_CONTINUE_SELECTED")
    assert index < ORCHESTRATION_STATES.index("COMPLETED_MEMO")


# --- the question must stop repeating once a human answers


def test_repeated_objections_reach_the_scheduler_question() -> None:
    inbox = {"entries": [_objection(i) for i in range(3)]}
    counts = _reviewer_objection_round_counts(inbox)
    assert counts["strategy_route_001"] == 3
    action, _ = _route_action(
        {"id": "strategy_route_001"},
        source_support_missing=False,
        reviewer_objection_rounds=counts["strategy_route_001"],
    )
    assert action == "ask_human"


def test_a_recorded_answer_releases_the_route() -> None:
    inbox = {"entries": [_objection(i) for i in range(3)] + [_answer()]}
    assert _human_answered_routes(inbox) == {"strategy_route_001"}
    counts = _reviewer_objection_round_counts(inbox)
    assert counts["strategy_route_001"] == 0
    action, _ = _route_action(
        {"id": "strategy_route_001"},
        source_support_missing=False,
        reviewer_objection_rounds=counts["strategy_route_001"],
    )
    assert action != "ask_human"


def test_an_answer_releases_only_its_own_route() -> None:
    inbox = {
        "entries": [_objection(i) for i in range(3)]
        + [_objection(i, route="strategy_route_002") for i in range(3, 6)]
        + [_answer("strategy_route_001")]
    }
    counts = _reviewer_objection_round_counts(inbox)
    assert counts["strategy_route_001"] == 0
    assert counts["strategy_route_002"] == 3


@pytest.mark.parametrize(
    "broken",
    [
        {"effect": "evidence"},
        {"advisory_only": False},
        {"validator_ok": False},
        {"type": "human_preference"},
    ],
)
def test_a_non_scheduling_answer_cannot_release_a_route(broken: dict) -> None:
    # The release is a scheduling-only mechanism; anything that is not a
    # validated scheduling-only entry must leave the question standing.
    inbox = {"entries": [_objection(i) for i in range(3)] + [{**_answer(), **broken}]}
    assert _human_answered_routes(inbox) == set()
    assert _reviewer_objection_round_counts(inbox)["strategy_route_001"] == 3


def test_the_answer_entry_is_schema_valid_and_carries_no_status() -> None:
    entry = UpdateInboxEntry.model_validate(
        {
            **_answer(),
            "origin_stage": "human_scheduling_answer",
            "origin_artifact_id_or_path": "human_choice.yaml",
            "locked_claim_id": "claim_001",
            "created_at": "2026-01-01T00:00:00+00:00",
            "dedupe_key": "human_scheduling_decision:strategy_route_001:x",
            "applied_at": None,
        }
    )
    assert entry.type == "human_scheduling_decision"
    assert entry.effect == "scheduling_only" and entry.advisory_only is True
    # No field on the entry can own a mathematical status.
    assert not {"status", "proof_status", "verdict", "earned_status"} & set(
        entry.model_dump()
    )


def test_the_decision_writer_refuses_junk(tmp_path) -> None:
    from espresso_proof.kernel.update_inbox_service import (
        UpdateInboxServiceError,
        record_human_scheduling_decision,
    )

    for decision, reason in (("maybe", "x"), ("continue", "   "), ("proved", "x")):
        with pytest.raises(UpdateInboxServiceError):
            record_human_scheduling_decision(
                tmp_path, route_id="r1", decision=decision, reason=reason
            )


# --- the answer must actually release the loop, end to end


def test_answering_releases_every_lock_the_question_held() -> None:
    """Three separate mechanisms held the run, and the first fix released one.

    Found by driving a real run through the dead end: after the state fix the
    CLI accepted the answer, and then (1) the applied-entry validator rejected
    the rebuilt plan, because an applied reviewer_objection whose route had
    moved off ask_human had no explanation; (2) the fresh inbox entry opened a
    pending disclosure the existing final report predated; (3) prover_loop.yaml
    still carried loop_decision=ask_human, so the driver broke before its first
    round and the answered run did zero work. All three are released together
    or the answer is theatre.
    """

    import inspect

    from espresso_proof.kernel.validators import scheduling
    from espresso_proof.orchestration import orchestrator

    validator = inspect.getsource(scheduling._validate_update_inbox_applied_at_consumption)
    assert "answered_routes" in validator
    assert "human_scheduling_decision" in validator

    answer = inspect.getsource(orchestrator.answer_scheduler_question)
    assert "refresh_final_report_disclosures" in answer
    assert 'loop.get("loop_decision") == "ask_human"' in answer
    # And it never touches a mathematical status.
    for forbidden in ("earned_status", "proof_language_status", "source_backed"):
        assert forbidden not in answer


# --- the question must not PARK the run either -------------------------------


def test_an_auto_answer_releases_the_route_but_is_not_a_human_answer() -> None:
    """The scheduler's question is about SPENDING, and spending is already
    bounded by max_rounds / max_agent_calls — parking the run until someone ran
    a CLI command added waiting, not safety. Machinery answers it now, in its
    own entry type, so no reader can mistake it for a person's decision."""

    inbox = {
        "entries": [_objection(i) for i in range(3)]
        + [{**_answer(), "type": "auto_scheduling_decision", "id": "auto_sched_1"}]
    }
    # Released for scheduling...
    assert _reviewer_objection_round_counts(inbox)["strategy_route_001"] == 0
    action, _ = _route_action(
        {"id": "strategy_route_001"},
        source_support_missing=False,
        reviewer_objection_rounds=0,
    )
    assert action != "ask_human"
    # ...but it is NOT recorded as a human having answered.
    assert _human_answered_routes(inbox) == set()


def test_the_driver_answers_instead_of_parking_and_says_so() -> None:
    import inspect

    from espresso_proof.orchestration import prover_loop_driver

    source = inspect.getsource(prover_loop_driver._auto_answer_scheduler_question)
    assert "record_auto_scheduling_decision" in source
    # Fail closed: a failure to record leaves the question standing.
    assert "parking" in source

    loop = inspect.getsource(prover_loop_driver._continue_prover_loop_locked)
    assert "_auto_answer_scheduler_question" in loop


def test_the_report_says_the_scheduler_decided_without_you(tmp_path) -> None:
    from espresso_proof.kernel.io import write_yaml
    from espresso_proof.kernel.surface_warnings import run_output_block

    write_yaml(
        tmp_path / "run_manifest.yaml",
        {"artifacts": {}, "optional_artifacts": {"update_inbox": "update_inbox.yaml"}},
    )
    write_yaml(
        tmp_path / "update_inbox.yaml",
        {
            "entries": [
                {"type": "auto_scheduling_decision", "route_id_or_step_id": "strategy_route_001"},
                {"type": "reviewer_objection", "route_id_or_step_id": "strategy_route_002"},
            ]
        },
    )
    block = run_output_block(tmp_path)
    assert "Scheduling decided without you" in block
    assert "strategy_route_001" in block and "strategy_route_002" not in block
    assert "answer-scheduler" in block


def test_the_auto_answer_lands_on_a_real_run_and_releases_it(tmp_path) -> None:
    """End to end on real artifacts: record the answer, and the route the
    scheduler was stuck on stops asking. The four-round live run never reached
    the ask_human threshold, so this is where the release is actually proven."""

    from espresso_proof.kernel.io import read_yaml
    from espresso_proof.kernel.time_allocation import _reviewer_objection_round_counts
    from espresso_proof.kernel.update_inbox_service import record_auto_scheduling_decision
    from espresso_proof.orchestration.orchestrator import orchestrate_run

    claim = tmp_path / "claim.md"
    claim.write_text("Every group of order 3 is cyclic.\n", encoding="utf-8")
    result = orchestrate_run(
        project="sched", input_path=claim, output_root=tmp_path,
        provider="stub", front_half="stub", auto_continue=True,
    )
    run_dir = Path(result["run_dir"])

    from espresso_proof.kernel.io import write_yaml

    route = read_yaml(run_dir / "prover_loop.yaml").get("selected_strategy_id") or "r1"
    claim_id = read_yaml(run_dir / "locked_claim.yaml")["id"]
    origin = "llm_agent_run.yaml"
    inbox = read_yaml(run_dir / "update_inbox.yaml")
    for index in range(3):
        inbox.setdefault("entries", []).append({
            **_objection(index, route),
            "origin_stage": "llm_prover_reviewers",
            "origin_artifact_id_or_path": origin,
            "locked_claim_id": claim_id,
            "created_at": f"2026-01-0{index + 1}T00:00:00+00:00",
            "dedupe_key": f"reviewer_objection:{route}:{index}",
            "applied_at": None,
        })
    write_yaml(run_dir / "update_inbox.yaml", inbox)
    # Three distinct objection ROUNDS on this route: the threshold that makes
    # the scheduler ask.
    assert _reviewer_objection_round_counts(inbox)[route] >= 3

    record_auto_scheduling_decision(
        run_dir, route_id=route, reason="answered so the run could continue"
    )
    inbox = read_yaml(run_dir / "update_inbox.yaml")
    assert _reviewer_objection_round_counts(inbox)[route] == 0  # released
    assert _human_answered_routes(inbox) == set()  # and not by a person
