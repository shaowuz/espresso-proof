from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FlexibleBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class SourceStatus(str, Enum):
    source_backed = "source_backed"
    candidate = "candidate"
    unverified = "unverified"
    misapplied = "misapplied"
    not_found = "not_found"


class ExampleStatus(str, Enum):
    survived = "survived"
    failed = "failed"
    inconclusive = "inconclusive"
    not_tested = "not_tested"


class ComputationStatus(str, Enum):
    checked = "checked"
    source_checked = "source_checked"
    inconclusive = "inconclusive"
    wrong = "wrong"
    human_needed = "human_needed"


class IdeaStatus(str, Enum):
    promising = "promising"
    fragile = "fragile"
    likely_false = "likely_false"
    refuted = "refuted"
    unclear = "unclear"


class ProofLanguageStatus(str, Enum):
    idea_sketch = "idea_sketch"
    proof_candidate = "proof_candidate"
    source_checked_argument = "source_checked_argument"
    computation_checked = "computation_checked"
    lean_checked = "lean_checked"
    human_confirmed = "human_confirmed"


class ExplorationMode(str, Enum):
    quick_test = "quick_test"
    normal_pass = "normal_pass"
    deep_exploration = "deep_exploration"
    long_watch = "long_watch"


class HumanChoiceStatus(str, Enum):
    pending = "pending"
    selected = "selected"
    declined = "declined"


class ContinueDecision(str, Enum):
    pending = "pending"
    continue_run = "continue"
    stop = "stop"
    revise = "revise"


class HumanFeedbackLabel(str, Enum):
    interesting = "interesting"
    canonical = "canonical"
    dangerous = "dangerous"
    misleading = "misleading"
    too_easy = "too_easy"
    irrelevant = "irrelevant"
    needs_source = "needs_source"
    needs_computation = "needs_computation"
    favorite = "favorite"


class HumanFeedbackScope(str, Enum):
    current_run = "current_run"
    project = "project"
    domain = "domain"
    personal_global = "personal_global"


class HumanFeedbackTargetType(str, Enum):
    example = "example"
    counterexample_test = "counterexample_test"
    test = "test"
    source_ref = "source_ref"
    source_step = "source_step"
    strategy = "strategy"


class HumanFeedbackAction(str, Enum):
    prioritize_counterexample_testing = "prioritize_counterexample_testing"
    run_referencing = "run_referencing"
    run_evidence_auditor = "run_evidence_auditor"
    promote_ranking = "promote_ranking"
    demote_ranking = "demote_ranking"
    save_to_memory = "save_to_memory"
    request_claim_review = "request_claim_review"
    request_rubric_update = "request_rubric_update"


class RankingDirection(str, Enum):
    up = "up"
    down = "down"


