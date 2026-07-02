"""Kernel data models — file-order slice 5/8. Imported via espresso_proof.kernel.models."""


from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


from espresso_proof.kernel.models._part1 import *  # noqa: F401,F403


from espresso_proof.kernel.models._part2 import *  # noqa: F401,F403


from espresso_proof.kernel.models._part3 import *  # noqa: F401,F403


from espresso_proof.kernel.models._part4 import *  # noqa: F401,F403


class ReviewerReport(StrictBaseModel):
    reviewer_id: Literal["strategy_reviewer", "evidence_reviewer", "line_proof_reviewer"]
    scope: Literal["proof_strategy", "examples_counterexamples", "line_by_line_proof"]
    verdict: ReviewerVerdict
    applicability: ReviewerApplicability = ReviewerApplicability.applicable
    summary: str
    objections: list[str] = Field(default_factory=list)
    checked_artifacts: list[str] = Field(default_factory=list)
    required_reruns: list[str] = Field(default_factory=list)
    severity: Literal["low", "medium", "high"]

    @model_validator(mode="after")
    def validate_applicability(self) -> ReviewerReport:
        if self.reviewer_id != "line_proof_reviewer" and self.applicability != ReviewerApplicability.applicable:
            raise ValueError("only line_proof_reviewer may be sketch_only or not_applicable")
        if self.applicability != ReviewerApplicability.applicable and self.verdict == ReviewerVerdict.failed:
            raise ValueError("sketch_only/not_applicable cannot have fail verdict")
        return self


class Review(StrictBaseModel):
    claim_id: str
    attempt_status: AttemptStatus
    verdict: Literal["continue", "repair_first", "likely_broken", "refuted", "unclear"]
    reviewers: list[ReviewerReport]
    strongest_objection: str
    theorem_drift: list[str] = Field(default_factory=list)
    missing_hypotheses: list[str] = Field(default_factory=list)
    source_risks: list[str] = Field(default_factory=list)
    example_risks: list[str] = Field(default_factory=list)
    downgrade_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_parallel_reviewers(self) -> Review:
        required = {"strategy_reviewer", "evidence_reviewer", "line_proof_reviewer"}
        seen = {reviewer.reviewer_id for reviewer in self.reviewers}
        if seen != required:
            raise ValueError(f"review requires exactly these reviewers: {sorted(required)}")

        any_fail = any(reviewer.verdict == ReviewerVerdict.failed for reviewer in self.reviewers)
        any_unclear = any(reviewer.verdict == ReviewerVerdict.unclear for reviewer in self.reviewers)
        if any_fail and self.attempt_status != AttemptStatus.failed:
            raise ValueError("any failed reviewer forces attempt_status=failed")
        if not any_fail and any_unclear and self.attempt_status != AttemptStatus.unclear:
            raise ValueError("unclear reviewers without failures force attempt_status=unclear")
        if not any_fail and not any_unclear and self.attempt_status != AttemptStatus.passed:
            raise ValueError("all reviewers passed but attempt_status is not passed")
        return self


class IdeaGrade(StrictBaseModel):
    claim_id: str
    status: IdeaStatus
    score: int = Field(ge=0, le=100)
    confidence: Literal["low", "medium", "high"]
    reasons: list[str]
    survived_tests: list[str] = Field(default_factory=list)
    failed_tests: list[str] = Field(default_factory=list)
    counterexample_candidates: list[str] = Field(default_factory=list)
    missing_hypotheses: list[str] = Field(default_factory=list)
    source_gaps: list[str] = Field(default_factory=list)
    repair_options: list[str] = Field(default_factory=list)
    next_cheapest_test: str
    human_review_required: bool
    proof_language_status: ProofLanguageStatus


class StrategyBriefItem(StrictBaseModel):
    id: str
    title: str
    score: int = Field(ge=0, le=100)
    status: IdeaStatus
    why_this_route: str
    survived_tests: list[str] = Field(default_factory=list)
    failed_tests: list[str] = Field(default_factory=list)
    main_risk: str
    source_status: Literal["source_backed", "candidate", "unverified", "mixed", "not_found"]
    estimated_effort: ExplorationMode
    next_loop_plan: str


class StrategySynthesis(StrictBaseModel):
    claim_id: str
    synthesized_from: list[str]
    routes: list[StrategyBriefItem]
    discarded_routes: list[str] = Field(default_factory=list)
    synthesis_note: str


