from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import ProofGoalGraph, SubgoalNode
from espresso_proof.orchestration.orchestrator import orchestrate_run, resume_run
from espresso_proof.kernel.subgoal_graph import (
    assemble_subgoal_graph,
    build_subgoal_graph,
    render_proof_search_lines,
)
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.kernel.validators import validate_run


# ---------------------------------------------------------------------------
# run fixtures
# ---------------------------------------------------------------------------
def _claim(tmp_path: Path) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(
        "Let p be a prime. Every group G of order p^2 is abelian.\n",
        encoding="utf-8",
    )
    return path


def _gate_run(tmp_path: Path) -> Path:
    result = orchestrate_run(
        project="subgoal_demo",
        input_path=_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
    )
    return result["run_dir"]


def _resumed_run(tmp_path: Path) -> Path:
    run_dir = _gate_run(tmp_path)
    brief = read_yaml(run_dir / "strategy_brief.yaml")
    selected = brief["top_strategies"][0]["id"]
    resume_run(
        run_dir=run_dir,
        selected_strategy=selected,
        effort=_first_allowed_effort(brief, selected),
        # These tests simulate a human picking the strategy; provenance must be
        # explicit now that the fail-closed default records 'auto'.
        selected_by="human",
    )
    return run_dir


def _first_allowed_effort(brief: dict, selected: str) -> str:
    for choice in brief.get("allowed_human_choices") or []:
        if choice.get("strategy_id") == selected:
            options = list(choice.get("effort_options") or [])
            if options:
                return str(options[0])
    return "quick_test"


def _graph(run_dir: Path) -> dict:
    return read_yaml(run_dir / "proof_goal_graph.yaml")


def _write_graph(run_dir: Path, graph: dict) -> None:
    write_yaml(run_dir / "proof_goal_graph.yaml", graph)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["proof_goal_graph"] = "proof_goal_graph.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)


# ---------------------------------------------------------------------------
# model-level structure
# ---------------------------------------------------------------------------
def _minimal_graph_dict() -> dict:
    return {
        "schema_version": 1,
        "claim_id": "claim_001",
        "run_id": "run_x",
        "generated_at": "2026-06-07T00:00:00+00:00",
        "selected_strategy": None,
        "generated_from": {},
        "nodes": [
            {
                "id": "subgoal_main",
                "statement": "Show the claim holds.",
                "role": "main_goal",
                "state": "open",
                "closure_contract": "A grounded or checked argument confirmed by a human.",
                "remaining_risk": "Choose a route first.",
            }
        ],
        "edges": [],
        "summary": {
            "open": 1,
            "sketched": 0,
            "source_supported": 0,
            "finite_or_computation_checked": 0,
            "blocked": 0,
        },
        "notes": [],
    }


def test_valid_minimal_graph_passes_model() -> None:
    ProofGoalGraph.model_validate(_minimal_graph_dict())


def test_duplicate_node_ids_rejected() -> None:
    graph = _minimal_graph_dict()
    graph["nodes"].append(copy.deepcopy(graph["nodes"][0]))
    with pytest.raises(Exception):
        ProofGoalGraph.model_validate(graph)


def test_edge_to_unknown_node_rejected() -> None:
    graph = _minimal_graph_dict()
    graph["edges"].append({"from": "subgoal_main", "to": "nope", "relation": "needed_for"})
    with pytest.raises(Exception):
        ProofGoalGraph.model_validate(graph)


def test_requires_exactly_one_main_goal() -> None:
    graph = _minimal_graph_dict()
    graph["nodes"][0]["role"] = "lemma"
    with pytest.raises(Exception):
        ProofGoalGraph.model_validate(graph)


@pytest.mark.parametrize("token", ["proved", "proof-close", "verified", "proof complete"])
def test_graph_cannot_contain_proof_complete_language(token: str) -> None:
    graph = _minimal_graph_dict()
    graph["nodes"][0]["remaining_risk"] = f"This subgoal is {token}."
    with pytest.raises(Exception):
        ProofGoalGraph.model_validate(graph)