class RankingWeight(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class HypothesisStatus(str, Enum):
    explicit = "explicit"
    inferred = "inferred"
    missing = "missing"
    unclear = "unclear"


class ReviewerVerdict(str, Enum):
    passed = "pass"
    failed = "fail"
    unclear = "unclear"


class ReviewerApplicability(str, Enum):
    applicable = "applicable"
    sketch_only = "sketch_only"
    not_applicable = "not_applicable"


class AttemptStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    unclear = "unclear"


class Locator(StrictBaseModel):
    kind: Literal[
        "theorem",
        "proposition",
        "lemma",
        "corollary",
        "definition",
        "section",
        "page",
        "arxiv_id",
        "url",
        "unknown",
    ]
    value: str

    @property
    def is_exact(self) -> bool:
        return self.kind != "unknown" and self.value.strip().lower() not in {
            "",
            "unknown",
            "to_be_filled",
            "tbd",
        }


class HypothesisMatch(StrictBaseModel):
    matches: bool | Literal["unclear"]
    mismatches: list[str] = Field(default_factory=list)


class SourceRef(StrictBaseModel):
    id: str
    type: Literal["paper", "book", "local_note", "theorem_database", "mathlib", "web_page"]
    title: str
    authors: list[str] = Field(default_factory=list)
    locator: Locator
    statement_or_summary: str
    hypothesis_match: HypothesisMatch
    confidence: Literal["high", "medium", "low"]
    status: SourceStatus

    @model_validator(mode="after")
    def validate_source_backed(self) -> SourceRef:
        if self.status == SourceStatus.source_backed:
            if not self.locator.is_exact:
                raise ValueError("source_backed requires an exact locator")
            if self.hypothesis_match.matches is not True:
                raise ValueError("source_backed requires matching hypotheses")
        return self


class Hypothesis(StrictBaseModel):
    name: str
    status: HypothesisStatus
    source: Literal["user", "agent", "source"]


class Claim(StrictBaseModel):
    id: str
    raw_text: str
    normalized_statement: str
    objects: list[str]
    hypotheses: list[Hypothesis]
    conclusion: str
    ambiguous_terms: list[str] = Field(default_factory=list)
    locked: bool
    repaired_from: str | None = None
    human_confirmed: bool = False

    @model_validator(mode="after")
    def validate_locked(self) -> Claim:
        if not self.locked:
            raise ValueError("P0 critique requires a locked claim")
        return self


class HypothesisLedger(StrictBaseModel):
    claim_id: str
    hypotheses: list[Hypothesis]
    missing_or_unclear: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class InvariantValue(StrictBaseModel):
    value: str
    status: ComputationStatus
    source_ref: str | None = None


class ExampleHypotheses(StrictBaseModel):
    smooth: bool | Literal["unknown"]
    projective: bool | Literal["unknown"]
    proper: bool | Literal["unknown"]
    base_field: str | Literal["unknown"]
    characteristic: str | Literal["unknown"]
    additional: list[str] = Field(default_factory=list)


class ExampleRecord(StrictBaseModel):
    id: str
    name: str
    domain: str
    role: Literal["toy", "classical", "canonical", "boundary", "pathology", "counterexample_in_literature", "open_case"]
    source_refs: list[str] = Field(default_factory=list)
    hypotheses: ExampleHypotheses
    known_invariants: dict[str, InvariantValue] = Field(default_factory=dict)
    computable_fields: list[str] = Field(default_factory=list)
    what_it_tests: list[str]
    claim_applicability: Literal["applies", "does_not_apply", "unclear"]
    notes: str = ""


class ExampleBank(StrictBaseModel):
    version: float | str
    project_id: str
    purpose: str
    source_refs: list[SourceRef]
    examples: list[ExampleRecord]


class TestedExample(StrictBaseModel):
    id: str
    template_id: str
    name: str
    satisfies_all_claim_hypotheses: bool | Literal["unclear"]
    hypothesis_mismatches: list[str] = Field(default_factory=list)
    what_it_tests: list[str]
    expected_conclusion: str
    observed_or_inferred_behavior: str
    result_for_current_idea: ExampleStatus
    source_or_reason: str
    nontriviality: Literal["routine", "standard", "literature_counterexample", "research_level"]


class ExampleSelection(StrictBaseModel):
    claim_id: str
    examples: list[TestedExample]


class ExampleComputationCheck(StrictBaseModel):
    id: str
    example_id: str
    claim_id: str
    computed_quantity: str
    input_data: list[str] = Field(default_factory=list)
    method: Literal["exact_arithmetic", "CAS", "lean", "source_lookup", "manual_review_needed"]
    tool_or_source: str
    result: str
    cross_check: list[str] = Field(default_factory=list)
    status: ComputationStatus
    notes: str = ""

    @model_validator(mode="after")
    def validate_evidence_method(self) -> ExampleComputationCheck:
        evidence_statuses = {ComputationStatus.checked, ComputationStatus.source_checked}
        if self.status in evidence_statuses and self.method == "manual_review_needed":
            raise ValueError("manual_review_needed cannot support checked/source_checked evidence")
        if self.status == ComputationStatus.source_checked and self.method != "source_lookup":
            raise ValueError("source_checked evidence must use source_lookup")
        return self


class ComputationChecks(StrictBaseModel):
    claim_id: str
    checks: list[ExampleComputationCheck]

    @model_validator(mode="after")
    def validate_nested_claim_ids(self) -> ComputationChecks:
        mismatches = [
            check.id
            for check in self.checks
            if check.claim_id != self.claim_id
        ]
        if mismatches:
            raise ValueError(
                f"computation check claim_id mismatch: {sorted(mismatches)}"
            )
        return self


class LiteratureItem(StrictBaseModel):
    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    locator: Locator
    relevance: Literal[
        "core",
        "background",
        "example_source",
        "counterexample_source",
        "possible_theorem_source",
    ]
    status: SourceStatus
    why_relevant: str
    found_by: Literal["user", "literature_review", "memory", "local_notes", "web_search"]

    @model_validator(mode="after")
    def validate_literature_status(self) -> LiteratureItem:
        if self.status == SourceStatus.source_backed:
            raise ValueError("Literature Review may only propose candidates; Referencing decides source_backed")
        if self.status == SourceStatus.misapplied:
            raise ValueError("misapplied is a Referencing judgment, not a Literature Review status")
        return self


class LiteratureReviewReport(StrictBaseModel):
    claim_id: str
    query_summary: str
    candidate_references: list[LiteratureItem] = Field(default_factory=list)
    known_examples: list[str] = Field(default_factory=list)
    known_counterexample_families: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ExtractedTheorem(StrictBaseModel):
    id: str
    source_ref_id: str
    kind: Literal["theorem", "proposition", "lemma", "corollary", "definition", "remark"]
    label: str
    locator: Locator
    statement: str
    context: str = ""
    hypothesis_terms: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]


class TheoremExtractionReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    generated_at: str
    sources: list[SourceRef]
    theorems: list[ExtractedTheorem]
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_theorem_source_refs(self) -> TheoremExtractionReport:
        source_ids = {source.id for source in self.sources}
        unknown = {
            theorem.source_ref_id
            for theorem in self.theorems
            if theorem.source_ref_id not in source_ids
        }
        if unknown:
            raise ValueError(f"theorems reference unknown sources: {sorted(unknown)}")
        return self


class LiteratureSourceAccess(StrictBaseModel):
    status: Literal["full_text_read", "abstract_only", "metadata_only", "unreadable", "unavailable"]
    access_path: str | None = None
    pages_or_sections_read: list[str] = Field(default_factory=list)
    extraction_method: Literal["pdf_text", "tex_source", "html", "plain_text", "metadata", "human_supplied", "unknown"]


class ReviewedSource(StrictBaseModel):
    source_ref: SourceRef
    access: LiteratureSourceAccess
    bibliographic_confidence: Literal["high", "medium", "low"]

    @model_validator(mode="after")
    def validate_review_source_status(self) -> ReviewedSource:
        if self.source_ref.status in {SourceStatus.source_backed, SourceStatus.misapplied}:
            raise ValueError("literature review agent may not emit source_backed or misapplied sources")
        return self


class TheoremStatementSpan(StrictBaseModel):
    page: str | None = None
    section: str | None = None
    char_start: int | None = None
    char_end: int | None = None


class DefinitionRef(StrictBaseModel):
    source_ref_id: str
    locator: Locator
    definition_text: str


class ReviewedTheoremHypothesis(StrictBaseModel):
    id: str
    text: str
    normalized: str
    definition_refs: list[DefinitionRef] = Field(default_factory=list)
    status: Literal["explicit", "inferred", "implicit", "unclear"]


class ReviewedTheorem(StrictBaseModel):
    id: str
    source_ref_id: str
    kind: Literal["theorem", "proposition", "lemma", "corollary", "definition", "remark"]
    label: str
    locator: Locator
    statement_exact: str
    statement_is_verbatim: bool
    statement_span: TheoremStatementSpan = Field(default_factory=TheoremStatementSpan)
    theorem_hypotheses: list[ReviewedTheoremHypothesis] = Field(default_factory=list)
    theorem_conclusion: str
    extraction_confidence: Literal["high", "medium", "low"]


class TheoremHypothesisComparison(StrictBaseModel):
    claim_hypothesis: str
    theorem_hypothesis_id: str | None = None
    relation: Literal[
        "same",
        "stronger",
        "weaker",
        "missing_in_theorem",
        "extra_in_theorem",
        "incompatible",
        "unclear",
    ]
    reason: str
    support_locator: Locator | None = None