class AlsoConsideredStrategy(StrictBaseModel):
    title: str
    reason_not_top_three: str


class HumanChoiceOption(StrictBaseModel):
    strategy_id: str
    effort_options: list[ExplorationMode]


class StrategyBrief(StrictBaseModel):
    claim_id: str
    generated_after_runs: list[str]
    top_strategies: list[StrategyBriefItem]
    also_considered: list[AlsoConsideredStrategy] = Field(default_factory=list)
    question_for_mathematician: str
    allowed_human_choices: list[HumanChoiceOption]

    @model_validator(mode="after")
    def validate_choices_and_scores(self) -> StrategyBrief:
        strategy_ids = {strategy.id for strategy in self.top_strategies}
        choice_ids = {choice.strategy_id for choice in self.allowed_human_choices}
        missing_choices = strategy_ids.difference(choice_ids)
        if missing_choices:
            raise ValueError(
                f"missing human choice options for strategies: {sorted(missing_choices)}"
            )
        if "none" not in choice_ids:
            raise ValueError("strategy brief must include a none human choice option")
        for choice in self.allowed_human_choices:
            if not choice.effort_options:
                raise ValueError(f"human choice {choice.strategy_id} requires effort options")
            if choice.strategy_id not in strategy_ids and choice.strategy_id != "none":
                raise ValueError(f"unknown strategy_id in human choice: {choice.strategy_id}")
        for strategy in self.top_strategies:
            if strategy.score >= 75 and strategy.source_status in {"unverified", "not_found"}:
                raise ValueError("high-scoring strategies require at least candidate or mixed source status")
        return self


class TimeAllocationEntry(StrictBaseModel):
    id: str
    route_id: str | None = None
    action: Literal[
        "source_pinning",
        "cheap_check",
        "bounded_proof_exploration",
        "repair_attempt",
        "ask_human",
        "stop",
    ]
    budget: Literal["tiny", "small", "bounded", "human", "none"]
    unchecked: Literal[True]
    advisory_only: Literal[True]
    effect: Literal["scheduling_only"]
    reason: str

    @model_validator(mode="after")
    def validate_route_binding(self) -> TimeAllocationEntry:
        route_actions = {
            "source_pinning",
            "cheap_check",
            "bounded_proof_exploration",
            "repair_attempt",
        }
        if self.action in route_actions and not self.route_id:
            raise ValueError(f"{self.action} allocation requires route_id")
        if self.action == "ask_human" and self.budget != "human":
            raise ValueError("ask_human allocation requires human budget")
        if self.action == "stop" and self.budget != "none":
            raise ValueError("stop allocation requires none budget")
        if self.action == "bounded_proof_exploration" and self.budget != "bounded":
            raise ValueError("bounded proof exploration must use bounded budget")
        return self


