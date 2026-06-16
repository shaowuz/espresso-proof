"""Goal 54B: structured reviewer-to-subgoal objections.

A reviewer objection becomes repairable *state* bound to a named proof-search
target, not just prose. These tests pin the three layers and their kernel-safety
contract:

1. ``ScopedObjection`` model: structural fail-closed binding (an id attaches only
   to its matching scope; a subgoal/proof_step scope must name its target; an
   inferred scope must justify itself). This is the "bogus scoped id" guard.
2. ``_normalize_scoped_objections`` parser: explicit reviewer scopes are preserved
   (a subgoal scope naming no target is downgraded to ``route`` — never falsely
   specific); legacy free-text ``risks`` are bound to the round's attacked subgoal
   as honestly-marked ``inferred_target``, or fall back to ``route``.
3. ``_pointer_scope_from_review``: a prover-loop review pointer claims a subgoal
   only when every subgoal-scoped objection names the SAME one.
4. Graph attach-if-matches: an objection naming an existing node is echoed on it;
   one naming a stale/unknown id is dropped (no fabricated node); rebuilds are
   idempotent; a global objection touches nothing. An objection can only BLOCK or
   PRIORITIZE repair — it never closes a subgoal or upgrades any status.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.llm_agents import (
    _normalize_scoped_objections,
    _pointer_scope_from_review,
    _review_pointer,
)
from espresso_proof.models import LLMAgentVerifierReview, ProverLoopReviewPointer, ScopedObjection
from espresso_proof.orchestrator import orchestrate_run, resume_run
from espresso_proof.run_dashboard import _proof_state_section
from espresso_proof.subgoal_graph import (
    assemble_subgoal_graph,
    build_subgoal_graph,
    render_proof_search_lines,
)


def _claim(tmp_path: Path) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(
        "Let p be a prime. Every group G of order p^2 is abelian.\n",
        encoding="utf-8",
    )
    return path


def _scoped(**overrides) -> dict:
    base = {
        "id": "v1_obj_000",
        "text": "Step 3 cites no source for the vanishing of H^3.",
        "scope": "subgoal",
        "scope_source": "explicit",
        "scope_inferred": False,
        "inference_reason": "",
        "subgoal_id": "subgoal_main",
        "proof_step_id": None,
        "kind": "missing_source",
        "severity": "P1",
        "suggested_next_action": "Cite Voisin or add a finite check.",
    }
    base.update(overrides)
    return base


# -- ScopedObjection model: structural fail-closed binding --------------------


def test_well_formed_subgoal_objection_validates() -> None:
    obj = ScopedObjection.model_validate(_scoped())
    assert obj.scope == "subgoal"
    assert obj.subgoal_id == "subgoal_main"
    assert obj.scope_source == "explicit"


def test_subgoal_id_requires_subgoal_scope() -> None:
    with pytest.raises(ValueError, match="subgoal_id requires scope='subgoal'"):
        ScopedObjection.model_validate(_scoped(scope="route", subgoal_id="subgoal_main"))


def test_proof_step_id_requires_proof_step_scope() -> None:
    with pytest.raises(ValueError, match="proof_step_id requires scope='proof_step'"):
        ScopedObjection.model_validate(
            _scoped(scope="subgoal", subgoal_id="subgoal_main", proof_step_id="step_2")
        )


def test_subgoal_scope_requires_subgoal_id() -> None:
    with pytest.raises(ValueError, match="scope='subgoal' requires a subgoal_id"):
        ScopedObjection.model_validate(_scoped(scope="subgoal", subgoal_id=None))


def test_proof_step_scope_requires_proof_step_id() -> None:
    with pytest.raises(ValueError, match="scope='proof_step' requires a proof_step_id"):
        ScopedObjection.model_validate(
            _scoped(scope="proof_step", subgoal_id=None, proof_step_id=None)
        )


def test_inferred_scope_requires_reason() -> None:
    with pytest.raises(ValueError, match="inferred scope requires a non-empty inference_reason"):
        ScopedObjection.model_validate(
            _scoped(scope_inferred=True, inference_reason="", scope_source="inferred_target")
        )


def test_inferred_target_source_requires_scope_inferred() -> None:
    with pytest.raises(ValueError, match="scope_source='inferred_target' requires scope_inferred"):
        ScopedObjection.model_validate(
            _scoped(scope_source="inferred_target", scope_inferred=False)
        )


def test_verifier_review_accepts_scoped_objections_additively() -> None:
    # Legacy producers that omit the field stay valid (default empty list).
    review = LLMAgentVerifierReview.model_validate(
        {
            "verifier_id": "engineering_000",
            "provider": "stub",
            "model": "stub",
            "created_at": "2026-06-13T00:00:00+00:00",
            "verdict": "needs_revision",
            "recommended_human_action": "revise",
            "status": "candidate",
        }
    )
    assert review.scoped_objections == []


# -- parser: explicit scopes preserved, legacy free text bound honestly -------


def test_explicit_subgoal_objection_preserved() -> None:
    raw = {"scoped_objections": [_scoped()]}
    out = _normalize_scoped_objections(raw, verifier_id="v1", target_subgoal_id="subgoal_block_002")
    assert len(out) == 1
    assert out[0]["scope"] == "subgoal"
    assert out[0]["subgoal_id"] == "subgoal_main"
    assert out[0]["scope_source"] == "explicit"
    # parser output is itself a valid ScopedObjection.
    ScopedObjection.model_validate(out[0])


def test_explicit_subgoal_scope_without_id_downgraded_to_route() -> None:
    # A reviewer claiming a subgoal scope but naming no target must never be
    # recorded as falsely subgoal-specific — it falls back to route.
    raw = {"scoped_objections": [_scoped(scope="subgoal", subgoal_id=None)]}
    out = _normalize_scoped_objections(raw, verifier_id="v1", target_subgoal_id="subgoal_main")
    assert out[0]["scope"] == "route"
    assert out[0]["subgoal_id"] is None
    ScopedObjection.model_validate(out[0])


def test_explicit_proof_step_scope_without_id_downgraded_to_route() -> None:
    raw = {"scoped_objections": [_scoped(scope="proof_step", subgoal_id=None, proof_step_id=None)]}
    out = _normalize_scoped_objections(raw, verifier_id="v1", target_subgoal_id=None)
    assert out[0]["scope"] == "route"
    assert out[0]["proof_step_id"] is None
    ScopedObjection.model_validate(out[0])


def test_legacy_risks_with_target_inferred() -> None:
    raw = {"risks": ["No source for the Hodge conjecture step.", "Hypothesis on dimension unstated."]}
    out = _normalize_scoped_objections(raw, verifier_id="v2", target_subgoal_id="subgoal_block_002")
    assert len(out) == 2
    for obj in out:
        assert obj["scope"] == "subgoal"
        assert obj["subgoal_id"] == "subgoal_block_002"
        assert obj["scope_source"] == "inferred_target"
        assert obj["scope_inferred"] is True
        assert obj["inference_reason"].strip()  # honestly justified
        ScopedObjection.model_validate(obj)


def test_legacy_risks_without_target_fallback_route() -> None:
    raw = {"risks": ["No source for the Hodge conjecture step."]}
    out = _normalize_scoped_objections(raw, verifier_id="v3", target_subgoal_id=None)
    assert len(out) == 1
    assert out[0]["scope"] == "route"
    assert out[0]["scope_source"] == "legacy_fallback"
    assert out[0]["scope_inferred"] is False
    assert out[0]["subgoal_id"] is None
    ScopedObjection.model_validate(out[0])


def test_malformed_and_empty_entries_dropped() -> None:
    assert _normalize_scoped_objections({}, verifier_id="v", target_subgoal_id=None) == []
    assert _normalize_scoped_objections({"risks": ["", "   "]}, verifier_id="v", target_subgoal_id="s") == []
    # explicit list with blank/non-dict entries: blanks dropped, valid kept.
    raw = {"scoped_objections": [{"text": ""}, "not a dict", _scoped()]}
    out = _normalize_scoped_objections(raw, verifier_id="v", target_subgoal_id=None)
    assert len(out) == 1
    assert out[0]["text"].startswith("Step 3")


def test_unknown_kind_and_severity_coerced_to_defaults() -> None:
    raw = {"scoped_objections": [_scoped(kind="totally_made_up", severity="P9")]}
    out = _normalize_scoped_objections(raw, verifier_id="v", target_subgoal_id=None)
    assert out[0]["kind"] == "other"
    assert out[0]["severity"] == "P2"
    ScopedObjection.model_validate(out[0])


# -- prover-loop review pointer scope -----------------------------------------


def test_pointer_scope_single_subgoal() -> None:
    review = {
        "verifier_id": "engineering_000",
        "verdict": "needs_revision",
        "objections": ["a", "b"],
        "recommended_human_action": "revise",
        "scoped_objections": [
            _scoped(subgoal_id="subgoal_block_002"),
            _scoped(id="v1_obj_001", subgoal_id="subgoal_block_002"),
        ],
    }
    scope, sid = _pointer_scope_from_review(review)
    assert (scope, sid) == ("subgoal", "subgoal_block_002")
    pointer = _review_pointer(review)
    assert pointer["scope"] == "subgoal"
    assert pointer["subgoal_id"] == "subgoal_block_002"
    ProverLoopReviewPointer.model_validate(pointer)


def test_pointer_scope_mixed_targets_is_route() -> None:
    review = {
        "verifier_id": "v",
        "verdict": "needs_revision",
        "recommended_human_action": "revise",
        "scoped_objections": [
            _scoped(subgoal_id="subgoal_block_002"),
            _scoped(id="x", subgoal_id="subgoal_block_003"),
        ],
    }
    assert _pointer_scope_from_review(review) == ("route", None)
    ProverLoopReviewPointer.model_validate(_review_pointer(review))


def test_pointer_scope_no_subgoal_objections_is_route() -> None:
    review = {
        "verifier_id": "v",
        "verdict": "plausible_direction",
        "recommended_human_action": "continue",
        "scoped_objections": [_scoped(scope="route", subgoal_id=None)],
    }
    assert _pointer_scope_from_review(review) == ("route", None)


# -- graph attach-if-matches + kernel safety ----------------------------------


def _resumed_run(tmp_path: Path) -> Path:
    result = orchestrate_run(
        project="g54b",
        input_path=_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
    )
    run_dir = Path(result["run_dir"])
    brief = read_yaml(
        run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["strategy_brief"]
    )
    resume_run(
        run_dir=run_dir,
        selected_strategy=brief["top_strategies"][0]["id"],
        effort="normal_pass",
        prover_provider="stub",
    )
    return run_dir


def _register_llm_agent_run(run_dir: Path, verifier_reviews: list[dict]) -> None:
    """Write a minimal llm_agent_run artifact carrying scoped objections and
    register it so ``assemble_subgoal_graph`` reads it (graph attach is a pure
    read of this file; it is never treated as evidence)."""
    write_yaml(run_dir / "llm_agent_run.yaml", {"verifier_reviews": verifier_reviews})
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["llm_agent_run"] = "llm_agent_run.yaml"
    write_yaml(manifest_path, manifest)


def test_objection_attaches_to_matching_stable_node(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _register_llm_agent_run(
        run_dir,
        [{"scoped_objections": [_scoped(subgoal_id="subgoal_main", text="Cite a source for step 3.")]}],
    )
    graph = assemble_subgoal_graph(run_dir)
    main = next(n for n in graph["nodes"] if n["id"] == "subgoal_main")
    assert any("Cite a source for step 3." in o for o in main["reviewer_objections"])
    assert any("[P1] missing_source" in o for o in main["reviewer_objections"])


def test_bogus_subgoal_id_creates_no_node_and_does_not_attach(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    before_ids = {n["id"] for n in assemble_subgoal_graph(run_dir)["nodes"]}
    _register_llm_agent_run(
        run_dir,
        [{"scoped_objections": [_scoped(subgoal_id="subgoal_invented_999", text="ghost objection")]}],
    )
    graph = assemble_subgoal_graph(run_dir)
    after_ids = {n["id"] for n in graph["nodes"]}
    assert after_ids == before_ids  # no fabricated node
    assert all("ghost objection" not in o for n in graph["nodes"] for o in n.get("reviewer_objections", []))


def test_rebuild_is_idempotent_no_duplicate_objections(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    # Two identical objections in the artifact must be deduped on attach...
    _register_llm_agent_run(
        run_dir,
        [
            {"scoped_objections": [_scoped(subgoal_id="subgoal_main", text="dup objection")]},
            {"scoped_objections": [_scoped(id="v2_obj_000", subgoal_id="subgoal_main", text="dup objection")]},
        ],
    )
    graph_a = build_subgoal_graph(run_dir)
    main_a = next(n for n in graph_a["nodes"] if n["id"] == "subgoal_main")
    count_a = sum(1 for o in main_a["reviewer_objections"] if "dup objection" in o)
    assert count_a == 1
    # ...and a rebuild leaves the objection set unchanged.
    graph_b = build_subgoal_graph(run_dir)
    main_b = next(n for n in graph_b["nodes"] if n["id"] == "subgoal_main")
    assert main_b["reviewer_objections"] == main_a["reviewer_objections"]


def test_global_objection_does_not_mutate_any_subgoal(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _register_llm_agent_run(
        run_dir,
        [{"scoped_objections": [
            {**_scoped(scope="global", subgoal_id=None, text="global concern"), "scope_source": "legacy_fallback"}
        ]}],
    )
    graph = assemble_subgoal_graph(run_dir)
    assert all("global concern" not in o for n in graph["nodes"] for o in n.get("reviewer_objections", []))


def test_objection_cannot_change_subgoal_state(tmp_path: Path) -> None:
    # Kernel safety: an objection can block/prioritize repair, never close a
    # subgoal or upgrade/flip any status. Node states derive from earned evidence
    # only, so attaching objections leaves the state vector identical.
    run_dir = _resumed_run(tmp_path)
    before = assemble_subgoal_graph(run_dir)
    states_before = {(n["id"], n["state"]) for n in before["nodes"]}
    _register_llm_agent_run(
        run_dir,
        [{"scoped_objections": [
            _scoped(subgoal_id="subgoal_main", kind="counterexample_pressure", severity="P0",
                    text="Possible counterexample in dimension 4."),
        ]}],
    )
    after = assemble_subgoal_graph(run_dir)
    states_after = {(n["id"], n["state"]) for n in after["nodes"]}
    assert states_after == states_before
    # counterexample_pressure is unchecked pressure, NEVER a refutation: no node
    # flips to a contradicted/refuted state because of it.
    assert all(n["state"] not in {"contradicted", "refuted"} for n in after["nodes"])


def test_attach_survives_absent_llm_agent_run(tmp_path: Path) -> None:
    # A refuted/early run has no llm_agent_run artifact; the attach loop must
    # not crash on the None optional artifact (regression: full-suite failure
    # on refuted-run graphs).
    run_dir = _resumed_run(tmp_path)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.get("optional_artifacts", {}).pop("llm_agent_run", None)
    write_yaml(manifest_path, manifest)
    graph = assemble_subgoal_graph(run_dir)  # must not raise
    assert graph["nodes"]


def test_attach_skips_non_dict_reviews(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _register_llm_agent_run(
        run_dir,
        ["not a dict", None, {"scoped_objections": [_scoped(subgoal_id="subgoal_main", text="real obj")]}],
    )
    graph = assemble_subgoal_graph(run_dir)  # must not raise on the junk entries
    main = next(n for n in graph["nodes"] if n["id"] == "subgoal_main")
    assert any("real obj" in o for o in main["reviewer_objections"])


def test_inferred_attachment_marked_and_next_action_surfaced(tmp_path: Path) -> None:
    # The reviewer's repairable instruction is shown, and an attribution that was
    # INFERRED from the round target is honestly marked (not presented as explicit).
    run_dir = _resumed_run(tmp_path)
    sid = _first_subgoal_id(run_dir)
    inferred = _scoped(
        subgoal_id=sid, scope_source="inferred_target", scope_inferred=True,
        inference_reason="bound to the round's attacked subgoal", text="hypothesis on dimension unstated",
        suggested_next_action="state the dimension hypothesis explicitly",
    )
    _register_llm_agent_run(run_dir, [{"scoped_objections": [inferred]}])
    graph = build_subgoal_graph(run_dir)
    node = next(n for n in graph["nodes"] if n["id"] == sid)
    tag = " ".join(node["reviewer_objections"])
    assert "→ next: state the dimension hypothesis explicitly" in tag
    assert "(inferred from round target)" in tag


def test_explicit_attachment_not_marked_inferred(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    sid = _first_subgoal_id(run_dir)
    _register_llm_agent_run(
        run_dir,
        [{"scoped_objections": [_scoped(subgoal_id=sid, text="explicit gap", suggested_next_action="")]}],
    )
    graph = build_subgoal_graph(run_dir)
    node = next(n for n in graph["nodes"] if n["id"] == sid)
    tag = " ".join(node["reviewer_objections"])
    assert "explicit gap" in tag
    assert "(inferred from round target)" not in tag  # explicit scope is not marked inferred
    assert "→ next:" not in tag  # no suggested action given


def test_dashboard_renders_main_goal_objections(tmp_path: Path) -> None:
    # Regression (FIX): the main goal's objections were dropped from the dashboard
    # while the memo showed them. A normal run's main node carries reviewer
    # objections derived from the review artifact, so the advisory block must show.
    run_dir = _resumed_run(tmp_path)
    graph = build_subgoal_graph(run_dir)
    main = next(n for n in graph["nodes"] if n["id"] == "subgoal_main")
    assert main["reviewer_objections"], "precondition: main node should carry review objections"
    # in a plain run only the main node carries objections, so the marker proves
    # the main-goal paragraph now renders them.
    html = _proof_state_section({"graph": graph})
    assert "Reviewer objections (advisory)" in html


def test_status_language_in_objection_is_scrubbed_on_attach(tmp_path: Path) -> None:
    # Free text embedded in the graph can never read as a finished proof.
    run_dir = _resumed_run(tmp_path)
    _register_llm_agent_run(
        run_dir,
        [{"scoped_objections": [
            _scoped(subgoal_id="subgoal_main", text="This already proved the theorem; proof-close."),
        ]}],
    )
    graph = assemble_subgoal_graph(run_dir)
    main = next(n for n in graph["nodes"] if n["id"] == "subgoal_main")
    attached = " ".join(main["reviewer_objections"])
    assert "proved" not in attached.lower()
    assert "proof-close" not in attached.lower()


# -- display: the objection shows up under its subgoal (memo + dashboard) ------


def _first_subgoal_id(run_dir: Path) -> str:
    """A non-main subgoal id from the built graph (these start with empty
    reviewer_objections, so an attached one renders within the display cap)."""
    graph = assemble_subgoal_graph(run_dir)
    return next(n["id"] for n in graph["nodes"] if n.get("role") != "main_goal")


def test_memo_renders_objection_under_its_subgoal(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    sid = _first_subgoal_id(run_dir)
    _register_llm_agent_run(
        run_dir,
        [{"scoped_objections": [_scoped(subgoal_id=sid, text="Source needed for step 3.")]}],
    )
    graph = build_subgoal_graph(run_dir)
    text = "\n".join(render_proof_search_lines(graph))
    assert "reviewer objection:" in text
    assert "Source needed for step 3." in text
    # advisory framing preserved — never proof-complete language.
    assert "not a proof" in text


def test_dashboard_renders_objection_under_its_subgoal(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    sid = _first_subgoal_id(run_dir)
    _register_llm_agent_run(
        run_dir,
        [{"scoped_objections": [_scoped(subgoal_id=sid, text="Source needed for step 3.")]}],
    )
    graph = build_subgoal_graph(run_dir)
    html = _proof_state_section({"graph": graph})
    assert "Reviewer objections (advisory)" in html
    assert "Source needed for step 3." in html