class ConclusionMatch(StrictBaseModel):
    relation: Literal["same", "stronger", "weaker", "orthogonal", "incompatible", "unclear"]
    reason: str


class TheoremRelevance(StrictBaseModel):
    role: Literal[
        "key_theorem",
        "supporting_lemma",
        "definition_source",
        "example_source",
        "counterexample_source",
        "background",
    ]
    summary: str


class ComparisonUncertainty(StrictBaseModel):
    confidence: Literal["high", "medium", "low"]
    failure_modes: list[
        Literal[
            "missing_exact_locator",
            "unread_source",
            "ambiguous_statement",
            "missing_definition",
            "hypothesis_mismatch",
            "translation_unclear",
            "weak_term_match",
            "no_theorem_found",
        ]
    ] = Field(default_factory=list)
    explanation: str

    @model_validator(mode="after")
    def validate_uncertainty_detail(self) -> ComparisonUncertainty:
        if self.confidence != "high" and not self.failure_modes:
            raise ValueError("medium/low comparison uncertainty must name at least one failure mode")
        return self


class TheoremClaimComparison(StrictBaseModel):
    theorem_id: str
    claim_id: str
    comparison_status: Literal[
        "applicable",
        "partially_applicable",
        "stronger_than_needed",
        "weaker_than_needed",
        "contradicts",
        "unclear",
    ]
    hypothesis_matches: list[TheoremHypothesisComparison] = Field(default_factory=list)
    conclusion_match: ConclusionMatch
    relevance: TheoremRelevance
    uncertainty: ComparisonUncertainty

    @model_validator(mode="after")
    def validate_applicable_comparison(self) -> TheoremClaimComparison:
        if self.comparison_status == "applicable":
            bad_relations = {"missing_in_theorem", "incompatible", "unclear"}
            if any(match.relation in bad_relations for match in self.hypothesis_matches):
                raise ValueError("applicable comparison requires non-unclear matching hypotheses")
            if self.conclusion_match.relation in {"orthogonal", "incompatible", "unclear"}:
                raise ValueError("applicable comparison requires a compatible conclusion")
        return self


class StrongestLiteratureSource(StrictBaseModel):
    source_ref_id: str
    theorem_id: str | None = None
    reason: str


class LiteratureRelevanceSummary(StrictBaseModel):
    overall_status: Literal[
        "supports_route",
        "contradicts_route",
        "background_only",
        "no_relevant_source",
        "unclear",
    ]
    strongest_sources: list[StrongestLiteratureSource] = Field(default_factory=list)
    main_blockers: list[str] = Field(default_factory=list)


class LiteratureUncertaintySummary(StrictBaseModel):
    overall_confidence: Literal["high", "medium", "low"]
    blocking_uncertainties: list[str] = Field(default_factory=list)
    needs_human_review: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_uncertainty_summary(self) -> LiteratureUncertaintySummary:
        if self.overall_confidence != "high" and not (
            self.blocking_uncertainties or self.needs_human_review
        ):
            raise ValueError("medium/low literature uncertainty must include concrete blockers")
        return self


class LiteratureReviewAgentReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    generated_at: str
    input_sources: list[ReviewedSource]
    extracted_theorems: list[ReviewedTheorem] = Field(default_factory=list)
    theorem_comparisons: list[TheoremClaimComparison] = Field(default_factory=list)
    relevance_summary: LiteratureRelevanceSummary
    uncertainty: LiteratureUncertaintySummary
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_literature_review_agent_refs(self) -> LiteratureReviewAgentReport:
        source_by_id = {source.source_ref.id: source for source in self.input_sources}
        unknown_theorem_sources = {
            theorem.source_ref_id
            for theorem in self.extracted_theorems
            if theorem.source_ref_id not in source_by_id
        }
        if unknown_theorem_sources:
            raise ValueError(f"reviewed theorems reference unknown sources: {sorted(unknown_theorem_sources)}")
        theorem_by_id = {theorem.id: theorem for theorem in self.extracted_theorems}
        unknown_comparisons = {
            comparison.theorem_id
            for comparison in self.theorem_comparisons
            if comparison.theorem_id not in theorem_by_id
        }
        if unknown_comparisons:
            raise ValueError(f"comparisons reference unknown theorems: {sorted(unknown_comparisons)}")
        for theorem in self.extracted_theorems:
            source = source_by_id[theorem.source_ref_id]
            if theorem.statement_is_verbatim and (
                source.access.status != "full_text_read" or not theorem.locator.is_exact
            ):
                raise ValueError("verbatim theorem statements require full text access and an exact locator")
        return self


