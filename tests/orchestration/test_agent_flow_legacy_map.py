from __future__ import annotations

from pathlib import Path, PureWindowsPath

import pytest

from espresso_proof.front_half.registry import FRONT_HALF_STAGES
from espresso_proof.kernel.agent_flow_legacy_map import (
    IMPLEMENTATION_STATUSES,
    LEGACY_FRONT_HALF_FAST_ORDER,
    LEGACY_FRONT_HALF_FULL_ORDER,
    LEGACY_MAPPING,
    PHYSICAL_STAGE_NAMESPACES,
    RUN_MANIFEST_STATES,
    RUNTIME_MAPPING_KINDS,
    conceptual_stage_ids_for_front_half_plan,
    conceptual_stage_ids_for_front_half_stage,
    front_half_stages_in_legacy_map,
    get_legacy_mapping,
    legacy_artifact_paths,
    manifest_states_in_legacy_map,
    mapped_stage_ids,
    physical_stage_namespaces,
    physical_stages_in_legacy_map,
    shared_legacy_artifacts,
    shared_physical_stages,
    stage_ids_for_legacy_artifact,
    stage_ids_for_manifest_state,
    stage_ids_for_physical_stage,
    stage_ids_by_implementation_status,
    stage_ids_by_runtime_kind,
    stage_ids_blocked_by_future_work,
    stage_ids_with_pending_work,
    unmapped_stage_outputs,
    validate_agent_flow_legacy_map,
)
from espresso_proof.kernel.agent_flow_spec import get_stage, ordered_stage_ids
from espresso_proof.orchestration.orchestrator import FAST_CORE_STAGES


def test_legacy_map_is_structurally_valid() -> None:
    validate_agent_flow_legacy_map()
    assert mapped_stage_ids() == ordered_stage_ids()


def test_legacy_front_half_orders_match_current_runtime() -> None:
    assert LEGACY_FRONT_HALF_FULL_ORDER == tuple(FRONT_HALF_STAGES)
    assert LEGACY_FRONT_HALF_FAST_ORDER == tuple(FAST_CORE_STAGES)


def test_front_half_runtime_plans_map_to_expected_conceptual_stages() -> None:
    assert conceptual_stage_ids_for_front_half_plan(FRONT_HALF_STAGES) == (
        "understanding_claim",
        "literature_review",
        "examples",
        "counterexamples",
        "counterexample_admissibility",
        "source_theorem_candidate",
        "conservative_regrade",
        "strategy_generator",
        "examples_counterexamples_attacker",
        "strategy_grader",
        "strategy_brief",
        "grade",
        "concise_answer",
        "memory",
    )
    assert conceptual_stage_ids_for_front_half_plan(FAST_CORE_STAGES) == (
        "understanding_claim",
        "source_theorem_candidate",
        "conservative_regrade",
        "strategy_generator",
        "strategy_grader",
        "strategy_brief",
        "grade",
        "concise_answer",
        "memory",
    )


def test_front_half_plan_mapping_rejects_unknown_runtime_stage() -> None:
    with pytest.raises(ValueError, match="unknown front-half plan stage"):
        conceptual_stage_ids_for_front_half_plan(("understanding_claim", "made_up_stage"))


def test_every_current_front_half_stage_is_mapped() -> None:
    assert set(front_half_stages_in_legacy_map()) == set(FRONT_HALF_STAGES)
    for stage in FRONT_HALF_STAGES:
        assert conceptual_stage_ids_for_front_half_stage(stage)


def test_physical_stage_namespaces_are_explicit_and_front_half_aligned() -> None:
    assert set(PHYSICAL_STAGE_NAMESPACES) == {
        "agents",
        "claim",
        "cli",
        "formal",
        "front_half",
        "kernel",
        "literature",
        "llm_agents",
        "memory",
        "mock_pipeline",
        "orchestration",
        "orchestrator",
        "run-agent",
        "server",
        "strategy",
        "ui",
        "verification",
    }
    assert set(physical_stage_namespaces()) == set(PHYSICAL_STAGE_NAMESPACES)
    for mapping in LEGACY_MAPPING:
        front_half_physical = tuple(
            physical_stage.split(":", 1)[1]
            for physical_stage in mapping.physical_stages
            if physical_stage.startswith("front_half:")
        )
        assert front_half_physical == mapping.front_half_stages


