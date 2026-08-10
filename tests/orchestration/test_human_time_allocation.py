"""Human feedback drives deterministic, advisory time allocation.

These tests pin the contract: a human's strategy score/priority/note deterministically
re-orders routes, sets the next-round allocation mode, and reaches prompt context —
while NEVER upgrading proof/source/evidence/grade/claim status. The derived packet is
an advisory sidecar the kernel never reads as evidence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.strategy.human_time_allocation import (
    CONTEXT_FILE,
    PRIORITY_BONUS,
    RECENT_FEEDBACK_BONUS,
    SELECTED_ROUTE_BONUS,
    advisory_scheduling_score,
    build_time_allocation_context,
    load_time_allocation_context,
    recommended_allocation_for_priority,
    time_allocation_prompt_context,
    write_time_allocation_context,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import HumanTimeAllocationContext
from espresso_proof.orchestration.orchestrator import orchestrate_run, resume_run
from espresso_proof.strategy.strategy_feedback import StrategyFeedback, append_feedback
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")
CLEAR_CLAIM = "Every group of order p^2, where p is prime, is abelian.\n"


def _clear_claim_input(tmp_path: Path) -> Path:
    path = tmp_path / "clear_claim.md"
    path.write_text(CLEAR_CLAIM, encoding="utf-8")
    return path


def _add_second_strategy_route(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief_path = run_dir / manifest["artifacts"]["strategy_brief"]
    synthesis_path = run_dir / manifest["artifacts"]["strategy_synthesis"]
    brief = read_yaml(brief_path)
    synthesis = read_yaml(synthesis_path)
    second = dict(brief["top_strategies"][0])
    second.update(
        {
            "id": "strategy_secondary_002",
            "title": "Secondary bounded check route",
            "score": max(0, int(second.get("score", 50)) - 5),
            "next_loop_plan": "Keep as backup route for allocation ordering tests.",
        }
    )
    brief["top_strategies"].append(second)
    brief["allowed_human_choices"].append(
        {"strategy_id": second["id"], "effort_options": [second["estimated_effort"]]}
    )
    synthesis["routes"].append(second)
    write_yaml(brief_path, brief)
    write_yaml(synthesis_path, synthesis)
    validate_run(run_dir)


# -- Deterministic scheduling score (exact formula) -------------------


def test_score_weights_match_spec() -> None:
    assert PRIORITY_BONUS == {"focus": 30, "repair": 20, "normal": 0, "reduce": -40}
    assert SELECTED_ROUTE_BONUS == 25
    assert RECENT_FEEDBACK_BONUS == 10


def test_score_focus_selected_recent_clamps_to_100() -> None:
    # 50 + 30 + 25 + 10 = 115 -> clamp 100
    assert advisory_scheduling_score(base=50, priority="focus", is_selected=True, is_recent=True) == 100


def test_score_reduce_subtracts_and_clamps_floor() -> None:
    assert advisory_scheduling_score(base=50, priority="reduce", is_selected=False, is_recent=False) == 10
    assert advisory_scheduling_score(base=10, priority="reduce", is_selected=False, is_recent=False) == 0


def test_score_components_add_independently() -> None:
    assert advisory_scheduling_score(base=50, priority="normal", is_selected=False, is_recent=False) == 50
    assert advisory_scheduling_score(base=50, priority="repair", is_selected=True, is_recent=False) == 95
    assert advisory_scheduling_score(base=40, priority="focus", is_selected=False, is_recent=True) == 80


# -- allocation mapping --------------------------------------------------------


def test_allocation_focus_and_normal_expand() -> None:
    assert recommended_allocation_for_priority("focus", has_blockers=False, only_valid_route=False) == "expand"
    assert recommended_allocation_for_priority("normal", has_blockers=False, only_valid_route=False) == "expand"


def test_allocation_repair_priority_repairs() -> None:
    assert recommended_allocation_for_priority("repair", has_blockers=False, only_valid_route=False) == "repair"


def test_allocation_reduce_is_cheap_check() -> None:
    assert recommended_allocation_for_priority("reduce", has_blockers=False, only_valid_route=False) == "cheap_check"


def test_reduce_cannot_hide_only_valid_route() -> None:
    # A 'reduce' must never starve the only valid route.
    assert recommended_allocation_for_priority("reduce", has_blockers=False, only_valid_route=True) == "expand"


def test_blockers_always_force_repair() -> None:
    # Reviewer objections outrank taste, for every priority.
    for priority in ("focus", "normal", "reduce", "repair"):
        assert recommended_allocation_for_priority(priority, has_blockers=True, only_valid_route=False) == "repair"


# -- model: advisory-only + selected-first invariant ---------------------------


def test_packet_rejects_status_fields() -> None:
    # extra='forbid' means a smuggled status field is rejected outright.
    with pytest.raises(Exception):
        HumanTimeAllocationContext.model_validate(
            {"selected_strategy": "s1", "routes": [], "source_backed": True}
        )


def test_packet_requires_selected_route_first() -> None:
    routes = [
        {"strategy_id": "a", "base_score": 50, "advisory_score": 50, "is_selected": False},
        {"strategy_id": "b", "base_score": 50, "advisory_score": 90, "is_selected": True},
    ]
    with pytest.raises(ValueError, match="selected route must rank first"):
        HumanTimeAllocationContext.model_validate({"routes": routes})


def test_packet_advisory_only_locked_true() -> None:
    ctx = HumanTimeAllocationContext.model_validate({"routes": []})
    assert ctx.advisory_only is True
    assert ctx.forbidden_status_effect == ["proof", "source", "evidence", "grade", "claim"]


# -- integration: a real run with human feedback -------------------------------


def _run_with_feedback(tmp_path: Path, *, priority: str, score: int = 90, strategy_id_override: str | None = None):
    result = orchestrate_run(
        project="g54c",
        input_path=_clear_claim_input(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    _add_second_strategy_route(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(run_dir / manifest["artifacts"]["strategy_brief"])
    selected = brief["top_strategies"][0]["id"]
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    append_feedback(
        run_dir,
        StrategyFeedback(
            feedback_id="fb_001",
            run_id=run_dir.name,
            claim_id=str(claim.get("id")),
            strategy_id=strategy_id_override or selected,
            score=score,
            priority=priority,
            note="Please focus the search here.",
            created_at="2026-06-13T00:00:00+00:00",
        ),
    )
    resume_run(
        run_dir=run_dir,
        selected_strategy=selected,
        effort="normal_pass",
        prover_provider="stub",
    )
    return run_dir, selected


def test_context_ranks_selected_first_with_correct_score(tmp_path: Path) -> None:
    run_dir, selected = _run_with_feedback(tmp_path, priority="focus", score=80)
    context = build_time_allocation_context(run_dir)
    assert context.routes, "expected at least one candidate route"
    assert context.routes[0].is_selected, "human-selected route must rank first"
    assert context.routes[0].strategy_id == selected
    selected_route = context.routes[0]
    # focus(+30) + selected(+25) + recent(+10) on top of the brief base score.
    base = selected_route.base_score
    assert selected_route.advisory_score == min(100, base + 30 + 25 + 10)
    assert selected_route.recommended_allocation in {"expand", "repair", "cheap_check"}


def test_sidecar_written_validates_and_is_not_a_kernel_artifact(tmp_path: Path) -> None:
    run_dir, _ = _run_with_feedback(tmp_path, priority="focus")
    # the continuation wrote the sidecar; it loads + validates...
    persisted = load_time_allocation_context(run_dir)
    assert persisted is not None and persisted.advisory_only is True
    # ...but it is NOT a kernel artifact (validate_run never reads it as evidence).
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert CONTEXT_FILE not in (manifest.get("artifacts") or {}).values()
    assert CONTEXT_FILE not in (manifest.get("optional_artifacts") or {}).values()
    validate_run(run_dir)  # still green with the advisory sidecar present


def test_appended_attempt_allocation_reflects_priority_deterministically(tmp_path: Path) -> None:
    run_dir, selected = _run_with_feedback(tmp_path, priority="reduce")
    loop = read_yaml(run_dir / "prover_loop.yaml")
    context = build_time_allocation_context(run_dir)
    only_valid = len(context.routes) == 1
    appended = [a for a in loop["attempts"] if a["round_index"] >= 2]
    assert appended, "stub loop should append at least one continuation round"
    for attempt in appended:
        expected = recommended_allocation_for_priority(
            "reduce" if attempt["strategy_id"] == selected else "normal",
            has_blockers=bool(attempt.get("blockers")),
            only_valid_route=only_valid,
        )
        assert attempt["time_allocation"] == expected, (
            f"attempt {attempt['attempt_id']} allocation {attempt['time_allocation']} "
            f"!= deterministic {expected}"
        )


def test_clean_continuation_reduce_route_gets_cheap_check(tmp_path: Path, monkeypatch) -> None:
    # When a continuation round has NO reviewer blockers, the selected route's
    # human priority drives the allocation: 'reduce' -> 'cheap_check' (not a
    # hardcoded 'expand'). Keep round 1 (the gate) with its real
    # blockers so the loop continues, then force the CONTINUATION review clean.
    import espresso_proof.agents.llm_agents as la

    result = orchestrate_run(
        project="g54c_clean",
        input_path=_clear_claim_input(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    _add_second_strategy_route(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(run_dir / manifest["artifacts"]["strategy_brief"])
    selected = brief["top_strategies"][0]["id"]
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    append_feedback(
        run_dir,
        StrategyFeedback(
            feedback_id="fb_r", run_id=run_dir.name, claim_id=str(claim.get("id")),
            strategy_id=selected, score=20, priority="reduce", created_at="2026-06-13T00:00:00+00:00",
        ),
    )

    # Monkeypatch AFTER the gate round so only continuation reviews are cleaned.
    real_make = la.make_verifier_review

    def clean_review(*args, **kwargs):
        review = real_make(*args, **kwargs)
        review.update(
            objections=[], missing_hypotheses=[], source_requests=[],
            verdict="plausible_direction", scoped_objections=[],
        )
        return review

    monkeypatch.setattr(la, "make_verifier_review", clean_review)
    resume_run(run_dir=run_dir, selected_strategy=selected, effort="normal_pass", prover_provider="stub")

    loop = read_yaml(run_dir / "prover_loop.yaml")
    appended = [
        a for a in loop["attempts"] if a["round_index"] >= 2 and a["strategy_id"] == selected
    ]
    assert appended, "expected a clean continuation round on the selected route"
    for attempt in appended:
        assert not attempt.get("blockers"), "continuation review was forced clean"
        assert attempt["time_allocation"] == "cheap_check"
    validate_run(run_dir)


def test_feedback_reaches_next_prompt_context(tmp_path: Path) -> None:
    run_dir, selected = _run_with_feedback(tmp_path, priority="focus", score=88)
    prompt_context = time_allocation_prompt_context(run_dir, selected)
    assert prompt_context, "feedback summary should be non-empty"
    assert "ADVISORY" in prompt_context
    assert "focus" in prompt_context.lower() or "FOCUS" in prompt_context
    assert "Advisory steering" in prompt_context  # the derived allocation hint


def test_unknown_strategy_feedback_is_ignored_fail_closed(tmp_path: Path) -> None:
    # Feedback that names a route not in the brief never creates a route.
    run_dir, selected = _run_with_feedback(
        tmp_path, priority="focus", strategy_id_override="strategy_does_not_exist_999"
    )
    context = build_time_allocation_context(run_dir)
    ids = {r.strategy_id for r in context.routes}
    assert "strategy_does_not_exist_999" not in ids
    # the selected route stays at normal priority (no real feedback bound to it).
    selected_route = next(r for r in context.routes if r.strategy_id == selected)
    assert selected_route.priority == "normal"


def test_repeated_feedback_does_not_duplicate_routes(tmp_path: Path) -> None:
    run_dir, selected = _run_with_feedback(tmp_path, priority="focus")
    # add a second, newer feedback item for the same route...
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    append_feedback(
        run_dir,
        StrategyFeedback(
            feedback_id="fb_002", run_id=run_dir.name, claim_id=str(claim.get("id")),
            strategy_id=selected, score=20, priority="reduce", created_at="2026-06-13T01:00:00+00:00",
        ),
    )
    context = build_time_allocation_context(run_dir)
    ids = [r.strategy_id for r in context.routes]
    assert len(ids) == len(set(ids)), "routes must be unique even with repeated feedback"
    # the NEWEST item wins for the route's priority.
    selected_route = next(r for r in context.routes if r.strategy_id == selected)
    assert selected_route.priority == "reduce"


def test_proof_language_note_never_reaches_dashboard(tmp_path: Path) -> None:
    # A note containing proof-complete language cannot reach user-visible output
    # (the advisory packet carries scores/priority only — no free-text note), and
    # nothing it contains upgrades any status.
    from espresso_proof.orchestration.run_dashboard import render_run_dashboard

    result = orchestrate_run(
        project="g54c_scrub",
        input_path=_clear_claim_input(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(run_dir / manifest["artifacts"]["strategy_brief"])
    selected = brief["top_strategies"][0]["id"]
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    append_feedback(
        run_dir,
        StrategyFeedback(
            feedback_id="fb_x", run_id=run_dir.name, claim_id=str(claim.get("id")),
            strategy_id=selected, score=99, priority="focus",
            note="This proves the theorem; proof-close, fully source_backed.",
            created_at="2026-06-13T00:00:00+00:00",
        ),
    )
    resume_run(run_dir=run_dir, selected_strategy=selected, effort="normal_pass", prover_provider="stub")
    html = render_run_dashboard(run_dir)
    assert "proof-close" not in html
    assert "proves the theorem" not in html
    validate_run(run_dir)  # the note upgraded nothing
