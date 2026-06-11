"""Goal 39: prover rounds are durably bound to a named subgoal.

The scheduler's pick was already threaded into the round PROMPT; these tests pin
that it is now also PERSISTED on the round record (``target_subgoal_id`` /
``target_local_claim``), that reviewer pointers declare their scope, and that
the binding stays advisory provenance: it can never invent graph nodes, change
subgoal state, or upgrade any status (graph states require earned evidence,
enforced by the existing validators).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.models import ProverLoopAttempt, ProverLoopReviewPointer
from espresso_proof.orchestrator import orchestrate_run, resume_run
from espresso_proof.subgoal_graph import assemble_subgoal_graph
from espresso_proof.validators import validate_run

FIXTURES = Path("tests/fixtures")


def _resumed_run(tmp_path: Path) -> Path:
    result = orchestrate_run(
        project="g39",
        input_path=FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    brief = read_yaml(
        run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["strategy_brief"]
    )
    resume_run(
        run_dir=run_dir,
        selected_strategy=brief["top_strategies"][0]["id"],
        effort="normal_pass",  # quick_test's 1-round budget is spent at the gate
        prover_provider="stub",
    )
    return run_dir


def test_appended_rounds_record_scheduler_selected_subgoal(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    loop = read_yaml(run_dir / "prover_loop.yaml")
    appended = [a for a in loop["attempts"] if a["round_index"] >= 2]
    assert appended, "stub loop should append at least one round"
    graph_ids = {node["id"] for node in assemble_subgoal_graph(run_dir)["nodes"]}
    by_id = {
        node["id"]: node for node in assemble_subgoal_graph(run_dir)["nodes"]
    }
    for attempt in appended:
        assert attempt["target_subgoal_id"] is not None
        assert attempt["target_subgoal_id"] in graph_ids
        node = by_id[attempt["target_subgoal_id"]]
        assert attempt["target_local_claim"] == node["statement"]
    validate_run(run_dir)


def test_initial_attempt_without_binding_still_validates(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    loop = read_yaml(run_dir / "prover_loop.yaml")
    first = next(a for a in loop["attempts"] if a["round_index"] == 1)
    # the gate-time initial attempt predates any scheduler pick: defaults apply.
    assert first.get("target_subgoal_id") is None
    assert first.get("target_local_claim", "") == ""
    ProverLoopAttempt.model_validate(first)


def test_pointer_subgoal_id_requires_subgoal_scope() -> None:
    base = {
        "verifier_id": "v1",
        "verdict": "needs_revision",
        "objection_count": 1,
        "recommended_human_action": "revise",
    }
    with pytest.raises(ValueError, match="scope='subgoal'"):
        ProverLoopReviewPointer.model_validate(
            {**base, "subgoal_id": "subgoal_main"}  # default scope is "route"
        )
    pointer = ProverLoopReviewPointer.model_validate(
        {**base, "scope": "subgoal", "subgoal_id": "subgoal_main"}
    )
    assert pointer.scope == "subgoal"
    # legacy pointers (no scope fields) still validate with the honest default.
    legacy = ProverLoopReviewPointer.model_validate(base)
    assert legacy.scope == "route"
    assert legacy.subgoal_id is None


def test_binding_is_advisory_and_cannot_touch_graph_state(tmp_path: Path) -> None:
    # A hand-edited (even nonsensical) target id is provenance noise, never a
    # graph mutation: the assembled graph derives nodes from earned evidence
    # only, so no node is invented, no state changes, and the run stays valid.
    run_dir = _resumed_run(tmp_path)
    graph_before = assemble_subgoal_graph(run_dir)
    loop = read_yaml(run_dir / "prover_loop.yaml")
    appended = [a for a in loop["attempts"] if a["round_index"] >= 2]
    appended[-1]["target_subgoal_id"] = "subgoal_invented_by_hand"
    write_yaml(run_dir / "prover_loop.yaml", loop)
    validate_run(run_dir)  # historical provenance; the graph is the live view
    graph_after = assemble_subgoal_graph(run_dir)
    assert {n["id"] for n in graph_after["nodes"]} == {
        n["id"] for n in graph_before["nodes"]
    }
    assert [n["state"] for n in graph_after["nodes"]] == [
        n["state"] for n in graph_before["nodes"]
    ]
