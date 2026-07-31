"""Human choices, feedback, agent contracts, loop control."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict, Field, model_validator
from pydantic import Field, model_validator
from typing import Any, Literal
from typing import Literal

from espresso_proof.kernel.models.foundations import ContinueDecision, HumanChoiceStatus, StrictBaseModel


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


__all__ = [
    "AdmissionCheck",
    "AgentContract",
    "AgentContracts",
    "ExampleFeedbackSummary",
    "FeedbackMemoryEntryPointer",
    "FeedbackRankingAdjustment",
    "HumanChoice",
    "HumanExampleFeedback",
    "HumanFeedbackItem",
    "LoopControl",
]
