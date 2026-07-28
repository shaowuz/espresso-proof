"""The subgoal graph drives scheduling (Proof-Search V2).

`select_next_subgoal` deterministically chooses the highest-value OPEN subgoal to
work on next; the prover round names that subgoal id + local claim. Selection is
pure scheduling -- it never sets/upgrades any status, and a refuted (contradicted)
main goal yields no target so stale route optimism cannot override a claim-level
checked refutation.
"""

from __future__ import annotations

import json
from pathlib import Path

from espresso_proof.agents.llm_agents import agent_prompt
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.subgoal_graph import (
    assemble_subgoal_graph,
    group_subgoals,
    render_proof_search_lines,
    select_next_subgoal,
)

FIXTURES = Path("tests/fixtures")


def _graph(*nodes: dict) -> dict:
    return {"nodes": list(nodes)}


def _main(state: str = "open") -> dict:
    return {"id": "subgoal_main", "role": "main_goal", "state": state, "statement": "Show: X"}


def _sub(node_id: str, state: str) -> dict:
    return {"id": node_id, "role": "reduction", "state": state, "statement": f"work {node_id}"}


# --- deterministic priority selection --------------------------------------
def test_select_next_subgoal_follows_priority_order() -> None:
    graph = _graph(
        _main(),
        _sub("s_sketched", "sketched"),
        _sub("s_block_rev", "blocked_reviewer_objection"),
        _sub("s_open", "open"),
        _sub("s_block_src", "blocked_missing_source"),
    )
    # open is highest priority, regardless of node order
    assert select_next_subgoal(graph)["id"] == "s_open"
    # deterministic / stable across calls
    assert select_next_subgoal(graph)["id"] == "s_open"
    # group_subgoals shares the same selection (single source of truth)
    assert group_subgoals(graph)["next"]["id"] == "s_open"


def test_select_next_subgoal_falls_through_priority() -> None:
    # no open -> blocked_missing_source; then blocked_reviewer_objection; then sketched
    assert select_next_subgoal(_graph(_main(), _sub("a", "blocked_missing_source"),
                                      _sub("b", "blocked_reviewer_objection"),
                                      _sub("c", "sketched")))["id"] == "a"
    assert select_next_subgoal(_graph(_main(), _sub("b", "blocked_reviewer_objection"),
                                      _sub("c", "sketched")))["id"] == "b"
    assert select_next_subgoal(_graph(_main(), _sub("c", "sketched")))["id"] == "c"


def test_select_next_subgoal_skips_resolved_states() -> None:
    # source_supported / scoped_checked are resolved evidence states, not "to work on"
    graph = _graph(_main(), _sub("r", "source_supported"), _sub("k", "scoped_checked"))
    assert select_next_subgoal(graph) is None


def test_refuted_main_goal_selects_no_subgoal() -> None:
    # stale route optimism cannot override a claim-level checked refutation
    graph = _graph(_main(state="contradicted"), _sub("s_open", "open"))
    assert select_next_subgoal(graph) is None
    assert group_subgoals(graph)["next"] is None


def test_selection_is_read_only() -> None:
    graph = _graph(_main(), _sub("s_open", "open"))
    snapshot = json.dumps(graph, sort_keys=True)
    select_next_subgoal(graph)
    assert json.dumps(graph, sort_keys=True) == snapshot  # no mutation


# --- the prover prompt names the target subgoal ----------------------------
def test_prover_prompt_names_target_subgoal() -> None:
    target = {"id": "subgoal_block_001", "local_claim": "Resolve: center is nontrivial",
              "state": "open", "instruction": "Work on THIS subgoal only; cite its id in your steps."}
    prompt = agent_prompt({"target_subgoal": target}, role="prover_loop_round")
    assert "subgoal_block_001" in prompt
    assert "Resolve: center is nontrivial" in prompt
    assert "Work on THIS subgoal only" in prompt


# --- integration on a real run ---------------------------------------------
def test_refuted_run_graph_selects_no_subgoal(tmp_path: Path) -> None:
    run = run_mock_critique("g26", FIXTURES / "refuted_by_all_hypotheses_counterexample" / "input.md", tmp_path)
    graph = assemble_subgoal_graph(run)
    main = next(n for n in graph["nodes"] if n["role"] == "main_goal")
    assert main["state"] == "contradicted"
    assert select_next_subgoal(graph) is None


def test_proof_search_render_names_selected_subgoal_and_stays_a_map() -> None:
    graph = _graph(_main(), _sub("s_open", "open"))
    block = "\n".join(render_proof_search_lines(graph))
    assert "Next subgoal to work on [s_open]" in block  # names the id
    # the rendering remains a proof-search map, never a proof claim
    assert "proof-search map, not a proof" in block