def test_physical_stage_reverse_mapping_makes_runtime_folds_explicit() -> None:
    assert "front_half:strategy" in physical_stages_in_legacy_map()
    assert stage_ids_for_physical_stage("front_half:strategy") == (
        "strategy_generator",
        "strategy_grader",
        "strategy_brief",
    )
    assert stage_ids_for_physical_stage("kernel:pressure_lane_barrier") == (
        "literature_review",
        "examples",
        "counterexamples",
    )
    assert stage_ids_for_physical_stage("missing:stage") == ()
    assert shared_physical_stages() == {
        "kernel:pressure_lane_barrier": (
            "literature_review",
            "examples",
            "counterexamples",
        ),
        "front_half:counterexamples": (
            "counterexamples",
            "counterexample_admissibility",
        ),
        "front_half:regrade": ("conservative_regrade", "grade"),
        "front_half:strategy": (
            "strategy_generator",
            "strategy_grader",
            "strategy_brief",
        ),
        "strategy:time_allocation": ("strategy_grader", "time_allocation"),
        "orchestrator:llm_prover_reviewers": ("prover", "reviewers"),
        "agents:llm_agents": ("prover", "reviewers"),
        "front_half:grade_memory_memo": ("grade", "concise_answer", "memory"),
    }


def test_manifest_state_mappings_are_known_and_single_owner() -> None:
    assert set(manifest_states_in_legacy_map()) <= set(RUN_MANIFEST_STATES)
    assert manifest_states_in_legacy_map() == (
        "SCAFFOLDED",
        "CLAIM_CLEAR",
        "WAITING_CLAIM_CLARIFICATION",
        "HUMAN_CHOICE_CONTINUE_SELECTED",
        "HUMAN_CHOICE_REVISE_SELECTED",
        "HUMAN_CHOICE_STOP_SELECTED",
        "AUTO_CHOICE_CONTINUE_SELECTED",
        "WAITING_LLM_AGENT_HUMAN_GATE",
    )
    assert stage_ids_for_manifest_state("SCAFFOLDED") == ("scaffold",)
    assert stage_ids_for_manifest_state("CLAIM_CLEAR") == ("claim_clear_gate",)
    assert stage_ids_for_manifest_state("WAITING_CLAIM_CLARIFICATION") == (
        "claim_clear_gate",
    )
    assert stage_ids_for_manifest_state("WAITING_LLM_AGENT_HUMAN_GATE") == ("prover",)
    for state in manifest_states_in_legacy_map():
        assert len(stage_ids_for_manifest_state(state)) == 1


def test_scaffold_mapping_reflects_stage_status_projection() -> None:
    mapping = get_legacy_mapping("scaffold")
    text = "\n".join(mapping.notes)

    assert mapping.implementation_status == "implemented_coarse"
    assert mapping.runtime_kind == "coarse_runtime"
    assert mapping.manifest_states == ("SCAFFOLDED",)
    assert "orchestration:agent_flow_driver" in mapping.physical_stages
    assert "stage_status.yaml" in text
    assert any("process-only" in note for note in mapping.notes)
    assert "no AGENT_FLOW stage status yet" not in text
    assert any("not mathematical progress" in note for note in mapping.notes)


def test_shared_legacy_artifacts_make_coarse_runtime_folds_explicit() -> None:
    assert shared_legacy_artifacts() == {
        "computation_checks.yaml": ("examples", "source_theorem_candidate"),
        "counterexamples.yaml": ("counterexamples", "counterexample_admissibility"),
        "tests.yaml": (
            "examples",
            "counterexamples",
            "counterexample_admissibility",
            "examples_counterexamples_attacker",
        ),
        "idea_grade.yaml": ("conservative_regrade", "agent_consensus_seal", "grade"),
        "loop_control.yaml": (
            "conservative_regrade",
            "time_allocation",
            "failure_classifier",
        ),
        "review.yaml": ("conservative_regrade", "reviewers"),
        "strategy_synthesis.yaml": ("strategy_generator", "strategy_grader"),
        "strategy_brief.yaml": ("strategy_grader", "strategy_brief"),
        "time_allocation.yaml": ("strategy_grader", "time_allocation"),
        "prover_loop.yaml": ("time_allocation", "prover"),
        "prover_rounds/index.yaml": ("prover", "reviewers"),
        "final_report.md": ("agent_consensus_seal", "concise_answer"),
        "pressure_lane_barrier.yaml": (
            "literature_review",
            "examples",
            "counterexamples",
        ),
    }
    assert stage_ids_for_legacy_artifact("strategy_synthesis.yaml") == (
        "strategy_generator",
        "strategy_grader",
    )
    assert stage_ids_for_legacy_artifact("missing.yaml") == ()


def test_legacy_artifact_paths_are_run_relative_posix_paths() -> None:
    paths = legacy_artifact_paths()

    assert "prover_rounds/index.yaml" in paths
    assert "memory/index.yaml" in paths
    assert all(path.strip() for path in paths)
    assert all("\\" not in path for path in paths)
    assert all(not Path(path).is_absolute() for path in paths)
    assert all(not PureWindowsPath(path).is_absolute() for path in paths)
    assert all(".." not in Path(path).parts for path in paths)
    assert all(".." not in PureWindowsPath(path).parts for path in paths)


