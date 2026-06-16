from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.orchestrator import orchestrate_run
from espresso_proof.time_allocation import (
    TIME_ALLOCATION_FILENAME,
    build_time_allocation_plan,
    write_time_allocation_plan,
)
from espresso_proof.update_inbox import append_update_inbox_event, load_update_inbox
from espresso_proof.surface_warnings import pending_update_inbox_ids, refresh_final_report_disclosures
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validators import validate_run

CLAIM = "Every group of order p^2, where p is prime, is abelian."


def _write_claim(tmp_path: Path, text: str = CLAIM) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(text, encoding="utf-8")
    return path


def _fast_run(tmp_path: Path, name: str = "time_allocation") -> Path:
    result = orchestrate_run(
        project=name,
        input_path=_write_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    run_dir = Path(result["run_dir"])
    validate_run(run_dir)
    return run_dir


def _plan(run_dir: Path) -> dict:
    return read_yaml(run_dir / TIME_ALLOCATION_FILENAME)


def _drop_time_allocation(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.get("optional_artifacts", {}).pop("time_allocation", None)
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    path = run_dir / TIME_ALLOCATION_FILENAME
    if path.exists():
        path.unlink()


def _mark_first_route_source_backed(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief_path = run_dir / manifest["artifacts"]["strategy_brief"]
    brief = read_yaml(brief_path)
    brief["top_strategies"][0]["source_status"] = "source_backed"
    write_yaml(brief_path, brief)
    synthesis_path = run_dir / manifest["artifacts"]["strategy_synthesis"]
    synthesis = read_yaml(synthesis_path)
    synthesis["routes"][0]["source_status"] = "source_backed"
    write_yaml(synthesis_path, synthesis)


def _claim_and_manifest(run_dir: Path) -> tuple[dict, dict]:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    return claim, manifest


def _first_route_id(run_dir: Path) -> str:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(run_dir / manifest["artifacts"]["strategy_brief"])
    return str(brief["top_strategies"][0]["id"])


def _reviewer_objection_event(run_dir: Path, *, route_id: str | None = None, **overrides) -> dict:
    claim, manifest = _claim_and_manifest(run_dir)
    route = route_id if route_id is not None else _first_route_id(run_dir)
    data = {
        "id": "upd_reviewer_001",
        "type": "reviewer_objection",
        "origin_stage": "prover_loop_round",
        "origin_artifact": "prover_rounds/round_001.yaml",
        "origin_artifact_id_or_path": "reviewer:objections:001",
        "locked_claim_id": claim["id"],
        "route_id_or_step_id": route,
        "artifact_revision_or_hash": "hash_review_001",
        "validator_ok": True,
        "created_at": "2026-06-15T00:00:00+00:00",
        "dedupe_key": f"{manifest['run_id']}:reviewer_objection:{route}",
        "applied_at": None,
        "effect": "scheduling_only",
        "advisory_only": True,
    }
    data.update(overrides)
    return data


def _source_gap_event(run_dir: Path, *, route_id: str | None = None, **overrides) -> dict:
    claim, manifest = _claim_and_manifest(run_dir)
    route = route_id if route_id is not None else _first_route_id(run_dir)
    data = {
        "id": "upd_source_gap_001",
        "type": "source_gap",
        "origin_stage": "source_theorem",
        "origin_artifact": "source_grounding.yaml",
        "origin_artifact_id_or_path": "step_001",
        "locked_claim_id": claim["id"],
        "route_id_or_step_id": route,
        "artifact_revision_or_hash": "hash_source_gap_001",
        "validator_ok": True,
        "created_at": "2026-06-15T00:00:00+00:00",
        "dedupe_key": f"{manifest['run_id']}:source_gap:{route}",
        "applied_at": None,
        "effect": "scheduling_only",
        "advisory_only": True,
    }
    data.update(overrides)
    return data


def test_strategy_generator_output_alone_does_not_start_allocation(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    _drop_time_allocation(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    write_yaml(
        run_dir / manifest["artifacts"]["strategy_synthesis"],
        {
            "claim_id": "claim_001",
            "synthesized_from": ["generator_only"],
            "routes": [{"id": "raw_route_001", "title": "Unscored route"}],
            "synthesis_note": "Generator-only output with no grader fields.",
        },
    )
    (run_dir / manifest["artifacts"]["strategy_brief"]).unlink()

    assert build_time_allocation_plan(run_dir) is None
    assert not (run_dir / TIME_ALLOCATION_FILENAME).exists()


def test_strategy_grader_output_starts_provisional_allocation(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    plan = _plan(run_dir)

    assert manifest["optional_artifacts"]["time_allocation"] == TIME_ALLOCATION_FILENAME
    assert plan["trigger"] == "strategy_grader"
    assert plan["allocation_state"] == "TIME_ALLOCATION_STARTED"
    assert plan["effect"] == "scheduling_only"
    assert plan["advisory_only"] is True
    assert plan["entries"]
    validate_run(run_dir)


def test_missing_strategy_brief_does_not_block_first_allocation_plan(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    _drop_time_allocation(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    (run_dir / manifest["artifacts"]["strategy_brief"]).unlink()

    plan = build_time_allocation_plan(run_dir)

    assert plan is not None
    assert plan.strategy_artifact == "strategy_synthesis"
    assert "strategy_brief" in plan.missing_context
    assert plan.entries


def test_missing_human_preference_does_not_block_first_allocation_plan(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    plan = _plan(run_dir)

    assert "human_preference" in plan["missing_context"]
    assert any(entry["action"] == "ask_human" for entry in plan["entries"])
    validate_run(run_dir)


def test_missing_source_and_pressure_context_are_recorded(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    plan = _plan(run_dir)

    assert "source_support" in plan["missing_context"]
    assert "examples_pressure" in plan["missing_context"]
    assert "counterexamples_pressure" in plan["missing_context"]
    assert any(entry["action"] == "source_pinning" for entry in plan["entries"])


def test_route_source_backed_label_still_requires_source_pinning(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    _drop_time_allocation(run_dir)
    _mark_first_route_source_backed(run_dir)

    plan = build_time_allocation_plan(run_dir)

    assert plan is not None
    assert "source_support" in plan.missing_context
    assert plan.entries[0].action == "source_pinning"


def test_unrelated_source_backed_step_does_not_suppress_missing_source_support(
    tmp_path: Path,
) -> None:
    run_dir = _fast_run(tmp_path)
    _drop_time_allocation(run_dir)
    _mark_first_route_source_backed(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    source_path = run_dir / manifest["artifacts"]["source_grounding"]
    source = read_yaml(source_path)
    source["source_refs"] = [
        {
            "id": "src_unrelated",
            "type": "paper",
            "title": "Unrelated source-backed step",
            "authors": [],
            "locator": {"kind": "theorem", "value": "Theorem U"},
            "statement_or_summary": "A source-backed theorem for a different proof step.",
            "hypothesis_match": {"matches": True, "mismatches": []},
            "confidence": "high",
            "status": "source_backed",
        }
    ]
    source["steps"] = [
        {
            "proof_step_id": "unrelated_source_step",
            "needed_theorem": "Unrelated step source support.",
            "status": "source_backed",
            "source_refs": ["src_unrelated"],
            "hypothesis_match": {"matches": True, "mismatches": []},
            "note": "This source-backed step is not bound to the strategy route.",
        }
    ]
    write_yaml(source_path, source)

    plan = build_time_allocation_plan(run_dir)

    assert plan is not None
    assert "source_support" in plan.missing_context
    assert plan.entries[0].action == "source_pinning"


def test_reviewer_objection_allocation_marks_consumed_inbox_entries(
    tmp_path: Path,
) -> None:
    run_dir = _fast_run(tmp_path)
    _drop_time_allocation(run_dir)
    route_id = _first_route_id(run_dir)
    append_update_inbox_event(run_dir, _reviewer_objection_event(run_dir, route_id=route_id))

    inbox_before = load_update_inbox(run_dir)
    assert inbox_before is not None
    assert pending_update_inbox_ids(inbox_before.model_dump(mode="json")) == [
        "upd_reviewer_001"
    ]

    plan = write_time_allocation_plan(run_dir)

    assert plan is not None
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["time_allocation"] == TIME_ALLOCATION_FILENAME
    assert "update_inbox" in manifest["dependencies"]["time_allocation"]["depends_on"]
    entry = next(entry for entry in plan.entries if entry.route_id == route_id)
    assert entry.action == "repair_attempt"
    inbox_after = load_update_inbox(run_dir)
    assert inbox_after is not None
    assert inbox_after.entries[0].applied_at is not None
    assert pending_update_inbox_ids(inbox_after.model_dump(mode="json")) == []
    validate_run(run_dir)


def test_reviewer_objection_allocation_preserves_existing_applied_at(
    tmp_path: Path,
) -> None:
    run_dir = _fast_run(tmp_path)
    _drop_time_allocation(run_dir)
    route_id = _first_route_id(run_dir)
    append_update_inbox_event(run_dir, _reviewer_objection_event(run_dir, route_id=route_id))
    inbox_path = run_dir / "update_inbox.yaml"
    raw = read_yaml(inbox_path)
    raw["entries"][0]["applied_at"] = "2026-06-15T02:00:00+00:00"
    write_yaml(inbox_path, raw)

    plan = write_time_allocation_plan(run_dir)

    assert plan is not None
    entry = next(entry for entry in plan.entries if entry.route_id == route_id)
    assert entry.action == "repair_attempt"
    inbox_after = load_update_inbox(run_dir)
    assert inbox_after is not None
    assert inbox_after.entries[0].applied_at == "2026-06-15T02:00:00+00:00"
    validate_run(run_dir)


def test_non_reviewer_inbox_entries_remain_pending_after_time_allocation(
    tmp_path: Path,
) -> None:
    run_dir = _fast_run(tmp_path)
    _drop_time_allocation(run_dir)
    append_update_inbox_event(run_dir, _source_gap_event(run_dir))

    plan = write_time_allocation_plan(run_dir)

    assert plan is not None
    inbox = load_update_inbox(run_dir)
    assert inbox is not None
    assert inbox.entries[0].type == "source_gap"
    assert inbox.entries[0].applied_at is None


def test_failed_time_allocation_write_does_not_mark_inbox_applied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import espresso_proof.time_allocation as time_allocation_mod

    run_dir = _fast_run(tmp_path)
    _drop_time_allocation(run_dir)
    append_update_inbox_event(run_dir, _reviewer_objection_event(run_dir))

    def fail_time_allocation_write(path, data):
        if Path(path).name == TIME_ALLOCATION_FILENAME:
            raise RuntimeError("simulated time_allocation write failure")
        return write_yaml(path, data)

    monkeypatch.setattr(time_allocation_mod, "write_yaml", fail_time_allocation_write)

    with pytest.raises(RuntimeError, match="simulated"):
        time_allocation_mod.write_time_allocation_plan(run_dir)

    inbox = load_update_inbox(run_dir)
    assert inbox is not None
    assert inbox.entries[0].applied_at is None


def test_failed_update_inbox_apply_rolls_back_time_allocation_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import espresso_proof.time_allocation as time_allocation_mod

    run_dir = _fast_run(tmp_path)
    append_update_inbox_event(run_dir, _reviewer_objection_event(run_dir))
    refresh_final_report_disclosures(run_dir)
    manifest_before = read_yaml(run_dir / "run_manifest.yaml")
    allocation_before = (run_dir / TIME_ALLOCATION_FILENAME).read_bytes()

    def fail_inbox_apply(run_dir: Path, entry_ids: set[str]) -> None:
        assert entry_ids == {"upd_reviewer_001"}
        raise RuntimeError("simulated update_inbox apply failure")

    monkeypatch.setattr(
        time_allocation_mod,
        "mark_update_inbox_entries_applied",
        fail_inbox_apply,
    )

    with pytest.raises(RuntimeError, match="simulated update_inbox apply failure"):
        time_allocation_mod.write_time_allocation_plan(run_dir)

    manifest_after = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest_after == manifest_before
    assert (run_dir / TIME_ALLOCATION_FILENAME).read_bytes() == allocation_before
    inbox = load_update_inbox(run_dir)
    assert inbox is not None
    assert inbox.entries[0].applied_at is None
    validate_run(run_dir)


@pytest.mark.parametrize("action,budget", [("source_pinning", "small"), ("cheap_check", "tiny")])
def test_scheduled_checks_cannot_write_source_or_checked_evidence_status(
    tmp_path: Path, action: str, budget: str
) -> None:
    run_dir = _fast_run(tmp_path)
    plan = _plan(run_dir)
    entry = next(entry for entry in plan["entries"] if entry.get("route_id"))
    entry["action"] = action
    entry["budget"] = budget
    entry["source_status"] = "source_backed"
    entry["evidence_status"] = "checked"
    write_yaml(run_dir / TIME_ALLOCATION_FILENAME, plan)

    with pytest.raises(KernelValidationError, match="schema validation failed"):
        validate_run(run_dir)


def test_ask_human_cannot_change_proof_evidence_or_source_status(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    plan = _plan(run_dir)
    ask_human = next(entry for entry in plan["entries"] if entry["action"] == "ask_human")
    ask_human["proof_status"] = "proved"
    ask_human["source_status"] = "source_backed"
    ask_human["evidence_status"] = "checked"
    write_yaml(run_dir / TIME_ALLOCATION_FILENAME, plan)

    with pytest.raises(KernelValidationError, match="schema validation failed"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "remove_path",
    [
        ("allocation_state",),
        ("advisory_only",),
        ("effect",),
        ("entries", 0, "unchecked"),
        ("entries", 0, "advisory_only"),
        ("entries", 0, "effect"),
    ],
)
def test_scheduling_only_markers_are_required(tmp_path: Path, remove_path: tuple) -> None:
    run_dir = _fast_run(tmp_path)
    plan = _plan(run_dir)
    target = plan
    for key in remove_path[:-1]:
        target = target[key]
    target.pop(remove_path[-1])
    write_yaml(run_dir / TIME_ALLOCATION_FILENAME, plan)

    with pytest.raises(KernelValidationError, match="schema validation failed"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "replacement",
    [
        None,
        [],
        ["proof", "source", "evidence"],
        ["proof", "source", "evidence", "theorem_applicability", "grade"],
    ],
)
def test_forbidden_status_effect_cannot_be_removed_or_weakened(
    tmp_path: Path,
    replacement: list[str] | None,
) -> None:
    run_dir = _fast_run(tmp_path)
    plan = _plan(run_dir)
    if replacement is None:
        plan.pop("forbidden_status_effect")
    else:
        plan["forbidden_status_effect"] = replacement
    write_yaml(run_dir / TIME_ALLOCATION_FILENAME, plan)

    with pytest.raises(KernelValidationError, match="forbidden_status_effect"):
        validate_run(run_dir)


def test_high_llm_score_or_proof_language_in_allocation_cannot_prove_route(
    tmp_path: Path,
) -> None:
    run_dir = _fast_run(tmp_path)
    plan = _plan(run_dir)
    plan["entries"][0]["reason"] = "LLM score 99 says proved and verified."
    write_yaml(run_dir / TIME_ALLOCATION_FILENAME, plan)

    with pytest.raises(KernelValidationError, match="time_allocation cannot write"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "phrase",
    [
        "proof-close",
        "complete proof",
        "this establishes the theorem",
        "source_backed",
        "checked_evidence",
        "theorem_applicability_status",
        "refutation_status",
        "settled",
        "verified by examples",
    ],
)
def test_forbidden_proof_language_in_allocation_reason_fails_validation(
    tmp_path: Path, phrase: str
) -> None:
    run_dir = _fast_run(tmp_path)
    plan = _plan(run_dir)
    plan["entries"][0]["reason"] = f"Unsafe allocation note: {phrase}."
    write_yaml(run_dir / TIME_ALLOCATION_FILENAME, plan)

    with pytest.raises(KernelValidationError, match="time_allocation cannot write"):
        validate_run(run_dir)


def test_deep_budget_cannot_override_validator_blocker(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    plan = _plan(run_dir)
    entry = next(entry for entry in plan["entries"] if entry.get("route_id"))
    entry["action"] = "bounded_proof_exploration"
    entry["budget"] = "bounded"
    write_yaml(run_dir / TIME_ALLOCATION_FILENAME, plan)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    grade_path = run_dir / manifest["artifacts"]["idea_grade"]
    grade = read_yaml(grade_path)
    grade["score"] = 100
    write_yaml(grade_path, grade)

    with pytest.raises(KernelValidationError, match="Grade 10"):
        validate_run(run_dir)