# ---------------------------------------------------------------------------
# run-validator cross-artifact rules
# ---------------------------------------------------------------------------
def test_builder_graph_validates_in_run(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    assert (run_dir / "proof_goal_graph.yaml").exists()
    validate_run(run_dir)


def test_claim_id_mismatch_rejected(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    graph = _graph(run_dir)
    graph["claim_id"] = "claim_999"
    _write_graph(run_dir, graph)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_run_id_mismatch_rejected(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    graph = _graph(run_dir)
    graph["run_id"] = "run_other"
    _write_graph(run_dir, graph)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_source_supported_without_earned_step_rejected(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    graph = _graph(run_dir)
    graph["nodes"].append(
        {
            "id": "subgoal_fake_source",
            "statement": "Cite a theorem.",
            "role": "reference_check",
            "state": "source_supported",
            "closure_contract": "An attributed source.",
            "supported_by_source_steps": ["step_does_not_exist"],
            "remaining_risk": "",
        }
    )
    graph["summary"]["source_supported"] += 1
    _write_graph(run_dir, graph)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_source_supported_state_requires_reference(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    graph = _graph(run_dir)
    graph["nodes"].append(
        {
            "id": "subgoal_bare_source",
            "statement": "Cite a theorem.",
            "role": "reference_check",
            "state": "source_supported",
            "closure_contract": "An attributed source.",
            "remaining_risk": "",
        }
    )
    _write_graph(run_dir, graph)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_scoped_checked_without_verification_rejected(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    graph = _graph(run_dir)
    graph["nodes"].append(
        {
            "id": "subgoal_fake_check",
            "statement": "A finite check.",
            "role": "computation_check",
            "state": "scoped_checked",
            "closure_contract": "A deterministic verifier confirms it.",
            "supported_by_computations": ["pv_missing"],
            "remaining_risk": "",
        }
    )
    _write_graph(run_dir, graph)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_scoped_checked_state_requires_reference(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    graph = _graph(run_dir)
    graph["nodes"].append(
        {
            "id": "subgoal_bare_check",
            "statement": "A finite check.",
            "role": "computation_check",
            "state": "scoped_checked",
            "closure_contract": "A deterministic verifier confirms it.",
            "remaining_risk": "",
        }
    )
    _write_graph(run_dir, graph)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_contradicted_without_refutation_rejected(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    # the stub p^2 run is not refuted, so a contradicted node must be rejected
    graph = _graph(run_dir)
    main = next(n for n in graph["nodes"] if n["role"] == "main_goal")
    main["state"] = "contradicted"
    _write_graph(run_dir, graph)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


# ---------------------------------------------------------------------------
# builder V0 behaviour
# ---------------------------------------------------------------------------
def test_p2_demo_creates_readable_subgoals(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    graph = _graph(run_dir)
    nodes = graph["nodes"]
    assert len(nodes) >= 2
    main = next(n for n in nodes if n["role"] == "main_goal")
    assert "abelian" in main["statement"].lower()
    # at least one subgoal remains open or only sketched
    assert any(n["state"] in {"open", "sketched"} for n in nodes)
    # no proof-complete language anywhere (model would also reject)
    blob = " ".join(
        [n["statement"] + n["closure_contract"] + n["remaining_risk"] for n in nodes]
    ).lower()
    for token in ("proved", "proven", "proof-close", "verified", "proof complete", "proof-complete"):
        assert token not in blob


def test_selected_route_creates_route_specific_subgoal(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    graph = _graph(run_dir)
    route_nodes = [n for n in graph["nodes"] if n["id"] == "subgoal_route"]
    assert route_nodes
    assert route_nodes[0]["state"] == "sketched"
    assert graph["selected_strategy"] is not None


# ---------------------------------------------------------------------------
# Typed route-death blockers (first slice)
# ---------------------------------------------------------------------------
def _prover_loop_path(run_dir: Path) -> Path:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    return run_dir / manifest["optional_artifacts"]["prover_loop"]


def _inject_blocker(run_dir: Path, blocker: str) -> None:
    """Prepend a blocker to the latest prover round so the graph builder sees it."""
    path = _prover_loop_path(run_dir)
    pl = read_yaml(path)
    attempts = pl.get("attempts") or [
        {"round_index": 0, "blockers": [], "reviewer_objections": []}
    ]
    latest = max(attempts, key=lambda a: a.get("round_index", 0))
    latest["blockers"] = [blocker, *(latest.get("blockers") or [])]
    pl["attempts"] = attempts
    write_yaml(path, pl)


def test_false_key_lemma_blocker_is_typed(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _inject_blocker(run_dir, "The key lemma 'reflexive implies free' is false in general.")
    graph = assemble_subgoal_graph(run_dir)
    typed = [n for n in graph["nodes"] if n.get("block_reason") == "false_key_lemma"]
    assert typed, "false key-lemma blocker should be typed as a dead route"
    node = typed[0]
    assert node["state"] == "blocked_reviewer_objection"
    assert node["suggested_next_action"]
    assert graph["summary"]["blocked"] >= 1
    # the typed graph still validates as a model (no smuggled status)
    ProofGoalGraph.model_validate(graph)


def test_external_tool_blocker_is_typed_and_counted(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _inject_blocker(
        run_dir, "This step needs an external tool (computer algebra) that is unavailable."
    )
    graph = assemble_subgoal_graph(run_dir)
    tool_nodes = [n for n in graph["nodes"] if n["state"] == "blocked_external_tool"]
    assert tool_nodes
    assert tool_nodes[0]["block_reason"] == "external_tool_blocked"
    assert graph["summary"]["blocked"] >= 1
    ProofGoalGraph.model_validate(graph)


def test_blocked_node_renders_translated_reason_without_raw_enum(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _inject_blocker(run_dir, "The key lemma 'reflexive implies free' is false in general.")
    graph = assemble_subgoal_graph(run_dir)
    blob = "\n".join(render_proof_search_lines(graph))
    # human wording + a concrete next step are shown
    assert "A key step looks false" in blob
    assert "suggested next step" in blob.lower()
    # the raw enum value / field name never leaks to the user-facing surface
    assert "false_key_lemma" not in blob
    assert "block_reason" not in blob


def _add_external_tool_run(run_dir: Path, tool_name: str, *, exit_code: int) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    etr = {
        "schema_version": 1,
        "claim_id": "claim_x",
        "run_id": manifest["run_id"],
        "generated_at": "2026-06-14T00:00:00+00:00",
        "runs": [
            {
                "id": "tool_1",
                "tool_name": tool_name,
                "scope_note": "scoped tool run, not a universal proof",
                "not_universal_proof": True,
                "exit_code": exit_code,
                "timed_out": False,
            }
        ],
        "notes": [],
    }
    write_yaml(run_dir / "external_tool_runs.yaml", etr)
    manifest.setdefault("optional_artifacts", {})["external_tool_runs"] = "external_tool_runs.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)


def test_failed_external_tool_run_becomes_blocked_node(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _add_external_tool_run(run_dir, "singular", exit_code=1)
    graph = assemble_subgoal_graph(run_dir)
    tool_nodes = [n for n in graph["nodes"] if n["id"].startswith("subgoal_tool_")]
    assert tool_nodes, "a non-clean tool run should surface a durable blocked node"
    node = tool_nodes[0]
    assert node["state"] == "blocked_external_tool"
    assert node["block_reason"] == "external_tool_blocked"
    assert node["suggested_next_action"]
    assert graph["summary"]["blocked"] >= 1
    # no earned status smuggled in
    assert not node.get("supported_by_source_steps")
    ProofGoalGraph.model_validate(graph)


def test_clean_external_tool_run_is_not_blocked(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _add_external_tool_run(run_dir, "lean", exit_code=0)  # clean exit
    graph = assemble_subgoal_graph(run_dir)
    assert not [n for n in graph["nodes"] if n["id"].startswith("subgoal_tool_")]


def test_block_reason_rejected_on_non_blocked_state() -> None:
    # a block_reason on an open (non-blocked) node smuggles dead-route typing
    with pytest.raises(ValidationError):
        SubgoalNode(
            id="subgoal_x",
            statement="An open subgoal.",
            role="lemma",
            state="open",
            closure_contract="Provide a grounded step.",
            block_reason="false_key_lemma",
        )


def test_non_evidence_state_cannot_reference_earned_support() -> None:
    # a blocked/conditional node must never reference earned source/checked support
    with pytest.raises(ValidationError):
        SubgoalNode(
            id="subgoal_x",
            statement="A blocked subgoal.",
            role="lemma",
            state="blocked_external_tool",
            closure_contract="Make the tool available.",
            supported_by_source_steps=["step_1"],
        )


def test_reviewer_objection_is_attachment_not_refutation(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    graph = _graph(run_dir)
    main = next(n for n in graph["nodes"] if n["role"] == "main_goal")
    # reviewer objections surface as advisory attachments, never as a refutation
    assert main["state"] != "contradicted"
    blocked = [
        n
        for n in graph["nodes"]
        if n["state"] in {"blocked_missing_source", "blocked_reviewer_objection"}
    ]
    assert main["reviewer_objections"] or blocked


def test_builder_is_idempotent(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    first = build_subgoal_graph(run_dir)
    first_bytes = (run_dir / "proof_goal_graph.yaml").read_bytes()
    second = build_subgoal_graph(run_dir)
    second_bytes = (run_dir / "proof_goal_graph.yaml").read_bytes()
    assert first == second
    assert first_bytes == second_bytes


def test_resume_refreshes_graph_and_is_idempotent(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    brief = read_yaml(run_dir / "strategy_brief.yaml")
    selected = brief["top_strategies"][0]["id"]
    effort = _first_allowed_effort(brief, selected)
    resume_run(run_dir=run_dir, selected_strategy=selected, effort=effort)
    assert (run_dir / "proof_goal_graph.yaml").exists()
    before = (run_dir / "proof_goal_graph.yaml").read_bytes()
    # a second resume is a no-op (run already completed) and leaves the graph stable
    resume_run(run_dir=run_dir, selected_strategy=selected, effort=effort)
    after = (run_dir / "proof_goal_graph.yaml").read_bytes()
    assert before == after
    validate_run(run_dir)


def test_no_route_selected_builds_single_open_main_goal(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    graph = build_subgoal_graph(run_dir)
    validate_run(run_dir)
    main_nodes = [node for node in graph["nodes"] if node["role"] == "main_goal"]
    assert len(main_nodes) == 1
    main = main_nodes[0]
    assert main["role"] == "main_goal"
    assert main["state"] == "open"
    assert "route" in main["remaining_risk"].lower()
    assert not main.get("supported_by_scoped_checks")


def _write_ledger(run_dir: Path, n_entries: int) -> None:
    """Write a theorem_condition_ledger with ``n_entries`` repair entries
    (subgoal_tcl_1 .. subgoal_tcl_N) and register it as an optional artifact."""
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    ledger = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": "2026-06-14T00:00:00+00:00",
        "entries": [
            {
                "id": f"tcl_{i}",
                "requested_theorem": f"Cited theorem {i}",
                "locator_status": "needs_exact_locator",
                "locator": None,
                "conditions": [
                    {"text": "G is finite", "claim_value": "", "match_status": "unknown"}
                ],
                "repair_subgoal_id": f"subgoal_tcl_{i}",
                "source_request_origin": "reviewer",
            }
            for i in range(1, n_entries + 1)
        ],
        "notes": [],
    }
    write_yaml(run_dir / "theorem_condition_ledger.yaml", ledger)
    manifest.setdefault("optional_artifacts", {})["theorem_condition_ledger"] = (
        "theorem_condition_ledger.yaml"
    )
    write_yaml(run_dir / "run_manifest.yaml", manifest)


def test_normal_ledger_built_graph_passes_validation(tmp_path: Path) -> None:
    # Residual (item 5): a graph built from a real ledger (every tcl node
    # backed by a ledger entry) passes validate_run.
    run_dir = _resumed_run(tmp_path)
    _write_ledger(run_dir, 2)
    build_subgoal_graph(run_dir)
    graph = _graph(run_dir)
    tcl_nodes = [n for n in graph["nodes"] if n["id"].startswith("subgoal_tcl_")]
    assert tcl_nodes, "ledger entries should create subgoal_tcl_* nodes"
    validate_run(run_dir)


def test_hallucinated_tcl_node_without_ledger_entry_fails(tmp_path: Path) -> None:
    # Residual (item 5): a subgoal_tcl_X node with NO matching ledger
    # entry is fail-closed rejected (stale/hallucinated repair node).
    run_dir = _resumed_run(tmp_path)
    _write_ledger(run_dir, 1)
    build_subgoal_graph(run_dir)
    graph = _graph(run_dir)
    main = next(n for n in graph["nodes"] if n["role"] == "main_goal")
    graph["nodes"].append(
        {
            "id": "subgoal_tcl_999",  # no ledger entry has this repair_subgoal_id
            "statement": "Check a cited theorem nobody requested.",
            "role": "hypothesis_check",
            "state": "open",
            "closure_contract": "List the theorem's hypotheses and match each.",
            "depends_on": [],
        }
    )
    main["depends_on"].append("subgoal_tcl_999")
    _write_graph(run_dir, graph)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_capped_ledger_graph_still_passes(tmp_path: Path) -> None:
    # Residual (item 5): a >5-entry ledger produces a CAPPED graph (fewer
    # tcl nodes than ledger entries). The id-consistency check must not falsely
    # fail — the graph holds a strict subset of the ledger's repair ids.
    run_dir = _resumed_run(tmp_path)
    _write_ledger(run_dir, 6)  # > _MAX_LEDGER_SUBGOALS (4)
    build_subgoal_graph(run_dir)
    graph = _graph(run_dir)
    tcl_nodes = [n for n in graph["nodes"] if n["id"].startswith("subgoal_tcl_")]
    # the cap means FEWER graph nodes than ledger entries, never extra
    assert 0 < len(tcl_nodes) < 6
    validate_run(run_dir)


def test_source_backed_step_attaches_only_to_matching_subgoal(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    sg["steps"].append(
        {
            "proof_step_id": "step_z",
            "needed_theorem": "Lagrange's theorem",
            "status": "source_backed",
            "source_refs": ["ref_z"],
            "hypothesis_match": {"matches": True, "mismatches": []},
            "note": "for the test",
        }
    )
    write_yaml(run_dir / "source_grounding.yaml", sg)
    graph = assemble_subgoal_graph(run_dir)
    src_nodes = [n for n in graph["nodes"] if n["id"] == "subgoal_src_step_z"]
    assert src_nodes
    assert src_nodes[0]["state"] == "source_supported"
    assert src_nodes[0]["supported_by_source_steps"] == ["step_z"]
    # the earned step is NOT attached to the whole-theorem main goal
    main = next(n for n in graph["nodes"] if n["role"] == "main_goal")
    assert "step_z" not in main.get("supported_by_source_steps", [])


def test_scoped_check_renders_finite_scope(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    claim = read_yaml(run_dir / "locked_claim.yaml")
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    write_yaml(
        run_dir / "computation_verification.yaml",
        {
            "schema_version": 1,
            "claim_id": claim["id"],
            "generated_at": "2026-06-07T00:00:00+00:00",
            "backend": "finite_group_cayley",
            "results": [
                {
                    "check_id": "chk_1",
                    "claim_id": claim["id"],
                    "kind": "finite_group_cayley",
                    "backend": "finite_group_cayley",
                    "executed": True,
                    "verdict": "confirmed",
                    "computed": "abelian",
                    "expected": "abelian",
                }
            ],
        },
    )
    manifest.setdefault("optional_artifacts", {})["computation_verification"] = (
        "computation_verification.yaml"
    )
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    graph = assemble_subgoal_graph(run_dir)
    cv_nodes = [n for n in graph["nodes"] if n["id"] == "subgoal_cv_chk_1"]
    assert cv_nodes
    node = cv_nodes[0]
    assert node["state"] == "scoped_checked"
    assert node["supported_by_scoped_checks"] == ["chk_1"]
    assert "finite or computational scope" in node["remaining_risk"].lower()


# ---------------------------------------------------------------------------
# dashboard / local UI rendering (Task 4)
# ---------------------------------------------------------------------------
def test_dashboard_renders_proof_state_section(tmp_path: Path) -> None:
    from espresso_proof.orchestration.run_dashboard import render_run_dashboard

    run_dir = _resumed_run(tmp_path)
    html = render_run_dashboard(run_dir)
    assert "Proof State" in html
    assert "Main goal" in html
    assert "Next subgoal to work on" in html
    # human wording, not raw internal enum names
    for raw in (
        "blocked_missing_source",
        "blocked_reviewer_objection",
        "scoped_checked",
        "source_supported",
        "main_goal",
        # Residual: new state/reason enum strings must also never leak.
        "blocked_external_tool",
        "conditional",
        "false_key_lemma",
        "theorem_condition_gap",
        "hard_research_blocker",
        "external_tool_blocked",
    ):
        assert raw not in html


def test_dashboard_blocked_row_shows_translated_reason_and_next_step(tmp_path: Path) -> None:
    # Residual (item 3): the dashboard's blocked rows reach memo parity —
    # they render "why blocked" (translated, never the raw enum) and the suggested
    # next step. Inject a typed external-tool blocker, rebuild, and render.
    from espresso_proof.orchestration.run_dashboard import render_run_dashboard

    run_dir = _resumed_run(tmp_path)
    _add_external_tool_run(run_dir, "singular", exit_code=1)
    build_subgoal_graph(run_dir)
    html = render_run_dashboard(run_dir)
    # translated wording + a concrete next step are shown
    assert "An external tool is unavailable" in html
    assert "Why blocked" in html
    assert "Suggested next step" in html
    # the raw enum value / field name never leaks to the user-facing surface
    for raw in ("external_tool_blocked", "blocked_external_tool", "block_reason"):
        assert raw not in html


def test_proof_state_section_has_no_proof_complete_language(tmp_path: Path) -> None:
    from espresso_proof.orchestration.run_dashboard import render_run_dashboard

    run_dir = _resumed_run(tmp_path)
    html = render_run_dashboard(run_dir)
    start = html.index("<h2>Proof State</h2>")
    section = html[start : html.index("</section>", start)].lower()
    for token in ("proof complete", "proof-complete", "proof_complete", "proof-close", "proved", "proven", "verified"):
        assert token not in section


def test_dashboard_renders_without_graph(tmp_path: Path) -> None:
    from espresso_proof.orchestration.run_dashboard import render_run_dashboard

    run_dir = _gate_run(tmp_path)  # no proof_goal_graph.yaml at the gate
    assert not (run_dir / "proof_goal_graph.yaml").exists()
    html = render_run_dashboard(run_dir)
    assert "Espresso Proof" in html
    assert "Proof State" not in html


def test_dashboard_render_is_read_only_for_yaml(tmp_path: Path) -> None:
    from espresso_proof.orchestration.run_dashboard import render_run_dashboard

    run_dir = _resumed_run(tmp_path)
    before = {
        p.name: p.read_bytes() for p in run_dir.glob("*.yaml")
    }
    render_run_dashboard(run_dir)
    after = {p.name: p.read_bytes() for p in run_dir.glob("*.yaml")}
    assert before == after


def test_persisted_dashboard_html_shows_proof_state(tmp_path: Path) -> None:
    # resume writes research_dashboard.html; the persisted file (which any
    # viewer/local UI serves) carries the Proof State section.
    run_dir = _resumed_run(tmp_path)
    html = (run_dir / "research_dashboard.html").read_text(encoding="utf-8")
    assert "Proof State" in html


def test_cli_build_subgoal_graph(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    result = CliRunner().invoke(app, ["build-subgoal-graph", "--run", str(run_dir)])
    assert result.exit_code == 0, result.stdout
    assert "subgoal graph" in result.stdout.lower()
    assert (run_dir / "proof_goal_graph.yaml").exists()
    validate_run(run_dir)


def test_verify_finite_groups_refreshes_subgoal_graph_and_dashboard(tmp_path: Path) -> None:
    # Regression for the stale-derived-views bug: after a scoped finite check
    # appends proof_verification.yaml, the existing proof-search graph + dashboard
    # must be refreshed so the memo, graph, and dashboard agree (no surface shows
    # "0 checked" while another shows the checked instances). Display-only refresh:
    # it re-validates and never upgrades any status.
    run_dir = _resumed_run(tmp_path)  # resume builds the graph + dashboard
    graph_before = _graph(run_dir)
    before_count = graph_before["summary"]["finite_or_computation_checked"]
    assert (run_dir / "research_dashboard.html").exists()

    result = CliRunner().invoke(
        app, ["verify-finite-groups", "--run", str(run_dir), "--prime", "2"]
    )
    assert result.exit_code == 0, result.stdout

    confirmed = [
        e
        for e in read_yaml(run_dir / "proof_verification.yaml")["entries"]
        if e["earned_status"] == "instance_checked"
    ]
    assert confirmed, "expected at least one instance_checked entry"

    graph_after = _graph(run_dir)
    assert graph_after["summary"]["finite_or_computation_checked"] == len(confirmed)
    scoped = [n for n in graph_after["nodes"] if n.get("state") == "scoped_checked"]
    assert len(scoped) == len(confirmed)
    # the refreshed dashboard now shows the finite/computational-scope wording
    html = (run_dir / "research_dashboard.html").read_text(encoding="utf-8")
    assert "finite/computational scope" in html
    # display-only: the run still validates and the claim grade is not upgraded
    validate_run(run_dir)
    grade = read_yaml(run_dir / "idea_grade.yaml")
    assert grade["proof_language_status"] == "idea_sketch"
    assert before_count != graph_after["summary"]["finite_or_computation_checked"]


def test_resume_completed_memo_refreshes_stale_graph(tmp_path: Path) -> None:
    # Regression: a re-resume on an already-COMPLETED_MEMO run (e.g. with a pre-loop
    # --auto-wire-verifications / --audit-evidence step) appends scoped evidence
    # before the idempotent early return. That early return must refresh the
    # proof-search graph so it does not stay stale relative to proof_verification.
    from espresso_proof.orchestration.orchestrator import resume_run
    from espresso_proof.verification.proof_verification import verify_finite_groups

    run_dir = _resumed_run(tmp_path)  # COMPLETED_MEMO; graph built
    sid = read_yaml(run_dir / "strategy_brief.yaml")["top_strategies"][0]["id"]
    before_count = _graph(run_dir)["summary"]["finite_or_computation_checked"]

    # Append scoped evidence out-of-band (library writer; no derived-view refresh).
    verify_finite_groups(run_dir, primes=[2])
    confirmed = [
        e
        for e in read_yaml(run_dir / "proof_verification.yaml")["entries"]
        if e["earned_status"] == "instance_checked"
    ]
    assert _graph(run_dir)["summary"]["finite_or_computation_checked"] == before_count  # stale

    # Re-resume: the COMPLETED_MEMO early return must refresh the graph.
    resume_run(
        run_dir=run_dir,
        selected_strategy=sid,
        effort=_first_allowed_effort(read_yaml(run_dir / "strategy_brief.yaml"), sid),
    )
    assert confirmed
    assert _graph(run_dir)["summary"]["finite_or_computation_checked"] == len(confirmed)
    validate_run(run_dir)
    assert read_yaml(run_dir / "idea_grade.yaml")["proof_language_status"] == "idea_sketch"


# ---------------------------------------------------------------------------
# "Negative Tests Required" — closure can never be earned by prose / preference
# / memory / finite examples / dropped-hypothesis counterexamples.
# ---------------------------------------------------------------------------
_EVIDENCE_STATES = {"source_supported", "scoped_checked"}


def test_human_strategy_preference_does_not_close_a_subgoal(tmp_path: Path) -> None:
    # A confirmed human strategy selection drives the resumed run, yet no subgoal
    # reaches an evidence (source_supported / scoped_checked) or contradicted state
    # from that preference alone.
    run_dir = _resumed_run(tmp_path)
    hc = read_yaml(run_dir / "human_choice.yaml")
    assert hc["status"] == "selected" and hc["human_confirmed"] is True
    graph = _graph(run_dir)
    main = next(node for node in graph["nodes"] if node["role"] == "main_goal")
    assert main["state"] not in _EVIDENCE_STATES
    assert main["state"] != "contradicted"
    assert not main.get("supported_by_scoped_checks")


def test_memory_does_not_close_a_subgoal(tmp_path: Path) -> None:
    # memory_context is built during resume; the subgoal graph never reads it as
    # evidence, so no node may be closed/supported because of memory.
    run_dir = _resumed_run(tmp_path)
    assert (run_dir / "memory_context.yaml").exists()
    graph = assemble_subgoal_graph(run_dir)
    main = next(node for node in graph["nodes"] if node["role"] == "main_goal")
    assert main["state"] not in _EVIDENCE_STATES
    assert not main.get("supported_by_scoped_checks")


def test_finite_examples_do_not_close_a_universal_subgoal(tmp_path: Path) -> None:
    # The run has example/computation checks in computations.yaml, but only a
    # deterministic verifier (computation_verification / proof_verification) can
    # yield a scoped_checked node -- and never the universal main goal.
    run_dir = _resumed_run(tmp_path)
    computations = read_yaml(run_dir / "computation_checks.yaml")
    assert computations  # example/computation checks exist for this run
    graph = assemble_subgoal_graph(run_dir)
    main = next(n for n in graph["nodes"] if n["role"] == "main_goal")
    assert main["state"] == "open"
    assert not main.get("supported_by_scoped_checks")
    assert not main.get("supported_by_computations")
    for node in graph["nodes"]:
        if node["state"] == "scoped_checked":
            assert node["role"] != "main_goal"


def test_dropped_hypothesis_counterexample_does_not_close_the_claim(tmp_path: Path) -> None:
    # The stub p^2 run is not refuted by the kernel; attaching counterexample
    # pressure to the main goal must NOT contradict it, and a hand-edited
    # contradicted main goal is rejected by the validator.
    run_dir = _resumed_run(tmp_path)
    idea = read_yaml(run_dir / "idea_grade.yaml")
    assert idea["status"] != "refuted"
    graph = _graph(run_dir)
    main = next(n for n in graph["nodes"] if n["role"] == "main_goal")
    assert main["state"] != "contradicted"
    # even with counterexample pressure attached, the claim cannot be closed as refuted
    main["state"] = "contradicted"
    main.setdefault("counterexample_pressure", []).append(
        "Holds for small cases; the dropped-hypothesis case is not a refutation."
    )
    _write_graph(run_dir, graph)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def _add_step_only_counterexample(run_dir: Path, *, explanation: str) -> None:
    """Append a TRUSTED step-only checked counterexample to the counterexamples
    artifact (mirrors the 57C polynomial witness scope: refutes a proof step, not
    the claim)."""
    path = run_dir / "counterexamples.yaml"
    bank = read_yaml(path)
    bank.setdefault("tests", []).append(
        {
            "id": "polywitness_step_only",
            "operator_id": "op_poly",
            "example_id": "ex_poly",
            "satisfies_all_claim_hypotheses": False,
            "target": "proof_step",
            "result": "failed",
            "refutes_claim": False,
            "refutes_step_only": True,
            "exposes_missing_hypothesis": False,
            "explanation": explanation,
        }
    )
    write_yaml(path, bank)


def test_step_only_counterexample_is_pressure_not_a_refutation(tmp_path: Path) -> None:
    # Residual (item 4): a TRUSTED step-only checked counterexample
    # surfaces as advisory pressure on the main node, but it can NEVER refute the
    # claim and NEVER set any node to "contradicted".
    run_dir = _resumed_run(tmp_path)
    _add_step_only_counterexample(
        run_dir, explanation="(x+y)^3 != x^3 + y^3 at a rational point; a proof step fails."
    )
    graph = assemble_subgoal_graph(run_dir)
    main = next(n for n in graph["nodes"] if n["role"] == "main_goal")

    # the witness appears as advisory pressure text, clearly scoped to a step
    assert any(
        "refutes a proof step, not the claim" in p for p in main["counterexample_pressure"]
    )
    # HARD SAFETY: no node becomes contradicted, and the claim is not refuted
    assert all(n["state"] != "contradicted" for n in graph["nodes"])
    idea = read_yaml(run_dir / "idea_grade.yaml")
    assert idea["status"] != "refuted"
    # the typed graph still validates as a model
    ProofGoalGraph.model_validate(graph)


def test_step_only_counterexample_render_is_scoped(tmp_path: Path) -> None:
    # Residual (item 4): the render shows the step-only witness scoped to
    # a proof step, never as a claim refutation.
    run_dir = _resumed_run(tmp_path)
    _add_step_only_counterexample(
        run_dir, explanation="A proof step fails at a rational point."
    )
    from espresso_proof.kernel.pressure_status import build_pressure_status

    build_pressure_status(run_dir)
    build_subgoal_graph(run_dir)
    graph = _graph(run_dir)
    text = "\n".join(render_proof_search_lines(graph))
    assert "refutes a proof step, not the claim" in text
    # advisory framing, never a refutation of the claim
    assert "not a refutation" in text.lower()
    # the run still validates after persisting the step-only witness
    validate_run(run_dir)


def test_graph_does_not_upgrade_grade_or_status(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    grade_before = read_yaml(run_dir / "idea_grade.yaml")
    lang_before = grade_before["proof_language_status"]
    build_subgoal_graph(run_dir)
    validate_run(run_dir)
    grade_after = read_yaml(run_dir / "idea_grade.yaml")
    assert grade_after == grade_before
    assert grade_after["proof_language_status"] == lang_before


# ---------------------------------------------------------------------------
# memo proof-search section + single-source rendering (dashboard <-> memo)
# ---------------------------------------------------------------------------
def test_render_proof_search_lines_uses_human_wording_and_disclaimer() -> None:
    from espresso_proof.kernel.subgoal_graph import render_proof_search_lines

    graph = {
        "nodes": [
            {"role": "main_goal", "statement": "Show the claim.", "state": "open"},
            {"role": "reduction", "statement": "Lock the claim.", "state": "sketched"},
            {"role": "lemma", "statement": "Find the source.", "state": "blocked_missing_source"},
        ]
    }
    lines = render_proof_search_lines(graph)
    text = "\n".join(lines)
    assert "Proof-search state (a map of the search, not a proof):" in text
    assert "Main goal [Open]:" in text
    assert "Only sketched:" in text
    assert "Blocked by a missing source" in text
    assert "Next subgoal to work on" in text
    assert "proof-search map, not a proof" in text
    # human wording only — no raw internal enum names leak into the memo text
    for raw in ("blocked_missing_source", "main_goal", "scoped_checked", "source_supported"):
        assert raw not in text
    # never proof-complete language
    for token in ("proof complete", "proof-close", "proved", "proven", "verified"):
        assert token not in text.lower()


def test_render_proof_search_lines_empty_graph_returns_nothing() -> None:
    from espresso_proof.kernel.subgoal_graph import render_proof_search_lines

    assert render_proof_search_lines({}) == []
    assert render_proof_search_lines({"nodes": []}) == []
    assert render_proof_search_lines(None) == []


def test_render_proof_search_lines_truncates_long_statements() -> None:
    from espresso_proof.kernel.subgoal_graph import render_proof_search_lines

    graph = {"nodes": [{"role": "main_goal", "statement": "x" * 500, "state": "open"}]}
    line = next(l for l in render_proof_search_lines(graph) if l.startswith("  Main goal"))
    assert line.endswith("…")
    assert len(line) < 260  # capped, not the full 500-char blob


def test_memo_includes_proof_search_section_when_graph_present(tmp_path: Path) -> None:
    from espresso_proof.ui.memo import build_concise_memo

    run_dir = _resumed_run(tmp_path)  # resume auto-builds the subgoal graph
    assert (run_dir / "proof_goal_graph.yaml").exists()
    memo = build_concise_memo(run_dir)
    assert "Proof-search state (a map of the search, not a proof):" in memo
    assert "Next subgoal to work on" in memo
    # no proof-complete language anywhere in the memo
    for token in ("proof complete", "proof-complete", "proof-close", "proved", "proven", "verified theorem"):
        assert not re.search(rf"\b{re.escape(token)}\b", memo.lower())


def test_memo_omits_proof_search_section_without_graph(tmp_path: Path) -> None:
    from espresso_proof.ui.memo import build_concise_memo

    run_dir = _gate_run(tmp_path)  # at the gate: no proof_goal_graph.yaml yet
    assert not (run_dir / "proof_goal_graph.yaml").exists()
    memo = build_concise_memo(run_dir)
    assert "Proof-search state" not in memo


def test_memo_render_is_read_only_for_yaml(tmp_path: Path) -> None:
    from espresso_proof.ui.memo import build_concise_memo

    run_dir = _resumed_run(tmp_path)
    before = {p.name: p.read_bytes() for p in run_dir.glob("*.yaml")}
    build_concise_memo(run_dir)
    after = {p.name: p.read_bytes() for p in run_dir.glob("*.yaml")}
    assert before == after


def test_dashboard_and_memo_share_one_wording_source() -> None:
    # The dashboard must render the proof-search state from the same wording
    # source as the memo (no second enum->wording map to drift out of sync).
    from espresso_proof.orchestration import run_dashboard
    from espresso_proof.kernel import subgoal_graph

    assert run_dashboard._state_words is subgoal_graph.state_words
    assert run_dashboard._PROOF_SEARCH_NOTE is subgoal_graph.PROOF_SEARCH_NOTE
    assert run_dashboard._group_subgoals is subgoal_graph.group_subgoals


# ---------------------------------------------------------------------------
# Surface proof_goal_graph.notes in memo + dashboard (display only)
# ---------------------------------------------------------------------------
def test_graph_notes_helper_filters_blank_and_strips() -> None:
    from espresso_proof.kernel.subgoal_graph import graph_notes

    assert graph_notes(None) == []
    assert graph_notes({}) == []
    assert graph_notes({"notes": []}) == []
    assert graph_notes({"notes": ["  ", ""]}) == []
    assert graph_notes({"notes": ["  keep me  ", "", "second"]}) == ["keep me", "second"]


def test_render_proof_search_lines_shows_notes_when_present() -> None:
    from espresso_proof.kernel.subgoal_graph import render_proof_search_lines

    graph = {
        "nodes": [{"role": "main_goal", "statement": "Show the claim.", "state": "open"}],
        "notes": ["Some prover blockers are not shown (subgoal display is capped)."],
    }
    text = "\n".join(render_proof_search_lines(graph))
    assert "About this proof-search view:" in text
    assert "subgoal display is capped" in text
    # the cap caveat must be shown in full, never truncated
    assert "Some prover blockers are not shown (subgoal display is capped)." in text


def test_render_proof_search_lines_omits_notes_block_when_absent() -> None:
    from espresso_proof.kernel.subgoal_graph import render_proof_search_lines

    graph = {"nodes": [{"role": "main_goal", "statement": "Show the claim.", "state": "open"}]}
    text = "\n".join(render_proof_search_lines(graph))
    assert "About this proof-search view" not in text


def test_memo_shows_graph_notes_when_present(tmp_path: Path) -> None:
    # A gate-run graph (no route selected yet) records a human-readable note.
    from espresso_proof.ui.memo import build_concise_memo

    run_dir = _gate_run(tmp_path)
    build_subgoal_graph(run_dir)
    notes = read_yaml(run_dir / "proof_goal_graph.yaml").get("notes") or []
    assert notes, "expected the gate-run graph to record at least one note"
    memo = build_concise_memo(run_dir)
    assert "About this proof-search view:" in memo
    assert notes[0] in memo
    for token in ("proof complete", "proof-complete", "proof-close", "proved", "verified theorem"):
        assert token not in memo.lower()


def test_dashboard_shows_graph_notes_when_present(tmp_path: Path) -> None:
    from espresso_proof.orchestration.run_dashboard import render_run_dashboard

    run_dir = _gate_run(tmp_path)
    build_subgoal_graph(run_dir)
    notes = read_yaml(run_dir / "proof_goal_graph.yaml").get("notes") or []
    assert notes
    html = render_run_dashboard(run_dir)
    assert "About this proof-search view" in html
    assert notes[0] in html


def test_dashboard_notes_are_html_escaped() -> None:
    from espresso_proof.orchestration.run_dashboard import _proof_state_section

    graph = {
        "nodes": [{"role": "main_goal", "statement": "Show the claim.", "state": "open"}],
        "notes": ['<script>alert("x")</script> & more'],
    }
    section = _proof_state_section({"graph": graph})
    assert "&lt;script&gt;" in section
    assert "<script>" not in section
    assert "&amp; more" in section


def test_dashboard_omits_notes_block_when_absent() -> None:
    from espresso_proof.orchestration.run_dashboard import _proof_state_section

    graph = {"nodes": [{"role": "main_goal", "statement": "Show the claim.", "state": "open"}]}
    section = _proof_state_section({"graph": graph})
    assert "sg-notes" not in section
    assert "About this proof-search view" not in section


def test_rendering_notes_does_not_mutate_graph_artifact(tmp_path: Path) -> None:
    from espresso_proof.ui.memo import build_concise_memo
    from espresso_proof.orchestration.run_dashboard import render_run_dashboard

    run_dir = _gate_run(tmp_path)
    build_subgoal_graph(run_dir)
    before = (run_dir / "proof_goal_graph.yaml").read_bytes()
    build_concise_memo(run_dir)
    render_run_dashboard(run_dir)
    after = (run_dir / "proof_goal_graph.yaml").read_bytes()
    assert before == after


# ---------------------------------------------------------------------------
# Theorem-condition ledger -> named repair subgoals
# ---------------------------------------------------------------------------
_TCL_SOURCE_REQUESTS = ["Cauchy theorem for finite groups", "Class equation argument"]
_TCL_MISSING_HYPOTHESES = ["G is finite", "p is prime"]


def _inject_reviewer_requests(
    run_dir: Path, source_requests: list[str], missing_hypotheses: list[str]
) -> None:
    """Add verbatim source_requests + missing_hypotheses to the first verifier
    review of the gate-time llm_agent_run, mirroring them into the matching
    round_001 review so the run cross-checks still pass (mirrors the ledger
    test helper)."""
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    optional = manifest["optional_artifacts"]

    agent_path = run_dir / optional["llm_agent_run"]
    agent = read_yaml(agent_path)
    verifier_id = agent["verifier_reviews"][0]["verifier_id"]
    agent["verifier_reviews"][0]["source_requests"] = source_requests
    agent["verifier_reviews"][0]["missing_hypotheses"] = missing_hypotheses
    write_yaml(agent_path, agent)

    index = read_yaml(run_dir / optional["prover_round_index"])
    round_one = next(r for r in index["rounds"] if r["round_index"] == 1)
    round_path = run_dir / round_one["artifact_path"]
    round_report = read_yaml(round_path)
    for review in round_report["verifier_reviews"]:
        if review["verifier_id"] == verifier_id:
            review["source_requests"] = source_requests
            review["missing_hypotheses"] = missing_hypotheses
    write_yaml(round_path, round_report)


def _ledger_run(tmp_path: Path) -> Path:
    from espresso_proof.verification.theorem_condition_ledger import build_theorem_condition_ledger

    run_dir = _resumed_run(tmp_path)
    _inject_reviewer_requests(run_dir, _TCL_SOURCE_REQUESTS, _TCL_MISSING_HYPOTHESES)
    build_theorem_condition_ledger(run_dir)
    return run_dir


def test_ledger_entry_becomes_open_hypothesis_check_repair_node(tmp_path: Path) -> None:
    run_dir = _ledger_run(tmp_path)
    ledger = read_yaml(run_dir / "theorem_condition_ledger.yaml")
    graph = assemble_subgoal_graph(run_dir)

    tcl_nodes = [n for n in graph["nodes"] if n["id"].startswith("subgoal_tcl_")]
    # one repair node per ledger entry, by ledger repair_subgoal_id (single writer)
    assert {n["id"] for n in tcl_nodes} == {e["repair_subgoal_id"] for e in ledger["entries"]}
    assert len(tcl_nodes) == len(ledger["entries"]) >= 2
    for node in tcl_nodes:
        assert node["role"] == "hypothesis_check"
        # slice-1 conditions are all "unknown" -> open named repair target
        assert node["state"] == "open"
        assert "block_reason" not in node
        # advisory search state only: never any earned support
        assert not node.get("supported_by_source_steps")
        assert not node.get("supported_by_computations")
        assert not node.get("supported_by_scoped_checks")
    ProofGoalGraph.model_validate(graph)
    validate_run(run_dir)


def test_ledger_repair_node_objection_attaches_to_that_node(tmp_path: Path) -> None:
    run_dir = _ledger_run(tmp_path)
    ledger = read_yaml(run_dir / "theorem_condition_ledger.yaml")
    target_id = ledger["entries"][0]["repair_subgoal_id"]

    # a reviewer scoped objection naming the ledger repair node (54B attach path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    agent_path = run_dir / manifest["optional_artifacts"]["llm_agent_run"]
    agent = read_yaml(agent_path)
    agent["verifier_reviews"][0].setdefault("scoped_objections", []).append(
        {
            "id": "v1_obj_tcl",
            "text": "This cited theorem's finiteness hypothesis is not justified.",
            "scope": "subgoal",
            "scope_source": "explicit",
            "scope_inferred": False,
            "inference_reason": "",
            "subgoal_id": target_id,
            "proof_step_id": None,
            "kind": "missing_source",
            "severity": "P1",
            "suggested_next_action": "List the theorem's hypotheses and check each.",
        }
    )
    write_yaml(agent_path, agent)

    graph = assemble_subgoal_graph(run_dir)
    node_by_id = {n["id"]: n for n in graph["nodes"]}
    target = node_by_id[target_id]
    assert any("finiteness hypothesis" in o for o in target.get("reviewer_objections", []))
    # it landed on the repair node, NOT the main goal
    main = next(n for n in graph["nodes"] if n["role"] == "main_goal")
    assert not any("finiteness hypothesis" in o for o in main.get("reviewer_objections", []))


def test_unmatched_condition_makes_repair_node_blocked_with_translated_wording(
    tmp_path: Path,
) -> None:
    run_dir = _ledger_run(tmp_path)
    # hand-set one condition to unmatched -> theorem_condition_gap repair node
    ledger_path = run_dir / "theorem_condition_ledger.yaml"
    ledger = read_yaml(ledger_path)
    ledger["entries"][0]["conditions"][0]["match_status"] = "unmatched"
    write_yaml(ledger_path, ledger)

    graph = assemble_subgoal_graph(run_dir)
    target_id = ledger["entries"][0]["repair_subgoal_id"]
    node = next(n for n in graph["nodes"] if n["id"] == target_id)
    assert node["state"] == "blocked_missing_source"
    assert node["block_reason"] == "theorem_condition_gap"
    assert node["suggested_next_action"]
    assert not node.get("supported_by_source_steps")
    ProofGoalGraph.model_validate(graph)

    text = "\n".join(render_proof_search_lines(graph))
    # translated wording is shown, no raw enum strings leak
    assert "A cited theorem's hypotheses are not met" in text
    for raw in ("theorem_condition_gap", "unmatched", "needs_exact_locator", "block_reason"):
        assert raw not in text


def test_ledger_entries_prevent_open_blocked_collapse(tmp_path: Path) -> None:
    run_dir = _ledger_run(tmp_path)
    graph = assemble_subgoal_graph(run_dir)
    # named repair subgoals exist -> not a {open:1, blocked:0}-only collapse
    tcl_nodes = [n for n in graph["nodes"] if n["id"].startswith("subgoal_tcl_")]
    assert tcl_nodes
    assert graph["summary"]["open"] >= len(tcl_nodes)
    # every ledger repair node id is wired into the main goal's depends_on
    main = next(n for n in graph["nodes"] if n["role"] == "main_goal")
    for node in tcl_nodes:
        assert node["id"] in main["depends_on"]


def test_no_double_repair_node_from_blocker_and_ledger(tmp_path: Path) -> None:
    """The ledger is the single writer of subgoal_tcl_* nodes; 57B's blocker path
    only writes subgoal_block_* ids, so the two never collide."""
    run_dir = _ledger_run(tmp_path)
    _inject_blocker(run_dir, "Need a source/theorem for the class equation")
    graph = assemble_subgoal_graph(run_dir)
    ids = [n["id"] for n in graph["nodes"]]
    assert len(ids) == len(set(ids))  # no duplicate node ids
    tcl = [i for i in ids if i.startswith("subgoal_tcl_")]
    block = [i for i in ids if i.startswith("subgoal_block_")]
    assert tcl and block
    assert set(tcl).isdisjoint(block)


# ---------------------------------------------------------------------------
# 56E (G56E-001): reviewer-named route/global gaps become blocked nodes
# ---------------------------------------------------------------------------
def _inject_scoped_objections(run_dir: Path, scoped: list[dict]) -> None:
    """Append scoped_objections to the gate-time llm_agent_run's first verifier
    review, mirroring them identically into the matching round_001 review so the
    run cross-checks (initial-round body == llm_agent_run body) still pass.
    Mirrors the ledger/source-request helper style."""
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    optional = manifest["optional_artifacts"]

    agent_path = run_dir / optional["llm_agent_run"]
    agent = read_yaml(agent_path)
    verifier_id = agent["verifier_reviews"][0]["verifier_id"]
    agent["verifier_reviews"][0].setdefault("scoped_objections", []).extend(
        copy.deepcopy(scoped)
    )
    write_yaml(agent_path, agent)

    index = read_yaml(run_dir / optional["prover_round_index"])
    round_one = next(r for r in index["rounds"] if r["round_index"] == 1)
    round_path = run_dir / round_one["artifact_path"]
    round_report = read_yaml(round_path)
    for review in round_report["verifier_reviews"]:
        if review["verifier_id"] == verifier_id:
            review.setdefault("scoped_objections", []).extend(copy.deepcopy(scoped))
    write_yaml(round_path, round_report)


def _route_objection(
    obj_id: str, kind: str, text: str, *, suggested_next_action: str = ""
) -> dict:
    return {
        "id": obj_id,
        "text": text,
        "scope": "route",
        "scope_source": "explicit",
        "scope_inferred": False,
        "inference_reason": "",
        "subgoal_id": None,
        "proof_step_id": None,
        "kind": kind,
        "severity": "P1",
        "suggested_next_action": suggested_next_action,
    }


def _global_objection(obj_id: str, kind: str, text: str) -> dict:
    return {**_route_objection(obj_id, kind, text), "scope": "global"}


def _subgoal_objection(obj_id: str, kind: str, text: str, subgoal_id: str) -> dict:
    return {
        "id": obj_id,
        "text": text,
        "scope": "subgoal",
        "scope_source": "explicit",
        "scope_inferred": False,
        "inference_reason": "",
        "subgoal_id": subgoal_id,
        "proof_step_id": None,
        "kind": kind,
        "severity": "P1",
        "suggested_next_action": "",
    }


def test_route_objections_become_blocked_nodes(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    # a real existing subgoal id to name with the subgoal-scoped objection
    pre = assemble_subgoal_graph(run_dir)
    real_id = next(n["id"] for n in pre["nodes"] if n["role"] != "main_goal")

    _inject_scoped_objections(
        run_dir,
        [
            _route_objection(
                "v1_route",
                "logical_gap",
                "The reduction skips a case where the order is a prime square.",
                suggested_next_action="Cover the missing case explicitly.",
            ),
            _route_objection(
                "v1_missing",
                "missing_source",
                "No cited source establishes the structure theorem invoked here.",
            ),
            _subgoal_objection(
                "v1_sub",
                "hypothesis_gap",
                "This subgoal omits the finiteness hypothesis.",
                real_id,
            ),
        ],
    )

    graph = assemble_subgoal_graph(run_dir)
    main_node = next(n for n in graph["nodes"] if n["role"] == "main_goal")
    obj_nodes = {n["id"]: n for n in graph["nodes"] if n["id"].startswith("subgoal_obj_")}
    # the injected route gaps each became a first-class blocked node
    missing = next(
        n for n in obj_nodes.values() if "structure theorem" in n["statement"]
    )
    logical = next(n for n in obj_nodes.values() if "reduction skips" in n["statement"])
    for node in (missing, logical):
        assert node["state"] == "blocked_reviewer_objection"
        assert node["reviewer_objections"]
        assert not node.get("supported_by_source_steps")
        assert not node.get("supported_by_computations")
        assert not node.get("supported_by_scoped_checks")
        assert node["id"] in main_node["depends_on"]

    assert missing["block_reason"] == "theorem_condition_gap"
    assert missing["role"] == "hypothesis_check"

    assert "block_reason" not in logical  # logical_gap is not a typed dead route
    assert logical["role"] == "lemma"

    # the subgoal-scoped objection still attaches to its node (54B) and does NOT
    # also create a subgoal_obj_* duplicate
    node_by_id = {n["id"]: n for n in graph["nodes"]}
    assert any(
        "finiteness hypothesis" in o
        for o in node_by_id[real_id].get("reviewer_objections", [])
    )
    assert not any("finiteness hypothesis" in n["statement"] for n in obj_nodes.values())

    ProofGoalGraph.model_validate(graph)
    validate_run(run_dir)


def test_global_objection_does_not_become_a_blocked_node(tmp_path: Path) -> None:
    # A "global" concern is about the whole claim, not a named route-death step:
    # it stays advisory (54B / main goal) and never becomes its own subgoal_obj_*
    # node. Only route-scoped named gaps are materialized.
    run_dir = _resumed_run(tmp_path)
    _inject_scoped_objections(
        run_dir,
        [
            _global_objection(
                "v1_only_global",
                "missing_source",
                "A global concern that is not a route-death step.",
            )
        ],
    )
    graph = assemble_subgoal_graph(run_dir)
    obj_nodes = [n for n in graph["nodes"] if n["id"].startswith("subgoal_obj_")]
    # the global objection's text is never materialized as its own route-death node
    # (other route-scoped objections from the run may still legitimately appear)
    assert not any(
        "global concern that is not a route-death step" in n["statement"] for n in obj_nodes
    )
    ProofGoalGraph.model_validate(graph)
    validate_run(run_dir)


def test_route_objections_prevent_open_blocked_collapse_and_count(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    before_summary = assemble_subgoal_graph(run_dir)["summary"]
    _inject_scoped_objections(
        run_dir,
        [
            _route_objection(
                "v1_route_unique",
                "logical_gap",
                "A brand-new route-death gap that needs its own node here.",
            )
        ],
    )
    after = assemble_subgoal_graph(run_dir)
    after_summary = after["summary"]
    # injecting one new route gap adds exactly one blocked node to the count
    assert after_summary["blocked"] == before_summary["blocked"] + 1
    # graph is not the {open:1, blocked:0} collapse
    assert not (after_summary["open"] == 1 and after_summary["blocked"] == 0)

    main = next(n for n in after["nodes"] if n["role"] == "main_goal")
    assert any(i.startswith("subgoal_obj_") for i in main["depends_on"])


def test_reviewer_named_node_build_is_idempotent_and_dedups_across_rounds(
    tmp_path: Path,
) -> None:
    run_dir = _resumed_run(tmp_path)
    same_text = "The same route-death gap reported in two rounds."
    # the identical objection lands on BOTH the gate review and round_001 review
    _inject_scoped_objections(
        run_dir, [_route_objection("v1_dup", "logical_gap", same_text)]
    )
    first = assemble_subgoal_graph(run_dir)
    obj_for_text = [
        n
        for n in first["nodes"]
        if n["id"].startswith("subgoal_obj_") and same_text in n["statement"]
    ]
    # dedup by text -> exactly one node despite the objection appearing twice
    # (once on the gate review and once on the mirrored round_001 review)
    assert len(obj_for_text) == 1

    build_subgoal_graph(run_dir)
    rebuilt = build_subgoal_graph(run_dir)
    obj_ids_1 = [n["id"] for n in first["nodes"] if n["id"].startswith("subgoal_obj_")]
    obj_ids_2 = [n["id"] for n in rebuilt["nodes"] if n["id"].startswith("subgoal_obj_")]
    assert obj_ids_1 == obj_ids_2  # same ids / count on rebuild
    ProofGoalGraph.model_validate(rebuilt)
    validate_run(run_dir)


def test_counterexample_pressure_objection_blocks_but_never_refutes(
    tmp_path: Path,
) -> None:
    run_dir = _resumed_run(tmp_path)
    cx_text = "A small example looks like it might break the route."
    _inject_scoped_objections(
        run_dir,
        [_route_objection("v1_cx", "counterexample_pressure", cx_text)],
    )
    graph = assemble_subgoal_graph(run_dir)
    obj = next(
        n
        for n in graph["nodes"]
        if n["id"].startswith("subgoal_obj_") and cx_text in n["statement"]
    )
    assert obj["state"] == "blocked_reviewer_objection"
    # unchecked pressure is not a typed dead route -> no block_reason
    assert "block_reason" not in obj
    # pressure never becomes a refutation: no contradicted node, claim not refuted
    assert obj["state"] != "contradicted"
    assert not obj["counterexample_pressure"]
    main = next(n for n in graph["nodes"] if n["role"] == "main_goal")
    assert main["state"] != "contradicted"
    ProofGoalGraph.model_validate(graph)
    validate_run(run_dir)


def test_reviewer_named_node_renders_translated_wording_no_raw_enum(
    tmp_path: Path,
) -> None:
    run_dir = _resumed_run(tmp_path)
    _inject_scoped_objections(
        run_dir,
        [
            _route_objection(
                "v1_g",
                "missing_source",
                "No cited source backs the invoked structure result.",
            )
        ],
    )
    graph = assemble_subgoal_graph(run_dir)
    blob = "\n".join(render_proof_search_lines(graph))
    assert "Reviewer-named gap" in blob
    assert "Blocked by a reviewer objection" in blob
    assert "A cited theorem's hypotheses are not met" in blob
    # no raw enum / field names leak to the user-facing surface
    for raw in ("theorem_condition_gap", "block_reason", "blocked_reviewer_objection"):
        assert raw not in blob