def test_strategy_service_split_is_explicitly_mapped() -> None:
    generator = get_legacy_mapping("strategy_generator")
    grader = get_legacy_mapping("strategy_grader")
    brief = get_legacy_mapping("strategy_brief")

    assert generator.front_half_stages == ("strategy",)
    assert grader.front_half_stages == ("strategy",)
    assert brief.front_half_stages == ("strategy",)
    assert generator.implementation_status == "implemented_coarse"
    assert grader.implementation_status == "implemented_coarse"
    assert brief.implementation_status == "implemented"
    assert get_stage("strategy_generator").starts_time_allocation is False
    assert get_stage("strategy_grader").starts_time_allocation is True
    assert any("Service boundary" in note for note in generator.notes)
    assert any("First allocation trigger" in note for note in grader.notes)


def test_source_theorem_candidate_maps_current_coarse_stages() -> None:
    mapping = get_legacy_mapping("source_theorem_candidate")

    assert mapping.implementation_status == "implemented_coarse"
    assert mapping.front_half_stages == ("source_theorem", "check_generator")
    assert "literature:source_grounding_stage" in mapping.physical_stages
    assert "verification:verification_request_probes" in mapping.physical_stages
    assert "verification:source_verification" in mapping.physical_stages
    assert "verification:theorem_applicability" in mapping.physical_stages
    assert "source_grounding.yaml" in mapping.artifacts
    assert "scoped_verification_requests.yaml" in mapping.optional_artifacts
    assert "theorem_applicability.yaml" in mapping.optional_artifacts
    assert get_stage("source_theorem_candidate").evidence_role == "candidate_only"
    assert any("validator-gated" in note for note in mapping.notes)
    assert any("structured requests only" in note for note in mapping.notes)
    assert any("finite/computational scope" in note for note in mapping.notes)


def test_conceptual_synthetic_stages_do_not_claim_direct_runtime() -> None:
    admissibility = get_legacy_mapping("counterexample_admissibility")

    assert admissibility.runtime_kind == "synthetic_from_existing_artifacts"
    assert admissibility.front_half_stages == ("counterexamples",)
    assert admissibility.pending_work
    assert get_stage("counterexample_admissibility").owns_proof_status is False


def test_legacy_map_marks_pending_optional_and_future_blocked_stages() -> None:
    pending = set(stage_ids_with_pending_work())
    blocked = set(stage_ids_blocked_by_future_work())

    assert {"literature_review", "examples", "counterexamples"} <= pending
    assert {"literature_review", "examples", "counterexamples"} <= blocked
    assert "claim_clear_gate" not in pending
    assert "claim_clear_gate" not in blocked
    assert get_legacy_mapping("time_allocation").runtime_kind == "optional_runtime"
    assert get_legacy_mapping("prover").runtime_kind == "optional_runtime"
    assert get_legacy_mapping("strategy_generator").pending_work
    assert get_legacy_mapping("strategy_grader").pending_work


def test_pressure_lane_mapping_reflects_barrier_backed_explicit_execution() -> None:
    for stage_id in ("literature_review", "examples", "counterexamples"):
        mapping = get_legacy_mapping(stage_id)
        text = "\n".join((*mapping.blocked_by_future_work, *mapping.notes))

        assert "kernel:pressure_lane_barrier" in mapping.physical_stages
        assert "pressure_lane_barrier.yaml" in mapping.optional_artifacts
        assert mapping.pending_work == (
            "Orchestrator-default pressure-lane execution remains disabled.",
        )
        assert mapping.blocked_by_future_work == (
            "Orchestrator-default pressure-lane execution product gate",
        )
        assert "barrier/resume safety" not in text
        assert "scheduling-only" in text
        assert "not orchestrator-default" in text


def test_prover_mapping_reflects_scoped_proof_verification_bridge() -> None:
    mapping = get_legacy_mapping("prover")

    assert mapping.runtime_kind == "optional_runtime"
    assert "verification:proof_verification" in mapping.physical_stages
    assert "formal:statement_lock" in mapping.physical_stages
    assert "formal:lean_bridge" in mapping.physical_stages
    assert "proof_verification.yaml" in mapping.optional_artifacts
    assert "statement_lock.yaml" in mapping.optional_artifacts
    assert any("cannot declare proof completion" in note for note in mapping.notes)
    assert any("scoped finite/computational checks" in note for note in mapping.notes)
    assert any("not a universal proof" in note for note in mapping.notes)
    assert any("lean_checked is not earnable" in note for note in mapping.notes)
    assert any("remain unverified" in note for note in mapping.notes)
    assert any("not proof evidence" in note for note in mapping.notes)