class CounterexampleTest(StrictBaseModel):
    id: str
    operator_id: str
    example_id: str
    satisfies_all_claim_hypotheses: bool | Literal["unclear"]
    target: Literal["theorem", "proof_step", "hypothesis", "analogy"]
    result: ExampleStatus
    refutes_claim: bool
    refutes_step_only: bool
    exposes_missing_hypothesis: bool
    explanation: str
    repaired_claim_suggestion: str | None = None

    @model_validator(mode="after")
    def validate_refutation_scope(self) -> CounterexampleTest:
        if self.satisfies_all_claim_hypotheses is False and self.refutes_claim:
            raise ValueError("dropped-hypothesis tests cannot refute the original claim")
        return self


class CounterexampleReport(StrictBaseModel):
    claim_id: str
    tests: list[CounterexampleTest]


class SourceGroundingStep(StrictBaseModel):
    proof_step_id: str
    needed_theorem: str
    status: SourceStatus
    source_refs: list[str] = Field(default_factory=list)
    hypothesis_match: HypothesisMatch
    note: str


class SourceGroundingReport(StrictBaseModel):
    claim_id: str
    source_refs: list[SourceRef] = Field(default_factory=list)
    steps: list[SourceGroundingStep]


class TestReportItem(StrictBaseModel):
    id: str
    description: str
    uses_examples: list[str] = Field(default_factory=list)
    uses_counterexamples: list[str] = Field(default_factory=list)
    result: ExampleStatus
    evidence_status: ComputationStatus
    main_observation: str
    next_action: str


class TestReport(StrictBaseModel):
    claim_id: str
    target: Literal["claim", "user_sketch", "proof_step", "strategy"]
    tests: list[TestReportItem]
    summary: str


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


class HumanChoice(StrictBaseModel):
    claim_id: str
    status: HumanChoiceStatus
    selected_strategy: str | None = None
    effort: ExplorationMode | None = None
    taste_judgment: str = ""
    continue_decision: ContinueDecision
    human_confirmed: bool = False

    @model_validator(mode="after")
    def validate_choice_state(self) -> HumanChoice:
        if self.status == HumanChoiceStatus.selected:
            if not self.selected_strategy:
                raise ValueError("selected human choice requires selected_strategy")
            if self.effort is None:
                raise ValueError("selected human choice requires effort")
            if self.continue_decision == ContinueDecision.pending:
                raise ValueError("selected human choice requires a non-pending continue_decision")
            if not self.human_confirmed:
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
    created_at: str
    resolved_at: str | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> LLMAgentHumanGate:
        if self.status == "resolved":
            if self.decision is None:
                raise ValueError("resolved human gate requires a decision")
            if not self.human_confirmed:
                raise ValueError("resolved human gate requires human_confirmed=true")
            if not self.resolved_at:
                raise ValueError("resolved human gate requires resolved_at")
        if self.status == "pending":
            if self.decision is not None or self.human_confirmed:
                raise ValueError("pending human gate cannot include a confirmed decision")
        return self


class LLMAgentProviderDiagnostic(StrictBaseModel):
    provider: Literal["openai", "external"]
    model: str
    api_key_env: str | None = None
    api_key_present: bool | None = None
    command_env: str | None = None
    command_present: bool | None = None
    endpoint: str | None = None
    network_check: Literal["skipped", "not_run", "passed", "failed"]
    status: Literal["missing_key", "missing_command", "configured", "ready", "error"]
    message: str
    structured_output: dict[str, Any]
    sample_verdict: str | None = None
    error: str | None = None


class LLMAgentRunReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    provider: Literal["openai", "stub", "external"]
    model: str
    input_artifacts: list[str]
    prover_attempt: LLMAgentAttempt
    verifier_reviews: list[LLMAgentVerifierReview]
    human_gate: LLMAgentHumanGate
    provider_diagnostic: LLMAgentProviderDiagnostic | None = None
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_agent_report(self) -> LLMAgentRunReport:
        if self.prover_attempt.role != "prover":
            raise ValueError("prover_attempt must have role=prover")
        if not self.verifier_reviews:
            raise ValueError("LLM agent run requires at least one verifier review")
        return self


class ProverLoopReviewPointer(StrictBaseModel):
    verifier_id: str
    verdict: Literal["plausible_direction", "needs_revision", "needs_sources", "blocked"]
    objection_count: int = Field(ge=0)
    recommended_human_action: Literal["continue", "revise", "stop"]


class ProverLoopAttempt(StrictBaseModel):
    attempt_id: str
    round_index: int = Field(ge=1)
    strategy_id: str
    parent_attempt_id: str | None = None
    prover_attempt_id: str
    source_request_count: int = Field(ge=0)
    risk_count: int = Field(ge=0)
    reviewer_ids: list[str]
    reviewer_objections: list[ProverLoopReviewPointer]
    time_allocation: Literal["expand", "repair", "cheap_check", "pause", "ask_human", "stop"]
    status: Literal["candidate", "needs_repair", "blocked", "waiting_human", "stopped"]
    blockers: list[str] = Field(default_factory=list)
    next_action: str

    @model_validator(mode="after")
    def validate_attempt_reviewers(self) -> ProverLoopAttempt:
        if not self.reviewer_ids:
            raise ValueError("prover loop attempt requires reviewer_ids")
        if len(self.reviewer_ids) != len(set(self.reviewer_ids)):
            raise ValueError("prover loop attempt reviewer_ids must be unique")
        pointer_ids = [review.verifier_id for review in self.reviewer_objections]
        if len(pointer_ids) != len(set(pointer_ids)):
            raise ValueError("reviewer_objections verifier_id values must be unique")
        if set(pointer_ids) != set(self.reviewer_ids):
            raise ValueError("reviewer_objections must match reviewer_ids")
        if self.status in {"needs_repair", "blocked"} and not self.blockers:
            raise ValueError("blocked/repair attempts require blockers")
        return self


class ProverLoopBudget(StrictBaseModel):
    approved_effort: ExplorationMode
    max_rounds: int = Field(ge=1)
    rounds_used: int = Field(ge=0)
    max_agent_calls: int = Field(ge=1)
    agent_calls_used: int = Field(ge=0)
    stop_if_human_gate_pending: bool = True

    @model_validator(mode="after")
    def validate_budget_use(self) -> ProverLoopBudget:
        if self.rounds_used > self.max_rounds:
            raise ValueError("rounds_used cannot exceed max_rounds")
        if self.agent_calls_used > self.max_agent_calls:
            raise ValueError("agent_calls_used cannot exceed max_agent_calls")
        return self


class ProverLoopReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    source_llm_agent_run: str
    selected_strategy_id: str | None = None
    budget: ProverLoopBudget
    attempts: list[ProverLoopAttempt]
    human_gate_status: Literal["pending", "resolved"]
    loop_decision: Literal["continue", "repair", "switch_strategy", "ask_human", "stop"]
    decision_reason: str
    audit_note: str

    @model_validator(mode="after")
    def validate_loop_report(self) -> ProverLoopReport:
        if not self.attempts:
            raise ValueError("prover loop report requires at least one attempt")
        attempt_ids = [attempt.attempt_id for attempt in self.attempts]
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("prover loop attempt ids must be unique")
        if self.budget.rounds_used != len({attempt.round_index for attempt in self.attempts}):
            raise ValueError("rounds_used must match distinct attempt round_index values")
        if self.human_gate_status == "pending" and self.loop_decision not in {"ask_human", "stop"}:
            raise ValueError("pending human gate requires ask_human or stop loop decision")
        return self


