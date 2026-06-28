from __future__ import annotations

from espresso_proof.orchestration.agent_flow_spec import (
    AGENT_FLOW_STAGES,
    CLAIM_CLEAR_BLOCKED_STAGE_IDS,
    EVIDENCE_ROLES,
    STAGE_STATUS_KINDS,
    get_stage,
    is_normalized_stage_id,
    ordered_stage_ids,
    stage_index,
    stages_blocked_by_unclear_claim,
    stages_by_parallel_group,
    time_allocation_triggers,
    validate_agent_flow_spec,
)


def test_agent_flow_spec_is_structurally_valid() -> None:
    validate_agent_flow_spec()
    ids = ordered_stage_ids()
    assert len(ids) == len(set(ids))
    assert ids[:3] == ("scaffold", "understanding_claim", "claim_clear_gate")
    assert all(is_normalized_stage_id(stage_id) for stage_id in ids)


def test_stage_status_and_evidence_vocabularies_are_explicit() -> None:
    assert set(STAGE_STATUS_KINDS) == {
        "entry",
        "gate",
        "required",
        "pressure_lane",
        "candidate_pass",
        "advisory",
        "human_gate",
        "report",
    }
    assert set(EVIDENCE_ROLES) == {
        "none",
        "candidate_only",
        "checked_evidence_possible",
        "proof_candidate_only",
        "advisory_only",
        "presentation_only",
    }
    assert {stage.status_kind for stage in AGENT_FLOW_STAGES} <= set(STAGE_STATUS_KINDS)
    assert {stage.evidence_role for stage in AGENT_FLOW_STAGES} <= set(EVIDENCE_ROLES)


def test_unclear_claim_blocks_downstream_swarm() -> None:
    blocked = set(stages_blocked_by_unclear_claim())
    assert CLAIM_CLEAR_BLOCKED_STAGE_IDS == stages_blocked_by_unclear_claim()
    assert CLAIM_CLEAR_BLOCKED_STAGE_IDS == ordered_stage_ids()[3:]
    assert set(CLAIM_CLEAR_BLOCKED_STAGE_IDS) <= blocked
    assert "understanding_claim" not in blocked
    assert "claim_clear_gate" not in blocked
    for stage_id in (
        "literature_review",
        "examples",
        "counterexamples",
        "strategy_generator",
        "prover",
        "reviewers",
        "line_numbered_proof_writer",
        "agent_consensus_seal",
    ):
        assert stage_id in blocked


def test_pressure_lanes_do_not_block_strategy_generation() -> None:
    strategy = get_stage("strategy_generator")
    assert "examples" not in strategy.depends_on
    assert "counterexamples" not in strategy.depends_on
    assert "examples" in strategy.reads_from
    assert "counterexamples" in strategy.reads_from
    assert get_stage("examples").status_kind == "pressure_lane"
    assert get_stage("counterexamples").status_kind == "pressure_lane"


def test_source_theorem_candidate_pass_precedes_strategy() -> None:
    assert stage_index("source_theorem_candidate") < stage_index("strategy_generator")
    source = get_stage("source_theorem_candidate")
    assert source.evidence_role == "candidate_only"
    assert any("source_backed" in note for note in source.notes)


def test_strategy_grader_is_the_time_allocation_trigger() -> None:
    assert time_allocation_triggers() == ("strategy_grader",)
    assert not get_stage("strategy_generator").starts_time_allocation
    assert get_stage("time_allocation").depends_on == ("strategy_grader",)


def test_parallel_groups_match_agent_flow_lanes() -> None:
    context_pressure = {stage.id for stage in stages_by_parallel_group("context_pressure")}
    assert {"literature_review", "examples", "counterexamples"} <= context_pressure
    strategy_assessment = {stage.id for stage in stages_by_parallel_group("strategy_assessment")}
    assert strategy_assessment == {"examples_counterexamples_attacker", "strategy_grader"}


def test_advisory_stages_do_not_own_proof_status() -> None:
    assert all(not stage.owns_proof_status for stage in AGENT_FLOW_STAGES)
    for stage_id in (
        "agent_consensus_seal",
        "human_strategy_preference",
        "update_inbox",
        "memory",
        "reviewers",
    ):
        assert get_stage(stage_id).evidence_role == "advisory_only"


def test_agent_consensus_seals_only_line_numbered_proofs() -> None:
    proof_writer = get_stage("line_numbered_proof_writer")
    seal = get_stage("agent_consensus_seal")
    assert stage_index("prover") < stage_index("line_numbered_proof_writer")
    assert stage_index("line_numbered_proof_writer") < stage_index("agent_consensus_seal")
    assert seal.depends_on == ("line_numbered_proof_writer",)
    assert "line_numbered_proof.yaml" in proof_writer.outputs
    assert "agent_consensus_review.yaml" in seal.outputs
    assert proof_writer.evidence_role == "proof_candidate_only"
    assert seal.evidence_role == "advisory_only"
    assert any("route-only" in note for note in seal.notes)
    assert any("not a machine check" in note for note in seal.notes)


def test_repair_target_is_advisory_sidecar() -> None:
    repair = get_stage("repair_target")
    assert repair.depends_on == ("reviewers",)
    assert repair.outputs == ("repair_target.yaml",)
    assert repair.evidence_role == "advisory_only"
    assert any("Snapshot-excluded sidecar" in note for note in repair.notes)
