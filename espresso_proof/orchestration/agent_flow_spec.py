"""Executable AGENT_FLOW stage graph metadata.

This module is deliberately declarative: it does not run agents, mutate run
artifacts, or interpret mathematical status. It gives orchestration, tests, and
UI code a small source of truth for AGENT_FLOW ordering, dependencies, parallel
pressure lanes, human gates, and advisory-only boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StageStatusKind = Literal[
    "entry",
    "gate",
    "required",
    "pressure_lane",
    "candidate_pass",
    "advisory",
    "human_gate",
    "report",
]

EvidenceRole = Literal[
    "none",
    "candidate_only",
    "checked_evidence_possible",
    "proof_candidate_only",
    "advisory_only",
    "presentation_only",
]


@dataclass(frozen=True)
class AgentFlowStage:
    """One node in the Espresso AGENT_FLOW graph.

    `depends_on` contains hard ordering dependencies. `reads_from` contains
    advisory inputs that may be pending, skipped, or absent; consumers must keep
    that uncertainty visible instead of treating it as evidence.
    """

    id: str
    display_name: str
    purpose: str
    depends_on: tuple[str, ...] = ()
    reads_from: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    status_kind: StageStatusKind = "required"
    evidence_role: EvidenceRole = "none"
    parallel_group: str | None = None
    blocks_if_claim_unclear: bool = True
    starts_time_allocation: bool = False
    owns_proof_status: bool = False
    notes: tuple[str, ...] = ()


CLAIM_CLEAR_BLOCKED_STAGE_IDS: tuple[str, ...] = (
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
    "human_strategy_preference",
    "time_allocation",
    "update_inbox",
    "prover",
    "reviewers",
    "repair_target",
    "line_numbered_proof_writer",
    "agent_consensus_seal",
    "failure_classifier",
    "grade",
    "concise_answer",
    "memory",
)


AGENT_FLOW_STAGES: tuple[AgentFlowStage, ...] = (
    AgentFlowStage(
        id="scaffold",
        display_name="Scaffold Run",
        purpose="Create a run directory, input file, manifest, and kernel-valid placeholders.",
        outputs=("run_manifest.yaml", "input.md"),
        status_kind="entry",
        blocks_if_claim_unclear=False,
    ),
    AgentFlowStage(
        id="understanding_claim",
        display_name="Understanding Claim Agent",
        purpose="Lock one mathematical claim, preserve quantifiers, and record ambiguity or missing hypotheses.",
        depends_on=("scaffold",),
        outputs=("locked_claim.yaml", "hypothesis_ledger.yaml"),
        status_kind="required",
        evidence_role="advisory_only",
        blocks_if_claim_unclear=False,
        notes=("The agent can propose a locked claim, but cannot mark it human-confirmed.",),
    ),
    AgentFlowStage(
        id="claim_clear_gate",
        display_name="Claim-Clear Gate",
        purpose="Stop downstream swarm stages unless the locked claim is precise enough to study.",
        depends_on=("understanding_claim",),
        outputs=("claim_clear_gate.yaml",),
        status_kind="gate",
        blocks_if_claim_unclear=False,
    ),
    AgentFlowStage(
        id="literature_review",
        display_name="Literature Review",
        purpose="Find candidate papers, theorems, definitions, and source locators after claim clear.",
        depends_on=("claim_clear_gate",),
        outputs=("literature_review.yaml",),
        status_kind="pressure_lane",
        evidence_role="candidate_only",
        parallel_group="context_pressure",
        notes=("Candidate discovery never creates source_backed status.",),
    ),
    AgentFlowStage(
        id="examples",
        display_name="Examples Pressure Lane",
        purpose="Select canonical examples and computation checks that pressure-test the claim.",
        depends_on=("claim_clear_gate",),
        reads_from=("literature_review",),
        outputs=("examples.yaml", "computation_checks.yaml", "tests.yaml"),
        status_kind="pressure_lane",
        evidence_role="checked_evidence_possible",
        parallel_group="context_pressure",
        notes=("Pending examples are not survived_tests.",),
    ),
    AgentFlowStage(
        id="counterexamples",
        display_name="Counterexamples Pressure Lane",
        purpose="Search for hard examples, pressure tests, and possible counterexamples.",
        depends_on=("claim_clear_gate",),
        reads_from=("literature_review",),
        outputs=("counterexamples.yaml", "tests.yaml"),
        status_kind="pressure_lane",
        evidence_role="checked_evidence_possible",
        parallel_group="context_pressure",
        notes=("Pending counterexamples are not failed_tests and do not mean no pressure.",),
    ),
    AgentFlowStage(
        id="counterexample_admissibility",
        display_name="Counterexample Admissibility Check",
        purpose="Classify whether a counterexample satisfies all locked-claim hypotheses and violates the conclusion.",
        depends_on=("counterexamples",),
        reads_from=("understanding_claim",),
        outputs=("counterexamples.yaml", "tests.yaml"),
        status_kind="required",
        evidence_role="checked_evidence_possible",
        parallel_group="context_pressure",
        notes=("Only admissible counterexamples can refute the locked claim.",),
    ),
    AgentFlowStage(
        id="source_theorem_candidate",
        display_name="Source / Theorem Candidate Pass",
        purpose="Record candidate source matches and theorem-condition risks before strategy generation.",
        depends_on=("claim_clear_gate",),
        reads_from=("literature_review", "examples", "counterexamples"),
        outputs=("source_grounding.yaml", "theorem_applicability.yaml"),
        status_kind="candidate_pass",
        evidence_role="candidate_only",
        notes=(
            "A source candidate is not source_backed.",
            "Theorem applicability does not prove a route or theorem.",
        ),
    ),
    AgentFlowStage(
        id="conservative_regrade",
        display_name="Conservative Regrade",
        purpose="Refresh grade/status from checked artifacts and known blockers before strategy generation.",
        depends_on=("claim_clear_gate", "source_theorem_candidate"),
        reads_from=("examples", "counterexamples", "literature_review"),
        outputs=("idea_grade.yaml", "loop_control.yaml"),
        status_kind="advisory",
        evidence_role="advisory_only",
        notes=("Pending pressure must remain pending, not survived/no-pressure evidence.",),
    ),
    AgentFlowStage(
        id="strategy_generator",
        display_name="Strategy Generator",
        purpose="Generate several candidate proof routes once the claim is clear.",
        depends_on=("claim_clear_gate", "source_theorem_candidate", "conservative_regrade"),
        reads_from=("literature_review", "examples", "counterexamples"),
        outputs=("strategy_synthesis.yaml",),
        status_kind="required",
        evidence_role="advisory_only",
        notes=(
            "May run while examples/counterexamples pressure lanes are pending.",
            "Does not start Time Allocation by itself.",
        ),
    ),
    AgentFlowStage(
        id="examples_counterexamples_attacker",
        display_name="Examples/Counterexamples Attacker",
        purpose="Attack candidate routes with checked examples, hard examples, and counterexample pressure.",
        depends_on=("strategy_generator",),
        reads_from=("examples", "counterexamples", "counterexample_admissibility"),
        outputs=("example_counterexample_attacks.yaml", "tests.yaml"),
        status_kind="advisory",
        evidence_role="checked_evidence_possible",
        parallel_group="strategy_assessment",
        notes=("Concrete example failure can lower or block a strategy score.",),
    ),
    AgentFlowStage(
        id="strategy_grader",
        display_name="Strategy Grader",
        purpose="Score plausibility, coherence, taste, repairability, and risk for candidate routes.",
        depends_on=("strategy_generator",),
        reads_from=("examples_counterexamples_attacker", "source_theorem_candidate"),
        outputs=("strategy_grades.yaml", "strategy_synthesis.yaml"),
        status_kind="advisory",
        evidence_role="advisory_only",
        parallel_group="strategy_assessment",
        starts_time_allocation=True,
        notes=(
            "First trigger for bounded Time Allocation.",
            "Cannot override concrete example failure or theorem-condition gaps.",
        ),
    ),
    AgentFlowStage(
        id="strategy_brief",
        display_name="Strategy Brief",
        purpose="Present top routes, risks, source status, effort estimates, and the next human-facing choice.",
        depends_on=("strategy_grader",),
        reads_from=("examples_counterexamples_attacker", "source_theorem_candidate", "conservative_regrade"),
        outputs=("strategy_brief.yaml",),
        status_kind="report",
        evidence_role="presentation_only",
    ),
    AgentFlowStage(
        id="human_strategy_preference",
        display_name="Human Strategy Preference",
        purpose="Optionally record the mathematician's route preference and effort budget.",
        depends_on=("strategy_brief",),
        outputs=("human_choice.yaml",),
        status_kind="human_gate",
        evidence_role="advisory_only",
        notes=("Human preference is steering, not evidence.",),
    ),
    AgentFlowStage(
        id="time_allocation",
        display_name="Time Allocation Agent",
        purpose="Allocate bounded prover effort from strategy grades, known blockers, optional human preference, and budget.",
        depends_on=("strategy_grader",),
        reads_from=("human_strategy_preference", "update_inbox", "examples_counterexamples_attacker"),
        outputs=("prover_loop.yaml", "loop_control.yaml"),
        status_kind="advisory",
        evidence_role="advisory_only",
        parallel_group="prover_control",
        notes=("Allocation can be revised by late blockers and reviewer objections.",),
    ),
    AgentFlowStage(
        id="update_inbox",
        display_name="Update Inbox",
        purpose="Record deduplicated scheduling-only late blockers and reviewer objections.",
        depends_on=("strategy_grader",),
        reads_from=("source_theorem_candidate", "examples_counterexamples_attacker", "reviewers"),
        outputs=("update_inbox.yaml",),
        status_kind="advisory",
        evidence_role="advisory_only",
        parallel_group="prover_control",
        notes=("Scheduling-only and advisory-only; cannot own proof/source/evidence/status fields.",),
    ),
    AgentFlowStage(
        id="prover",
        display_name="Prover",
        purpose="Try allocated routes and produce proof sketches, proof candidates, conditional arguments, or repair attempts.",
        depends_on=("time_allocation",),
        reads_from=("strategy_brief", "human_strategy_preference", "update_inbox"),
        outputs=("llm_agent_run.yaml", "prover_loop.yaml", "prover_rounds/index.yaml"),
        status_kind="required",
        evidence_role="proof_candidate_only",
        parallel_group="prover_line",
        notes=("Cannot declare proof completion.",),
    ),
    AgentFlowStage(
        id="reviewers",
        display_name="Parallel Reviewers",
        purpose="Independently review proof outline coherence, literature use, and line-level proof detail.",
        depends_on=("prover",),
        outputs=("review.yaml", "prover_rounds/index.yaml"),
        status_kind="required",
        evidence_role="advisory_only",
        parallel_group="review",
        notes=("Reviewer approval does not prove a theorem.",),
    ),
    AgentFlowStage(
        id="repair_target",
        display_name="Repair Target Sidecar",
        purpose="Bind scoped reviewer objections to the next continuation attempt as advisory repair context.",
        depends_on=("reviewers",),
        reads_from=("prover", "update_inbox"),
        outputs=("repair_target.yaml",),
        status_kind="advisory",
        evidence_role="advisory_only",
        parallel_group="prover_control",
        notes=(
            "Snapshot-excluded sidecar; it steers the next attempt but is not a manifest artifact.",
            "Repair targets cannot upgrade proof, source, evidence, grade, or checked status.",
        ),
    ),
    AgentFlowStage(
        id="line_numbered_proof_writer",
        display_name="Line-Numbered Proof Writer",
        purpose="Turn a prover candidate route into a structured advisory proof with line ids, justifications, and dependencies.",
        depends_on=("prover",),
        reads_from=("strategy_brief", "source_theorem_candidate", "reviewers"),
        outputs=("line_numbered_proof.yaml",),
        status_kind="advisory",
        evidence_role="proof_candidate_only",
        parallel_group="review",
        notes=(
            "A route, proposed_steps list, or sketch is not sealable.",
            "The artifact is advisory and carries no source-backed, checked, proved, or human-confirmed status.",
        ),
    ),
    AgentFlowStage(
        id="agent_consensus_seal",
        display_name="Agent-Consensus Seal",
        purpose="Run adversarial proof reviewers over the line-numbered proof and, only on unanimous no-fatal-flaw, write the amber advisory consensus record.",
        depends_on=("line_numbered_proof_writer",),
        reads_from=("reviewers", "source_theorem_candidate", "prover"),
        outputs=("agent_consensus_review.yaml", "idea_grade.yaml", "final_report.md"),
        status_kind="advisory",
        evidence_role="advisory_only",
        parallel_group="review",
        notes=(
            "Only the line-numbered proof is reviewed; route-only consensus fails closed.",
            "The seal is hash-bound to current artifacts and is not a machine check.",
            "agent_consensus_mock_checked is amber/advisory and never green, source-backed, checked, or proved.",
        ),
    ),
    AgentFlowStage(
        id="failure_classifier",
        display_name="Failure Classifier",
        purpose="Classify blockers such as source_gap, proof_gap, theorem_condition_gap, misapplied_source, no_progress, or budget_exhausted.",
        depends_on=("reviewers",),
        reads_from=(
            "update_inbox",
            "repair_target",
            "examples_counterexamples_attacker",
            "source_theorem_candidate",
        ),
        outputs=("loop_control.yaml", "failure_pattern_audit.yaml"),
        status_kind="advisory",
        evidence_role="advisory_only",
        notes=("Failure categories control routing, not proof status.",),
    ),
    AgentFlowStage(
        id="grade",
        display_name="Grade",
        purpose="Conservatively score whether the current idea is worth continuing.",
        depends_on=("reviewers", "failure_classifier"),
        reads_from=(
            "agent_consensus_seal",
            "examples_counterexamples_attacker",
            "source_theorem_candidate",
            "human_strategy_preference",
        ),
        outputs=("idea_grade.yaml",),
        status_kind="advisory",
        evidence_role="advisory_only",
        notes=(
            "Grade is not theorem verification.",
            "A perfect grade remains blocked for amber agent consensus and other non-global proof statuses.",
        ),
    ),
    AgentFlowStage(
        id="concise_answer",
        display_name="Concise Answer",
        purpose="Render a compact research memo with top routes, truth labels, obstruction, and next action.",
        depends_on=("grade",),
        reads_from=("strategy_brief", "human_strategy_preference", "update_inbox"),
        outputs=("final_report.md",),
        status_kind="report",
        evidence_role="presentation_only",
    ),
    AgentFlowStage(
        id="memory",
        display_name="Memory",
        purpose="Save useful routes, failures, examples, source gaps, and human preferences for future advisory context.",
        depends_on=("concise_answer",),
        reads_from=("grade", "strategy_brief", "human_strategy_preference"),
        outputs=("memory/", "memory_context.yaml"),
        status_kind="advisory",
        evidence_role="advisory_only",
        notes=("Memory is advisory, not evidence.",),
    ),
)

_AGENT_FLOW_BY_ID: dict[str, AgentFlowStage] = {stage.id: stage for stage in AGENT_FLOW_STAGES}


def ordered_stage_ids() -> tuple[str, ...]:
    """Return AGENT_FLOW stage ids in canonical display/execution order."""

    return tuple(stage.id for stage in AGENT_FLOW_STAGES)


def get_stage(stage_id: str) -> AgentFlowStage:
    """Return one stage by id, raising KeyError for an unknown stage."""

    return _AGENT_FLOW_BY_ID[stage_id]


def stage_index(stage_id: str) -> int:
    """Return the canonical order index for one stage id."""

    return ordered_stage_ids().index(stage_id)


def stages_blocked_by_unclear_claim() -> tuple[str, ...]:
    """Return stages that must not run while the claim-clear gate blocks downstream work."""

    return tuple(stage.id for stage in AGENT_FLOW_STAGES if stage.blocks_if_claim_unclear)


def stages_by_parallel_group(group: str) -> tuple[AgentFlowStage, ...]:
    """Return stages tagged as part of a conceptual parallel lane/group."""

    return tuple(stage for stage in AGENT_FLOW_STAGES if stage.parallel_group == group)


def time_allocation_triggers() -> tuple[str, ...]:
    """Return stage ids whose completed output may start bounded Time Allocation."""

    return tuple(stage.id for stage in AGENT_FLOW_STAGES if stage.starts_time_allocation)


def validate_agent_flow_spec() -> None:
    """Fail if the declarative graph violates local structural invariants."""

    ids = ordered_stage_ids()
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate AGENT_FLOW stage ids")
    known = set(ids)
    for stage in AGENT_FLOW_STAGES:
        missing = (set(stage.depends_on) | set(stage.reads_from)) - known
        if missing:
            raise ValueError(f"stage {stage.id!r} references unknown stage(s): {sorted(missing)}")
        for dependency in stage.depends_on:
            if stage_index(dependency) >= stage_index(stage.id):
                raise ValueError(
                    f"stage {stage.id!r} depends on non-earlier stage {dependency!r}"
                )
    if "strategy_generator" in time_allocation_triggers():
        raise ValueError("Strategy Generator alone must not start Time Allocation")
    if time_allocation_triggers() != ("strategy_grader",):
        raise ValueError("Strategy Grader must be the first Time Allocation trigger")
    if any(stage.owns_proof_status for stage in AGENT_FLOW_STAGES):
        raise ValueError("AGENT_FLOW stages must not own theorem proof status")
    seal = get_stage("agent_consensus_seal")
    if "line_numbered_proof_writer" not in seal.depends_on:
        raise ValueError("Agent consensus must review a line-numbered proof, not a route")
    if get_stage("line_numbered_proof_writer").evidence_role != "proof_candidate_only":
        raise ValueError("Line-numbered proof writer must remain proof-candidate-only")


validate_agent_flow_spec()

__all__ = [
    "AGENT_FLOW_STAGES",
    "CLAIM_CLEAR_BLOCKED_STAGE_IDS",
    "AgentFlowStage",
    "EvidenceRole",
    "StageStatusKind",
    "get_stage",
    "ordered_stage_ids",
    "stage_index",
    "stages_blocked_by_unclear_claim",
    "stages_by_parallel_group",
    "time_allocation_triggers",
    "validate_agent_flow_spec",
]