class TimeAllocationPlan(StrictBaseModel):
    """V2 Task 04 provisional scheduling plan from Strategy Grader.

    This artifact is deliberately scheduling-only: it can order work and name
    missing context, but it has no field that can own proof/source/evidence/
    theorem/refutation/grade status. StrictBaseModel rejects any smuggled status
    fields on the plan or its entries.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    trigger: Literal["strategy_grader"]
    allocation_state: Literal["TIME_ALLOCATION_STARTED"]
    strategy_artifact: Literal["strategy_brief", "strategy_synthesis"]
    advisory_only: Literal[True]
    effect: Literal["scheduling_only"]
    missing_context: list[
        Literal[
            "strategy_brief",
            "human_preference",
            "source_support",
            "examples_pressure",
            "counterexamples_pressure",
        ]
    ] = Field(default_factory=list)
    entries: list[TimeAllocationEntry]
    forbidden_status_effect: list[str] = Field(
        default_factory=lambda: [
            "proof",
            "source",
            "evidence",
            "theorem_applicability",
            "refutation",
            "grade",
        ]
    )

    @model_validator(mode="after")
    def validate_scheduling_only(self) -> TimeAllocationPlan:
        if not self.entries:
            raise ValueError("time allocation plan requires at least one entry")
        ids = [entry.id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("time allocation entries must have unique ids")
        if len(self.missing_context) != len(set(self.missing_context)):
            raise ValueError("missing_context entries must be unique")
        return self


UpdateInboxType = Literal[
    "source_gap",
    "source_status_signal",
    "theorem_condition_blocker",
    "example_pressure",
    "counterexample_pressure",
    "counterexample_admissibility",
    "strategy_brief",
    "human_preference",
    "reviewer_objection",
    "prover_progress",
    "budget_signal",
    "advisory_memory_signal",
]


class UpdateInboxEntry(StrictBaseModel):
    """Scheduling-only late signal for V2 Update Inbox.

    The entry has no payload field and no mathematical status owner field. Strict
    validation rejects status smuggling; validator_ok only means the origin
    artifact validated in its own lane.
    """

    id: str
    type: UpdateInboxType
    origin_stage: str
    origin_artifact: str
    origin_artifact_id_or_path: str
    locked_claim_id: str
    route_id_or_step_id: str | None
    artifact_revision_or_hash: str
    validator_ok: bool
    created_at: str
    dedupe_key: str
    applied_at: str | None
    effect: Literal["scheduling_only"]
    advisory_only: Literal[True]

    @model_validator(mode="after")
    def validate_required_text(self) -> UpdateInboxEntry:
        text_fields = {
            "id": self.id,
            "origin_stage": self.origin_stage,
            "origin_artifact": self.origin_artifact,
            "origin_artifact_id_or_path": self.origin_artifact_id_or_path,
            "locked_claim_id": self.locked_claim_id,
            "artifact_revision_or_hash": self.artifact_revision_or_hash,
            "created_at": self.created_at,
            "dedupe_key": self.dedupe_key,
        }
        blank = [name for name, value in text_fields.items() if not value.strip()]
        if blank:
            raise ValueError(f"update inbox entry fields cannot be blank: {blank}")
        if self.route_id_or_step_id is not None and not self.route_id_or_step_id.strip():
            raise ValueError("route_id_or_step_id cannot be blank")
        return self


class UpdateInboxReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    entries: list[UpdateInboxEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_entries(self) -> UpdateInboxReport:
        ids = [entry.id for entry in self.entries]
        dedupe = [entry.dedupe_key for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("update inbox entry ids must be unique")
        if len(dedupe) != len(set(dedupe)):
            raise ValueError("update inbox dedupe_key values must be unique")
        return self


class HumanChoice(StrictBaseModel):
    claim_id: str
    status: HumanChoiceStatus
    selected_strategy: str | None = None
    effort: ExplorationMode | None = None
    taste_judgment: str = ""
    continue_decision: ContinueDecision
    # human_confirmed records ONLY that a human made THIS strategy selection (it is
    # required true whenever status == selected AND selected_by == 'human', see
    # validate_choice_state). It is NOT a proof/claim confirmation and NOT the
    # LLM-agent human gate: a valid run can carry human_confirmed=true here while
    # llm_agent_run.human_gate.status is still 'pending' (the human picked a route;
    # the prover-pass output still awaits review). Do not read it as "the human
    # confirmed the result".
    human_confirmed: bool = False
    # Provenance of the selection. 'human' (default, today's behavior) means a real
    # human picked the strategy; 'auto' means machinery picked it with no human in
    # the loop. Auto provenance can NEVER claim human confirmation: selected_by ==
    # 'auto' forces human_confirmed == false (fail closed, any status).
    selected_by: Literal["human", "auto"] = "human"

    @model_validator(mode="after")
    def validate_choice_state(self) -> HumanChoice:
        """Enforce choice-state consistency and honest selection provenance.

        Only selected_by == 'human' may claim human confirmation: any other
        provenance value (auto today, anything a future Literal widening might
        add) requires human_confirmed == false regardless of status — the check
        is exhaustive on purpose, not an 'auto' special case. A human selection
        keeps today's rule: status == selected requires human_confirmed == true.
        """
        if self.human_confirmed and self.selected_by != "human":
            raise ValueError(
                f"selected_by={self.selected_by!r} can never claim human_confirmed=true "
                "(only a real human selection may)"
            )
        if self.status == HumanChoiceStatus.selected:
            if not self.selected_strategy:
                raise ValueError("selected human choice requires selected_strategy")
            if self.effort is None:
                raise ValueError("selected human choice requires effort")
            if self.continue_decision == ContinueDecision.pending:
                raise ValueError("selected human choice requires a non-pending continue_decision")
            if self.selected_by == "human" and not self.human_confirmed:
                raise ValueError("selected human choice requires human_confirmed=true")
        if self.status == HumanChoiceStatus.pending:
            if self.selected_strategy is not None or self.effort is not None:
                raise ValueError("pending human choice cannot have selected_strategy or effort")
            if self.continue_decision != ContinueDecision.pending:
                raise ValueError("pending human choice requires continue_decision=pending")
        return self


class HumanFeedbackItem(StrictBaseModel):
    feedback_id: str
    target_type: HumanFeedbackTargetType
    target_artifact: str
    target_id: str
    labels: list[HumanFeedbackLabel]
    reason: str
    scope: HumanFeedbackScope
    requested_actions: list[HumanFeedbackAction] = Field(default_factory=list)
    human_confirmed: bool
    supersedes_feedback_id: str | None = None

    @model_validator(mode="after")
    def validate_feedback_item(self) -> HumanFeedbackItem:
        if len(self.labels) != len(set(self.labels)):
            raise ValueError("feedback labels must be unique")
        if not self.labels:
            raise ValueError("feedback requires at least one label")
        if not self.reason.strip():
            raise ValueError("feedback reason cannot be empty")
        return self


class HumanExampleFeedback(StrictBaseModel):
    schema_version: Literal[1] = 1
    project_id: str
    claim_id: str
    run_id: str
    created_at: str
    feedback: list[HumanFeedbackItem]

    @model_validator(mode="after")
    def validate_feedback_ids(self) -> HumanExampleFeedback:
        ids = [item.feedback_id for item in self.feedback]
        if len(ids) != len(set(ids)):
            raise ValueError("feedback ids must be unique")
        return self


class FeedbackRankingAdjustment(StrictBaseModel):
    target_id: str
    target_type: HumanFeedbackTargetType
    target_artifact: str
    feedback_ids: list[str]
    direction: RankingDirection
    weight: RankingWeight
    reason: str

    @model_validator(mode="after")
    def validate_ranking_adjustment(self) -> FeedbackRankingAdjustment:
        if not self.feedback_ids:
            raise ValueError("ranking adjustment requires feedback_ids")
        if len(self.feedback_ids) != len(set(self.feedback_ids)):
            raise ValueError("ranking adjustment feedback_ids must be unique")
        if not self.reason.strip():
            raise ValueError("ranking adjustment reason cannot be empty")
        return self


class FeedbackMemoryEntryPointer(StrictBaseModel):
    memory_entry_id: str
    entry_path: str


class ExampleFeedbackSummary(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    generated_by: Literal["summarize-feedback", "mock_pipeline"]
    feedback_artifact_path: str
    summarized_from: list[str]
    input_artifact_digests: dict[str, str] = Field(default_factory=dict)
    ranking_adjustments: list[FeedbackRankingAdjustment] = Field(default_factory=list)
    project_preferences: list[str] = Field(default_factory=list)
    playbook_tuning_candidates: list[str] = Field(default_factory=list)
    memory_entries: list[FeedbackMemoryEntryPointer] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_summary_references(self) -> ExampleFeedbackSummary:
        if not self.summarized_from:
            raise ValueError("feedback summary requires summarized_from")
        if len(self.summarized_from) != len(set(self.summarized_from)):
            raise ValueError("summarized_from ids must be unique")
        return self


class AgentContract(StrictBaseModel):
    agent_name: str
    input_artifacts: list[str]
    output_artifact: str
    output_schema: str
    failure_modes: list[str]
    must_not: list[str]
    human_gate: Literal["none", "major_decision", "required"]


class AgentContracts(StrictBaseModel):
    project_id: str
    contracts: list[AgentContract]

    @model_validator(mode="after")
    def validate_unique_contracts(self) -> AgentContracts:
        names = [contract.agent_name for contract in self.contracts]
        if len(names) != len(set(names)):
            raise ValueError("agent contract names must be unique")
        return self


class AdmissionCheck(StrictBaseModel):
    project_id: str
    workflow_spec_valid: bool
    agent_contracts_exist: bool
    required_artifacts_declared: bool
    trial_fixture_exists: bool
    human_confirmed: bool
    admitted: bool
    blockers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_admission(self) -> AdmissionCheck:
        required = [
            self.workflow_spec_valid,
            self.agent_contracts_exist,
            self.required_artifacts_declared,
            self.trial_fixture_exists,
            self.human_confirmed,
        ]
        if self.admitted and not all(required):
            raise ValueError("admitted workflow requires all admission checks to pass")
        if self.admitted and self.blockers:
            raise ValueError("admitted workflow cannot have blockers")
        return self


class LoopControl(StrictBaseModel):
    claim_id: str
    cycles_analyzed: int = Field(ge=1)
    no_new_counterexamples_cycles: int = Field(ge=0)
    no_new_references_cycles: int = Field(ge=0)
    grade_delta_last_two: int
    budget_exhausted: bool = False
    should_stop: bool
    stop_reasons: list[str] = Field(default_factory=list)
    next_action: Literal["continue_loop", "run_strategy_brief", "ask_human", "stop"]

    @model_validator(mode="after")
    def validate_stop_rule(self) -> LoopControl:
        no_progress = (
            self.no_new_counterexamples_cycles >= 2
            and self.no_new_references_cycles >= 2
            and self.grade_delta_last_two <= 0
        )
        if (no_progress or self.budget_exhausted) and not self.should_stop:
            raise ValueError("two no-progress cycles or exhausted budget require should_stop=true")
        if self.should_stop and self.next_action == "continue_loop":
            raise ValueError("should_stop=true cannot continue the loop")
        if self.next_action == "stop" and not self.should_stop:
            raise ValueError("next_action=stop requires should_stop=true")
        if self.should_stop and not self.stop_reasons:
            raise ValueError("stopped loops require stop_reasons")
        return self


class LLMAgentAttempt(StrictBaseModel):
    agent_id: str
    role: Literal["prover", "verifier"]
    provider: Literal["openai", "stub", "external"]
    model: str
    created_at: str
    input_artifacts: list[str]
    output_summary: str
    proposed_steps: list[str] = Field(default_factory=list)
    source_requests: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    status: Literal["candidate", "needs_human", "blocked", "error"]


class ScopedObjection(StrictBaseModel):
    """Goal 54B: a reviewer objection bound to a named proof-search target so it
    becomes repairable state, not just prose.

    Kernel-safety contract (advisory only):
    - an objection can only BLOCK or PRIORITIZE repair; it can never prove,
      refute, close, or upgrade any proof/source/evidence/grade/claim status;
    - ``counterexample_pressure`` is unchecked pressure, never a refutation;
    - ``subgoal_id`` / ``proof_step_id`` are UNVERIFIED provenance, deliberately
      NOT hard-reconciled against the live proof graph in ``validate_run`` (Goal-39
      precedent: blocker-derived node ids renumber across rebuilds). Reconciliation
      is done at graph-build time (attach-if-matches); falsely-subgoal-specific free
      text is recorded as route/global instead.
    """

    id: str
    text: str
    scope: Literal["subgoal", "proof_step", "route", "global"]
    scope_source: Literal["explicit", "inferred_target", "legacy_fallback"]
    scope_inferred: bool = False
    inference_reason: str = ""
    subgoal_id: str | None = None
    proof_step_id: str | None = None
    kind: Literal[
        "missing_source",
        "hypothesis_gap",
        "definition_gap",
        "logical_gap",
        "computation_needed",
        "counterexample_pressure",
        "unclear_claim",
        "reviewer_disagreement",
        "other",
    ]
    severity: Literal["P0", "P1", "P2", "P3"]
    suggested_next_action: str = ""

    @model_validator(mode="after")
    def validate_scope_binding(self) -> ScopedObjection:
        # Structural fail-closed: an id may attach only to its matching scope, a
        # subgoal/proof_step scope must name its target, and an inferred scope must
        # justify itself. This is the "bogus scoped id" validate_run catches —
        # without reconciling the id against a renumbering graph.
        if self.subgoal_id is not None and self.scope != "subgoal":
            raise ValueError("scoped_objection subgoal_id requires scope='subgoal'")
        if self.proof_step_id is not None and self.scope != "proof_step":
            raise ValueError("scoped_objection proof_step_id requires scope='proof_step'")
        if self.scope == "subgoal" and not self.subgoal_id:
            raise ValueError("scoped_objection scope='subgoal' requires a subgoal_id")
        if self.scope == "proof_step" and not self.proof_step_id:
            raise ValueError("scoped_objection scope='proof_step' requires a proof_step_id")
        if self.scope_inferred and not self.inference_reason.strip():
            raise ValueError("an inferred scope requires a non-empty inference_reason")
        if self.scope_source == "inferred_target" and not self.scope_inferred:
            raise ValueError("scope_source='inferred_target' requires scope_inferred=true")
        return self


class LLMAgentVerifierReview(StrictBaseModel):
    verifier_id: str
    provider: Literal["openai", "stub", "external"]
    model: str
    created_at: str
    verdict: Literal["plausible_direction", "needs_revision", "needs_sources", "blocked"]
    objections: list[str] = Field(default_factory=list)
    missing_hypotheses: list[str] = Field(default_factory=list)
    source_requests: list[str] = Field(default_factory=list)
    unsafe_language: list[str] = Field(default_factory=list)
    # Goal 54B: additive structured objections bound to proof-search targets.
    # Legacy free-text fields above are preserved; the normalizer also mirrors them
    # into route/global scoped_objections (scope_source='legacy_fallback') so older
    # producers stay valid and downstream has one structured surface.
    scoped_objections: list[ScopedObjection] = Field(default_factory=list)
    recommended_human_action: Literal["continue", "revise", "stop"]
    status: Literal["candidate", "needs_human", "blocked"]


class LLMAgentHumanGate(StrictBaseModel):
    gate_id: str
    status: Literal["pending", "resolved"]
    question: str
    allowed_decisions: list[Literal["continue", "revise", "stop"]]
    recommended_decision: Literal["continue", "revise", "stop"]
    decision: Literal["continue", "revise", "stop"] | None = None
    reason: str | None = None
    human_confirmed: bool = False
    # Provenance of the resolution. 'human' (default, today's behavior) means a
    # real human resolved this gate; 'auto' means machinery resolved it with no
    # human in the loop. Auto provenance can NEVER claim human confirmation:
    # resolved_by == 'auto' forces human_confirmed == false (fail closed, any
    # status) — an auto-resolved gate is honestly recorded as "no human involved".
    resolved_by: Literal["human", "auto"] = "human"
    created_at: str
    resolved_at: str | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> LLMAgentHumanGate:
        """Enforce gate-resolution consistency and honest resolution provenance.

        Only resolved_by == 'human' may claim human confirmation: any other
        provenance value (auto today, anything a future Literal widening might
        add) requires human_confirmed == false regardless of status — the check
        is exhaustive on purpose, not an 'auto' special case. A human-resolved
        gate keeps today's rule: status == resolved requires
        human_confirmed == true.
        """
        if self.human_confirmed and self.resolved_by != "human":
            raise ValueError(
                f"resolved_by={self.resolved_by!r} can never claim human_confirmed=true "
                "(only a real human resolution may)"
            )
        if self.status == "resolved":
            if self.decision is None:
                raise ValueError("resolved human gate requires a decision")
            if self.resolved_by == "human" and not self.human_confirmed:
                raise ValueError("resolved human gate requires human_confirmed=true")
            if not self.resolved_at:
                raise ValueError("resolved human gate requires resolved_at")
        if self.status == "pending":
            if self.decision is not None or self.human_confirmed:
                raise ValueError("pending human gate cannot include a confirmed decision")
        return self


__all__ = [
    'AdmissionCheck',
    'AgentContract',
    'AgentContracts',
    'AlsoConsideredStrategy',
    'ExampleFeedbackSummary',
    'FeedbackMemoryEntryPointer',
    'FeedbackRankingAdjustment',
    'HumanChoice',
    'HumanChoiceOption',
    'HumanExampleFeedback',
    'HumanFeedbackItem',
    'IdeaGrade',
    'LLMAgentAttempt',
    'LLMAgentHumanGate',
    'LLMAgentVerifierReview',
    'LoopControl',
    'Review',
    'ReviewerReport',
    'ScopedObjection',
    'StrategyBrief',
    'StrategyBriefItem',
    'StrategySynthesis',
    'TimeAllocationEntry',
    'TimeAllocationPlan',
    'UpdateInboxEntry',
    'UpdateInboxReport',
    'UpdateInboxType',
]