class MemoryEntry(StrictBaseModel):
    id: str
    timestamp: str
    project: str
    type: Literal["failed_attempt", "tested_example", "counterexample", "source_gap", "repaired_claim", "user_decision"]
    claim_id: str
    summary: str
    provenance: list[str]
    reusable_lesson: str


class MemoryIndexEntry(StrictBaseModel):
    id: str
    entry_path: str
    claim_id: str
    type: Literal["failed_attempt", "tested_example", "counterexample", "source_gap", "repaired_claim", "user_decision"]
    tags: list[str] = Field(default_factory=list)
    summary: str
    provenance: list[str]


class MemoryIndex(StrictBaseModel):
    project_id: str
    last_updated: str
    lookup_keys: list[str] = Field(default_factory=list)
    entries: list[MemoryIndexEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_entries(self) -> MemoryIndex:
        ids = [entry.id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("memory index entry ids must be unique")
        return self


class RunManifest(StrictBaseModel):
    run_id: str
    project_id: str
    created_at: str
    current_state: str
    input_file: str
    artifacts: dict[str, str]
    optional_artifacts: dict[str, str] = Field(default_factory=dict)
    dependencies: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    retries: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_required_artifacts(self) -> RunManifest:
        required = {
            "locked_claim",
            "agent_contracts",
            "hypothesis_ledger",
            "literature_review",
            "examples",
            "computation_checks",
            "counterexamples",
            "tests",
            "source_grounding",
            "review",
            "idea_grade",
            "loop_control",
            "strategy_synthesis",
            "strategy_brief",
            "human_choice",
            "final_report",
        }
        missing = required.difference(self.artifacts)
        if missing:
            raise ValueError(f"missing run artifacts: {sorted(missing)}")
        return self


class CoreCorpusItem(StrictBaseModel):
    id: str
    title: str
    url: str
    role: Literal["core", "background", "example_source", "counterexample_source"]


class WorkflowSpec(FlexibleBaseModel):
    name: str
    mode: str
    status: Literal["draft", "human_confirmed", "deprecated"]
    domain: str
    core_corpus: list[CoreCorpusItem]
    agent_flow: list[str]
    default_critique_flow: list[str]
    required_examples: list[str]
    canonical_example_templates: list[str]
    pathology_templates: list[str]
    test_operators: list[str]
    non_goals: list[str]

    @model_validator(mode="after")
    def validate_non_goals(self) -> WorkflowSpec:
        joined = " ".join(self.non_goals).lower()
        if "lean formalization" not in joined:
            raise ValueError("workflow must explicitly reject Lean formalization as a default goal")
        if not self.canonical_example_templates:
            raise ValueError("workflow requires canonical example templates")
        if not self.test_operators:
            raise ValueError("workflow requires test operators")
        return self


MODEL_REGISTRY: dict[str, type[BaseModel]] = {
    "claim": Claim,
    "agent_contracts": AgentContracts,
    "admission_check": AdmissionCheck,
    "hypothesis_ledger": HypothesisLedger,
    "literature_review": LiteratureReviewReport,
    "literature_review_agent": LiteratureReviewAgentReport,
    "example_bank": ExampleBank,
    "examples": ExampleSelection,
    "computation_checks": ComputationChecks,
    "counterexamples": CounterexampleReport,
    "tests": TestReport,
    "source_grounding": SourceGroundingReport,
    "theorem_extraction": TheoremExtractionReport,
    "review": Review,
    "idea_grade": IdeaGrade,
    "loop_control": LoopControl,
    "strategy_synthesis": StrategySynthesis,
    "strategy_brief": StrategyBrief,
    "human_choice": HumanChoice,
    "human_example_feedback": HumanExampleFeedback,
    "example_feedback_summary": ExampleFeedbackSummary,
    "llm_agent_run": LLMAgentRunReport,
    "prover_loop": ProverLoopReport,
    "memory_entry": MemoryEntry,
    "memory_index": MemoryIndex,
    "run_manifest": RunManifest,
    "workflow_spec": WorkflowSpec,
    "source_ref": SourceRef,
    "extracted_theorem": ExtractedTheorem,
    "example_computation_check": ExampleComputationCheck,
}
