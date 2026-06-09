from __future__ import annotations

import copy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.models import ProofGoalGraph
from espresso_proof.orchestrator import orchestrate_run, resume_run
from espresso_proof.subgoal_graph import assemble_subgoal_graph, build_subgoal_graph
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validators import validate_run


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
    resume_run(run_dir=run_dir, selected_strategy=selected, effort="quick_test")
    return run_dir


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
    resume_run(run_dir=run_dir, selected_strategy=selected, effort="quick_test")
    assert (run_dir / "proof_goal_graph.yaml").exists()
    before = (run_dir / "proof_goal_graph.yaml").read_bytes()
    # a second resume is a no-op (run already completed) and leaves the graph stable
    resume_run(run_dir=run_dir, selected_strategy=selected, effort="quick_test")
    after = (run_dir / "proof_goal_graph.yaml").read_bytes()
    assert before == after
    validate_run(run_dir)


def test_no_route_selected_builds_single_open_main_goal(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    graph = build_subgoal_graph(run_dir)
    validate_run(run_dir)
    assert len(graph["nodes"]) == 1
    main = graph["nodes"][0]
    assert main["role"] == "main_goal"
    assert main["state"] == "open"
    assert "route" in main["remaining_risk"].lower()


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
    from espresso_proof.run_dashboard import render_run_dashboard

    run_dir = _resumed_run(tmp_path)
    html = render_run_dashboard(run_dir)
    assert "Proof State" in html
    assert "Main goal" in html
    assert "Next subgoal to attack" in html
    # human wording, not raw internal enum names
    for raw in (
        "blocked_missing_source",
        "blocked_reviewer_objection",
        "scoped_checked",
        "source_supported",
        "main_goal",
    ):
        assert raw not in html


def test_proof_state_section_has_no_proof_complete_language(tmp_path: Path) -> None:
    from espresso_proof.run_dashboard import render_run_dashboard

    run_dir = _resumed_run(tmp_path)
    html = render_run_dashboard(run_dir)
    start = html.index("<h2>Proof State</h2>")
    section = html[start : html.index("</section>", start)].lower()
    for token in ("proof complete", "proof-complete", "proof_complete", "proof-close", "proved", "proven", "verified"):
        assert token not in section


def test_dashboard_renders_without_graph(tmp_path: Path) -> None:
    from espresso_proof.run_dashboard import render_run_dashboard

    run_dir = _gate_run(tmp_path)  # no proof_goal_graph.yaml at the gate
    assert not (run_dir / "proof_goal_graph.yaml").exists()
    html = render_run_dashboard(run_dir)
    assert "Espresso Proof" in html
    assert "Proof State" not in html


def test_dashboard_render_is_read_only_for_yaml(tmp_path: Path) -> None:
    from espresso_proof.run_dashboard import render_run_dashboard

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
    assert graph_before["summary"]["finite_or_computation_checked"] == 0
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


def test_resume_completed_memo_refreshes_stale_graph(tmp_path: Path) -> None:
    # Regression: a re-resume on an already-COMPLETED_MEMO run (e.g. with a pre-loop
    # --auto-wire-verifications / --audit-evidence step) appends scoped evidence
    # before the idempotent early return. That early return must refresh the
    # proof-search graph so it does not stay stale relative to proof_verification.
    from espresso_proof.orchestrator import resume_run
    from espresso_proof.proof_verification import verify_finite_groups

    run_dir = _resumed_run(tmp_path)  # COMPLETED_MEMO; graph built
    sid = read_yaml(run_dir / "strategy_brief.yaml")["top_strategies"][0]["id"]
    assert _graph(run_dir)["summary"]["finite_or_computation_checked"] == 0

    # Append scoped evidence out-of-band (library writer; no derived-view refresh).
    verify_finite_groups(run_dir, primes=[2])
    assert _graph(run_dir)["summary"]["finite_or_computation_checked"] == 0  # stale

    # Re-resume: the COMPLETED_MEMO early return must refresh the graph.
    resume_run(run_dir=run_dir, selected_strategy=sid, effort="quick_test")
    confirmed = [
        e
        for e in read_yaml(run_dir / "proof_verification.yaml")["entries"]
        if e["earned_status"] == "instance_checked"
    ]
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
    for node in graph["nodes"]:
        assert node["state"] not in _EVIDENCE_STATES
        assert node["state"] != "contradicted"


def test_memory_does_not_close_a_subgoal(tmp_path: Path) -> None:
    # memory_context is built during resume; the subgoal graph never reads it as
    # evidence, so no node may be closed/supported because of memory.
    run_dir = _resumed_run(tmp_path)
    assert (run_dir / "memory_context.yaml").exists()
    graph = assemble_subgoal_graph(run_dir)
    for node in graph["nodes"]:
        assert node["state"] not in _EVIDENCE_STATES


def test_finite_examples_do_not_close_a_universal_subgoal(tmp_path: Path) -> None:
    # The run has example/computation checks in computations.yaml, but only a
    # deterministic verifier (computation_verification / proof_verification) can
    # yield a scoped_checked node -- and never the universal main goal.
    run_dir = _resumed_run(tmp_path)
    computations = read_yaml(run_dir / "computation_checks.yaml")
    assert computations  # example/computation checks exist for this run
    graph = assemble_subgoal_graph(run_dir)
    assert not any(node["state"] == "scoped_checked" for node in graph["nodes"])
    main = next(n for n in graph["nodes"] if n["role"] == "main_goal")
    assert main["state"] == "open"
    assert not main.get("supported_by_scoped_checks")
    assert not main.get("supported_by_computations")


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
    from espresso_proof.subgoal_graph import render_proof_search_lines

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
    assert "Next subgoal to attack:" in text
    assert "proof-search map, not a proof" in text
    # human wording only — no raw internal enum names leak into the memo text
    for raw in ("blocked_missing_source", "main_goal", "scoped_checked", "source_supported"):
        assert raw not in text
    # never proof-complete language
    for token in ("proof complete", "proof-close", "proved", "proven", "verified"):
        assert token not in text.lower()


def test_render_proof_search_lines_empty_graph_returns_nothing() -> None:
    from espresso_proof.subgoal_graph import render_proof_search_lines

    assert render_proof_search_lines({}) == []
    assert render_proof_search_lines({"nodes": []}) == []
    assert render_proof_search_lines(None) == []


def test_render_proof_search_lines_truncates_long_statements() -> None:
    from espresso_proof.subgoal_graph import render_proof_search_lines

    graph = {"nodes": [{"role": "main_goal", "statement": "x" * 500, "state": "open"}]}
    line = next(l for l in render_proof_search_lines(graph) if l.startswith("  Main goal"))
    assert line.endswith("…")
    assert len(line) < 260  # capped, not the full 500-char blob


def test_memo_includes_proof_search_section_when_graph_present(tmp_path: Path) -> None:
    from espresso_proof.memo import build_concise_memo

    run_dir = _resumed_run(tmp_path)  # resume auto-builds the subgoal graph
    assert (run_dir / "proof_goal_graph.yaml").exists()
    memo = build_concise_memo(run_dir)
    assert "Proof-search state (a map of the search, not a proof):" in memo
    assert "Next subgoal to attack:" in memo
    # no proof-complete language anywhere in the memo
    for token in ("proof complete", "proof-complete", "proof-close", "proved", "proven", "verified theorem"):
        assert token not in memo.lower()


def test_memo_omits_proof_search_section_without_graph(tmp_path: Path) -> None:
    from espresso_proof.memo import build_concise_memo

    run_dir = _gate_run(tmp_path)  # at the gate: no proof_goal_graph.yaml yet
    assert not (run_dir / "proof_goal_graph.yaml").exists()
    memo = build_concise_memo(run_dir)
    assert "Proof-search state" not in memo


def test_memo_render_is_read_only_for_yaml(tmp_path: Path) -> None:
    from espresso_proof.memo import build_concise_memo

    run_dir = _resumed_run(tmp_path)
    before = {p.name: p.read_bytes() for p in run_dir.glob("*.yaml")}
    build_concise_memo(run_dir)
    after = {p.name: p.read_bytes() for p in run_dir.glob("*.yaml")}
    assert before == after


def test_dashboard_and_memo_share_one_wording_source() -> None:
    # The dashboard must render the proof-search state from the same wording
    # source as the memo (no second enum->wording map to drift out of sync).
    from espresso_proof import run_dashboard, subgoal_graph

    assert run_dashboard._state_words is subgoal_graph.state_words
    assert run_dashboard._PROOF_SEARCH_NOTE is subgoal_graph.PROOF_SEARCH_NOTE
    assert run_dashboard._group_subgoals is subgoal_graph.group_subgoals


# ---------------------------------------------------------------------------
# Goal 15: surface proof_goal_graph.notes in memo + dashboard (display only)
# ---------------------------------------------------------------------------
def test_graph_notes_helper_filters_blank_and_strips() -> None:
    from espresso_proof.subgoal_graph import graph_notes

    assert graph_notes(None) == []
    assert graph_notes({}) == []
    assert graph_notes({"notes": []}) == []
    assert graph_notes({"notes": ["  ", ""]}) == []
    assert graph_notes({"notes": ["  keep me  ", "", "second"]}) == ["keep me", "second"]


def test_render_proof_search_lines_shows_notes_when_present() -> None:
    from espresso_proof.subgoal_graph import render_proof_search_lines

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
    from espresso_proof.subgoal_graph import render_proof_search_lines

    graph = {"nodes": [{"role": "main_goal", "statement": "Show the claim.", "state": "open"}]}
    text = "\n".join(render_proof_search_lines(graph))
    assert "About this proof-search view" not in text


def test_memo_shows_graph_notes_when_present(tmp_path: Path) -> None:
    # A gate-run graph (no route selected yet) records a human-readable note.
    from espresso_proof.memo import build_concise_memo

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
    from espresso_proof.run_dashboard import render_run_dashboard

    run_dir = _gate_run(tmp_path)
    build_subgoal_graph(run_dir)
    notes = read_yaml(run_dir / "proof_goal_graph.yaml").get("notes") or []
    assert notes
    html = render_run_dashboard(run_dir)
    assert "About this proof-search view" in html
    assert notes[0] in html


def test_dashboard_notes_are_html_escaped() -> None:
    from espresso_proof.run_dashboard import _proof_state_section

    graph = {
        "nodes": [{"role": "main_goal", "statement": "Show the claim.", "state": "open"}],
        "notes": ['<script>alert("x")</script> & more'],
    }
    section = _proof_state_section({"graph": graph})
    assert "&lt;script&gt;" in section
    assert "<script>" not in section
    assert "&amp; more" in section


def test_dashboard_omits_notes_block_when_absent() -> None:
    from espresso_proof.run_dashboard import _proof_state_section

    graph = {"nodes": [{"role": "main_goal", "statement": "Show the claim.", "state": "open"}]}
    section = _proof_state_section({"graph": graph})
    assert "sg-notes" not in section
    assert "About this proof-search view" not in section


def test_rendering_notes_does_not_mutate_graph_artifact(tmp_path: Path) -> None:
    from espresso_proof.memo import build_concise_memo
    from espresso_proof.run_dashboard import render_run_dashboard

    run_dir = _gate_run(tmp_path)
    build_subgoal_graph(run_dir)
    before = (run_dir / "proof_goal_graph.yaml").read_bytes()
    build_concise_memo(run_dir)
    render_run_dashboard(run_dir)
    after = (run_dir / "proof_goal_graph.yaml").read_bytes()
    assert before == after
