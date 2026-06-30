"""Kernel data models — file-order slice 4/8. Imported via espresso_proof.kernel.models."""


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
        _validate_literature_candidate_only_meanings(
            self.model_dump(mode="json"),
            artifact_name="Literature Review Agent",
        )
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
    missing_hypothesis: str | None = None
    support: str | None = None
    explanation: str
    repaired_claim_suggestion: str | None = None

    @model_validator(mode="after")
    def validate_refutation_scope(self) -> CounterexampleTest:
        # A counterexample can refute the claim only with a checked 'failed'
        # result: refutes_claim is honest only once the example was actually
        # tested against the claim and the claim FAILED on it. not_tested /
        # inconclusive / survived can never carry a refutation (mirrors the
        # dropped-hypothesis / proof-step guards below; the kernel must enforce
        # this, not rely on agent good behavior).
        if self.refutes_claim and self.result != ExampleStatus.failed:
            raise ValueError(
                "a counterexample can refute the claim only with a checked 'failed' "
                "result; an untested / survived / inconclusive result cannot refute"
            )
        if self.refutes_claim and (self.exposes_missing_hypothesis or self.missing_hypothesis):
            raise ValueError(
                "dropped-hypothesis / missing-hypothesis pressure cannot refute "
                "the original claim"
            )
        if self.satisfies_all_claim_hypotheses is False and self.refutes_claim:
            raise ValueError("dropped-hypothesis tests cannot refute the original claim")
        if self.satisfies_all_claim_hypotheses is not True and self.refutes_claim:
            raise ValueError(
                "a counterexample can refute the claim only when every claim hypothesis "
                "is explicitly satisfied; unclear admissibility cannot refute"
            )
        if self.refutes_step_only and self.refutes_claim:
            raise ValueError("a proof-step attack cannot refute the original claim")
        if self.refutes_claim and self.target != "theorem":
            raise ValueError("claim refutation requires target='theorem'")
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

    @model_validator(mode="after")
    def validate_unique_bindings(self) -> SourceGroundingReport:
        ref_ids = [ref.id for ref in self.source_refs]
        if len(ref_ids) != len(set(ref_ids)):
            raise ValueError("source_grounding source_ref ids must be unique")
        step_ids = [step.proof_step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("source_grounding proof_step_id values must be unique")
        for step in self.steps:
            if len(step.source_refs) != len(set(step.source_refs)):
                raise ValueError(
                    f"source_grounding step {step.proof_step_id!r} source_refs must be unique"
                )
        return self


class TheoremApplicabilityCondition(StrictBaseModel):
    id: str
    text: str
    condition_type: Literal[
        "object",
        "morphism",
        "base_field",
        "genericity",
        "smoothness",
        "properness",
        "flatness",
        "coherence",
        "definition",
        "other",
    ]
    status: Literal["satisfied", "missing", "mismatched", "unclear", "not_checked"]
    evidence: str
    definition_locator: Locator | None = None


class TheoremApplicabilityUsage(StrictBaseModel):
    proof_step_id: str
    source_ref_id: str
    theorem_id: str | None = None
    theorem_label: str | None = None
    theorem_statement: str
    locator: Locator
    source_status: SourceStatus
    conclusion_status: Literal["compatible", "weaker", "stronger", "orthogonal", "contradicts", "unclear"]
    conditions: list[TheoremApplicabilityCondition]
    applicability_status: Literal[
        "applicable",
        "blocked",
        "misapplied",
        "unclear",
        "needs_human_assumption",
        "wrong_source",
    ]
    blocks_source_backed: bool
    claim_scope: Literal["universal", "restricted", "unclear"] = "unclear"
    theorem_scope: Literal["universal", "very_general", "generic", "restricted", "unclear"] = "unclear"
    scope_justification: str = ""
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_condition_status(self) -> TheoremApplicabilityUsage:
        bad_conditions = {"missing", "mismatched", "unclear", "not_checked"}
        has_bad_condition = any(condition.status in bad_conditions for condition in self.conditions)
        if self.applicability_status == "applicable":
            if self.source_status != SourceStatus.source_backed:
                raise ValueError("applicable theorem usage requires source_backed source status")
            if not self.conditions:
                raise ValueError("applicable theorem usage requires non-empty condition coverage")
            if has_bad_condition:
                raise ValueError("applicable theorem usage requires all conditions satisfied")
            if self.conclusion_status != "compatible":
                raise ValueError("applicable theorem usage requires compatible conclusion")
            if self.blocks_source_backed:
                raise ValueError("applicable theorem usage cannot block source_backed")
        if has_bad_condition and not self.blocks_source_backed:
            raise ValueError("unsatisfied theorem conditions must block source_backed")
        if self.source_status != SourceStatus.source_backed and not self.blocks_source_backed:
            raise ValueError("non-source-backed theorem usage must block source_backed")
        return self


class TheoremApplicabilityReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    source_grounding_artifact: str
    literature_review_agent_artifact: str | None = None
    usages: list[TheoremApplicabilityUsage]
    # Goal 57A slice-2: default-deny usages derived from the advisory theorem
    # condition ledger (reviewer source_requests). These are NOT source-grounding
    # usages: they never map to a proof step, never earn source_backed, and (being
    # default-deny) are NEVER ``applicable`` unless a condition is explicitly
    # ``matched`` AND the locator is exact. Their only safety effect is to forbid
    # ``all_applicable`` while any ledger obligation is unresolved. Additive and
    # optional — absent/empty means behaviour is unchanged.
    ledger_usages: list[TheoremApplicabilityUsage] = Field(default_factory=list)
    overall_status: Literal[
        "all_applicable",
        "blocked",
        "misapplied",
        "unclear",
        "needs_human_assumption",
        "wrong_source",
    ]
    notes: list[str] = Field(default_factory=list)
    # Goal 41: sha256 of source_grounding.yaml's bytes AT AUDIT TIME. Optional/
    # additive: when set, the run validator requires the current file to match —
    # editing the grounding after the audit (without re-running it) fails
    # closed instead of leaving a stale "applicable" verdict standing.
    source_grounding_sha256: str = ""

    @model_validator(mode="after")
    def validate_overall_status(self) -> TheoremApplicabilityReport:
        proof_step_ids = [usage.proof_step_id for usage in self.usages]
        if len(proof_step_ids) != len(set(proof_step_ids)):
            raise ValueError("theorem applicability usages must have unique proof_step_id values")
        if self.overall_status == "all_applicable":
            if any(usage.applicability_status != "applicable" for usage in self.usages):
                raise ValueError("all_applicable requires every theorem usage applicable")
            # Goal 57A: a ledger obligation that is not fully resolved (non-exact
            # locator or any non-satisfied condition surfaces as a non-applicable
            # ledger usage) must forbid all_applicable. Default-deny, fail closed.
            if any(usage.applicability_status != "applicable" for usage in self.ledger_usages):
                raise ValueError(
                    "all_applicable requires every theorem condition ledger usage applicable"
                )
        if any(usage.applicability_status == "misapplied" for usage in self.usages):
            if self.overall_status != "misapplied":
                raise ValueError("misapplied theorem usage requires overall_status=misapplied")
        if any(usage.applicability_status == "wrong_source" for usage in self.usages):
            if self.overall_status not in {"wrong_source", "misapplied"}:
                raise ValueError("wrong_source theorem usage requires overall_status=wrong_source or misapplied")
        if any(usage.applicability_status == "needs_human_assumption" for usage in self.usages):
            if self.overall_status not in {"needs_human_assumption", "blocked", "misapplied", "wrong_source"}:
                raise ValueError("needs_human_assumption usage requires a blocking overall status")
        if any(usage.applicability_status == "blocked" for usage in self.usages):
            if self.overall_status not in {"blocked", "misapplied", "wrong_source", "needs_human_assumption"}:
                raise ValueError("blocked theorem usage requires blocked or misapplied overall status")
        return self


class TheoremApplicabilityReviewRecord(StrictBaseModel):
    reviewer_id: Literal["source_match_reviewer", "hypothesis_reviewer"]
    proof_step_id: str | None
    verdict: Literal[
        "applicable",
        "blocked",
        "needs_human_assumption",
        "wrong_source",
        "unclear",
    ]
    conclusion_status: Literal["compatible", "weaker", "stronger", "orthogonal", "contradicts", "unclear"]
    conditions: list[TheoremApplicabilityCondition] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TheoremApplicabilityReviewArtifact(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    reviewers: list[Literal["source_match_reviewer", "hypothesis_reviewer"]]
    reviews: list[TheoremApplicabilityReviewRecord]
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_reviewers(self) -> TheoremApplicabilityReviewArtifact:
        if self.reviewers != ["source_match_reviewer", "hypothesis_reviewer"]:
            raise ValueError("theorem_applicability_review must record both reviewers in order")
        return self


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


__all__ = [
    'ComparisonUncertainty',
    'ConclusionMatch',
    'CounterexampleReport',
    'CounterexampleTest',
    'DefinitionRef',
    'LiteratureRelevanceSummary',
    'LiteratureReviewAgentReport',
    'LiteratureSourceAccess',
    'LiteratureUncertaintySummary',
    'ReviewedSource',
    'ReviewedTheorem',
    'ReviewedTheoremHypothesis',
    'SourceGroundingReport',
    'SourceGroundingStep',
    'StrongestLiteratureSource',
    'TestReport',
    'TestReportItem',
    'TheoremApplicabilityCondition',
    'TheoremApplicabilityReport',
    'TheoremApplicabilityReviewArtifact',
    'TheoremApplicabilityReviewRecord',
    'TheoremApplicabilityUsage',
    'TheoremClaimComparison',
    'TheoremExtractionReport',
    'TheoremHypothesisComparison',
    'TheoremRelevance',
    'TheoremStatementSpan',
]
