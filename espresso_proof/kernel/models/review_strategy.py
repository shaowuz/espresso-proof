"""Reviews, idea grade, strategy brief/synthesis, time allocation, update inbox."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict, Field, model_validator
from pydantic import Field, model_validator
from typing import Any, Literal
from typing import Literal

from espresso_proof.kernel.models.foundations import AttemptStatus, ReviewerApplicability, ReviewerVerdict, StrictBaseModel


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
    """provisional scheduling plan from Strategy Grader.

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
    # A human's answer to a scheduler question ("the objections keep coming; do
    # I keep spending?"). Scheduling-only like every other entry: it supersedes
    # the reviewer_objection entries that raised the question so the rebuilt
    # plan stops asking, and it can never touch a mathematical status.
    "human_scheduling_decision",
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

class RouteAllocationEntry(StrictBaseModel):
    """One candidate route's advisory scheduling line.

    Pure scheduling arithmetic derived from the human's strategy feedback +
    choice + the brief's score. ``advisory_score`` orders attention/budget; it is
    NOT a proof/grade/evidence score and never upgrades any status."""

    strategy_id: str
    base_score: int = Field(ge=0, le=100)
    priority: Literal["focus", "normal", "reduce", "repair"] = "normal"
    is_selected: bool = False
    is_most_recent_feedback: bool = False
    advisory_score: int = Field(ge=0, le=100)
    unresolved_blockers: int = Field(ge=0, default=0)
    recommended_allocation: Literal["expand", "repair", "cheap_check"] = "expand"

class HumanTimeAllocationContext(StrictBaseModel):
    """Deterministic, advisory-only time-allocation context derived from
    human strategy feedback.

    It can reorder candidate routes, recommend a per-route allocation mode, and
    feed prompt context. It can NEVER set proof/source/evidence/grade/claim
    status. ``advisory_only`` is locked True and ``forbidden_status_effect`` names
    the statuses this layer must never touch (documentation + a structural check
    that the model carries no such field)."""

    schema_version: int = 1
    advisory_only: Literal[True] = True
    selected_strategy: str | None = None
    routes: list[RouteAllocationEntry] = Field(default_factory=list)
    derived_allocation_hint: str = ""
    forbidden_status_effect: list[str] = Field(
        default_factory=lambda: ["proof", "source", "evidence", "grade", "claim"]
    )

    @model_validator(mode="after")
    def validate_advisory(self) -> HumanTimeAllocationContext:
        # The ranking is advisory: routes are ordered by (selected first, then
        # higher advisory_score, then fewer blockers, then stable id). Enforce the
        # selected-route-first invariant so the packet can never bury the human's
        # explicit pick behind a computed score.
        selected_positions = [
            i for i, route in enumerate(self.routes) if route.is_selected
        ]
        if selected_positions and selected_positions[0] != 0:
            raise ValueError("the human-selected route must rank first")
        if len({route.strategy_id for route in self.routes}) != len(self.routes):
            raise ValueError("routes must have unique strategy_id values")
        return self


__all__ = [
    "AlsoConsideredStrategy",
    "HumanChoiceOption",
    "HumanTimeAllocationContext",
    "IdeaGrade",
    "Review",
    "ReviewerReport",
    "RouteAllocationEntry",
    "StrategyBrief",
    "StrategyBriefItem",
    "StrategySynthesis",
    "TimeAllocationEntry",
    "TimeAllocationPlan",
    "UpdateInboxEntry",
    "UpdateInboxReport",
    "UpdateInboxType",
]