def test_update_inbox_mapping_reflects_service_boundary() -> None:
    mapping = get_legacy_mapping("update_inbox")

    assert mapping.implementation_status == "implemented_coarse"
    assert mapping.runtime_kind == "optional_runtime"
    assert "kernel:update_inbox_service" in mapping.physical_stages
    assert mapping.pending_work == ("Deeper prover-loop extraction remains future work.",)
    assert any("rejects forged, stale" in note for note in mapping.notes)
    assert any("consumed by scheduling, not resolved" in note for note in mapping.notes)


def test_concise_answer_mapping_reflects_trace_surface() -> None:
    mapping = get_legacy_mapping("concise_answer")

    assert mapping.implementation_status == "implemented_coarse"
    assert mapping.runtime_kind == "coarse_runtime"
    assert "orchestration:agent_flow_status" in mapping.physical_stages
    assert "ui:stage_trace_panel" in mapping.physical_stages
    assert "stage_status.yaml" in mapping.optional_artifacts
    assert mapping.pending_work == ()
    assert any("stage_status.yaml" in note for note in mapping.notes)
    assert any("display-safe UI surface" in note for note in mapping.notes)
    assert any("not final-answer evidence" in note for note in mapping.notes)


def test_conservative_regrade_mapping_reflects_visible_pressure_lanes() -> None:
    mapping = get_legacy_mapping("conservative_regrade")
    text = "\n".join((*mapping.pending_work, *mapping.notes))

    assert mapping.implementation_status == "partial"
    assert mapping.runtime_kind == "coarse_runtime"
    assert "visible later" not in text
    assert any("pre-strategy regrade artifact" in item for item in mapping.pending_work)
    assert any("pressure_lanes/stage_status" in note for note in mapping.notes)
    assert any("not survived/no-pressure evidence" in note for note in mapping.notes)


def test_claim_clear_gate_mapping_reflects_stage_status_projection() -> None:
    mapping = get_legacy_mapping("claim_clear_gate")

    assert mapping.implementation_status == "implemented_coarse"
    assert mapping.runtime_kind == "coarse_runtime"
    assert mapping.pending_work == ()
    assert mapping.blocked_by_future_work == ()
    assert any("stage_status.yaml" in note for note in mapping.notes)
    assert any("manifest lifecycle remains coarse" in note for note in mapping.notes)


def test_legacy_map_status_and_kind_summaries_are_explicit() -> None:
    assert set(IMPLEMENTATION_STATUSES) == {
        "implemented",
        "implemented_coarse",
        "partial",
        "docs_only",
        "dev_test_only",
        "implemented_advisory",
    }
    assert set(RUNTIME_MAPPING_KINDS) == {
        "direct_runtime",
        "coarse_runtime",
        "synthetic_from_existing_artifacts",
        "optional_runtime",
        "pending_future_work",
        "dev_test_only",
    }
    assert stage_ids_by_runtime_kind("synthetic_from_existing_artifacts") == (
        "counterexample_admissibility",
    )
    assert set(stage_ids_by_runtime_kind("optional_runtime")) == {
        "time_allocation",
        "update_inbox",
        "prover",
        "reviewers",
        "repair_target",
        "line_numbered_proof_writer",
        "agent_consensus_seal",
        "failure_classifier",
    }
    assert set(stage_ids_by_implementation_status("implemented_advisory")) == {
        "repair_target",
        "line_numbered_proof_writer",
        "agent_consensus_seal",
    }
    assert stage_ids_by_implementation_status("docs_only") == ()
    assert stage_ids_by_runtime_kind("pending_future_work") == ()


def test_legacy_map_output_gaps_are_explicit_pending_work() -> None:
    gaps = unmapped_stage_outputs()

    assert gaps == {
        "examples_counterexamples_attacker": ("example_counterexample_attacks.yaml",),
        "strategy_grader": ("strategy_grades.yaml",),
    }
    for stage_id, outputs in gaps.items():
        pending_text = "\n".join(get_legacy_mapping(stage_id).pending_work)
        for output in outputs:
            assert output in pending_text


def test_advisory_optional_surfaces_stay_advisory_in_map() -> None:
    for stage_id in (
        "repair_target",
        "line_numbered_proof_writer",
        "agent_consensus_seal",
    ):
        mapping = get_legacy_mapping(stage_id)
        assert mapping.implementation_status == "implemented_advisory"
        assert mapping.runtime_kind == "optional_runtime"
        assert get_stage(stage_id).owns_proof_status is False
