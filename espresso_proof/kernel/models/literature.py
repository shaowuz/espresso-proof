"""Literature retrieval, review, extraction, and candidate sources."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict, Field, model_validator
from pydantic import Field, model_validator
from typing import Any, Literal
from typing import Literal

from espresso_proof.kernel.models.foundations import SourceStatus, StrictBaseModel


class RetrievedSourceStatus(str, Enum):
    """Status a retrieval step may assign. There is deliberately NO ``source_backed``
    value: retrieval can only surface candidates. Only Source Grounding
    (exact locator + hypothesis match + attribution) earns source_backed."""

    candidate = "candidate"
    unverified = "unverified"
    not_found = "not_found"

class RetrievedSourceHit(StrictBaseModel):
    """A candidate source surfaced by retrieval. Carries no source_backed status;
    a short attributed snippet only (<=240 chars), never full verbatim text."""

    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    identifier: str = ""
    locator: Locator
    provider: str
    relevance: float = 0.0
    status: RetrievedSourceStatus = RetrievedSourceStatus.candidate
    snippet: str = ""
    local_path: str = ""

    @model_validator(mode="after")
    def validate_snippet_and_attribution(self) -> RetrievedSourceHit:
        if len(self.snippet) > 240:
            raise ValueError("retrieved snippet must be a short excerpt (<=240 chars)")
        return self

class LiteratureRetrievalReport(StrictBaseModel):
    """Real Literature Retrieval V0 output: candidate sources only.

    Retrieval may search providers / local indexes, read source text, and extract
    citation metadata + candidate locators -- but it NEVER sets source_backed,
    never skips exact locator / hypothesis match / attribution (those belong to
    Source Grounding), never treats a snippet as a checked source, never hides a
    failed search (a ``not_found`` note is recorded), and never fabricates
    metadata. The model has no source/evidence/proof status field.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    query: str
    providers: list[str]
    hits: list[RetrievedSourceHit] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

class LiteratureSearchPlan(StrictBaseModel):
    """LLM-guided retrieval PLAN: what theorem/source to look for. It proposes
    search terms only and has NO source/evidence/proof status field. A plan can
    never earn reference support — only Source Grounding (exact locator +
    hypothesis match + attribution) can."""

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    search_questions: list[str] = Field(default_factory=list)
    generated_queries: list[str] = Field(default_factory=list)
    expected_theorem_shapes: list[str] = Field(default_factory=list)
    risk_terms: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

class AutonomousRetrievalCandidate(StrictBaseModel):
    """One autonomously-retrieved candidate source. Candidate-only by
    construction: ``status`` is pinned to the literal ``"candidate"`` so neither
    a provider hit nor an LLM ``why_relevant`` explanation can mark anything
    source_backed. The locator is provider-supplied; it is never invented here.
    An abstract/snippet is a short excerpt, never full verbatim text."""

    id: str
    provider: str
    query: str
    title: str
    authors: list[str] = Field(default_factory=list)
    identifier: str = ""  # url / arXiv id / DOI as returned by the provider
    snippet: str = ""  # abstract or snippet (short excerpt)
    why_relevant: str = ""
    relevance: float = 0.0
    status: Literal["candidate"] = "candidate"
    local_path: str = ""

    @model_validator(mode="after")
    def validate_candidate_excerpt(self) -> AutonomousRetrievalCandidate:
        if len(self.snippet) > 600:
            raise ValueError("retrieved snippet/abstract must be a short excerpt (<=600 chars)")
        return self

class AutonomousLiteratureRetrievalReport(StrictBaseModel):
    """LLM-guided autonomous retrieval output: candidate sources only.

    The retrieval layer may plan queries, search providers, read metadata, and
    let an LLM rank/explain candidates -- but it NEVER sets source_backed, never
    skips exact locator / hypothesis match / attribution (those belong to Source
    Grounding), and never treats an abstract/title as theorem support. The model
    has no source/evidence/proof status field; ``grounded`` only records whether
    a candidate was HANDED to grounding (grounding alone decides source_backed)."""

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    providers: list[str]
    queries: list[str] = Field(default_factory=list)
    candidates: list[AutonomousRetrievalCandidate] = Field(default_factory=list)
    # ``grounded`` only records that a candidate was HANDED to grounding;
    # ``earned_source_backed`` records the actual outcome decided by grounding
    # (exact locator + hypothesis match + attribution). Retrieval never sets the
    # latter itself — it is read back from source_grounding after ground_run.
    grounded: bool = False
    grounded_candidate_id: str | None = None
    grounded_retrieval_source_id: str | None = None
    earned_source_ref_ids: list[str] = Field(default_factory=list)
    earned_proof_step_ids: list[str] = Field(default_factory=list)
    earned_source_backed: bool = False
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_earned_binding(self) -> AutonomousLiteratureRetrievalReport:
        if self.earned_source_backed and (
            not self.grounded
            or not self.grounded_candidate_id
            or not self.grounded_retrieval_source_id
            or not self.earned_source_ref_ids
            or not self.earned_proof_step_ids
        ):
            raise ValueError(
                "earned_source_backed requires grounded candidate, retrieval source, "
                "source ref, and proof step bindings"
            )
        return self

class LiteratureCandidate(StrictBaseModel):
    """A single CANDIDATE source the LLM Literature Scout proposes for a specific
    proof step. It is candidate-only by construction: ``status`` is a fixed
    Literal so a model/hand-edited file cannot smuggle ``source_backed`` (it is
    rejected at load), and there is no checked/proof/grade field. Reference
    support is earned only later by retrieval + extraction + grounding +
    validation, never by this artifact.
    """

    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    source_type: Literal["paper", "book", "notes", "web", "local", "unknown"] = "unknown"
    locator_hint: str = ""
    statement_hint: str = ""
    url_or_identifier: str = ""
    why_relevant: str = ""
    missing_metadata: list[str] = Field(default_factory=list)
    # SAFETY: candidate-only. Any other value is rejected at validation; the
    # normalizer also forces this so the scout can never self-certify a source.
    status: Literal["candidate"] = "candidate"

class ProofStepCandidates(StrictBaseModel):
    """Candidate sources tied to one proof step (by proof_step_id)."""

    proof_step_id: str
    proof_step_summary: str = ""
    candidates: list[LiteratureCandidate] = Field(default_factory=list)

class LiteratureCandidates(StrictBaseModel):
    """The LLM Literature Scout artifact: candidate sources/locator hints tied to
    proof steps. LLM = scout, kernel = judge. It carries NO source_backed /
    checked / proof / grade / refutation field, so nothing here can upgrade any
    trusted status."""

    schema_version: int = 1
    claim_id: str
    generated_at: str = ""
    proof_step_candidates: list[ProofStepCandidates] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

class RetrievalMetadata(StrictBaseModel):
    """Citation metadata carried alongside a resolved source. Descriptive only."""

    title: str = ""
    authors: list[str] = Field(default_factory=list)
    identifier: str = ""

class RetrievalResolutionReport(StrictBaseModel):
    """Records one retrieval-to-grounding attempt: how a candidate source was
    resolved (local file / corpus / url / arxiv), whether grounding was attempted,
    and the outcome. It carries NO source_backed / checked / proof / grade /
    refutation field, so resolving a source can never by itself grant trusted
    status — grounding + validation still own that.
    """

    schema_version: int = 1
    claim_id: str
    generated_at: str = ""
    candidate_id: str = ""
    resolution_status: Literal["resolved", "failed", "skipped"]
    source_kind: Literal["local_file", "local_corpus", "url", "arxiv", "none"] = "none"
    source_path_or_url: str = ""
    metadata: RetrievalMetadata = Field(default_factory=RetrievalMetadata)
    extraction_artifact: str = ""
    grounding_attempted: bool = False
    notes: list[str] = Field(default_factory=list)

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
    # Optional link to the source (URL / arXiv / DOI). Display convenience only:
    # a link never contributes support — grounding still needs an exact locator
    # and a hypothesis match.
    url: str = ""
    found_by: Literal["user", "literature_review", "memory", "local_notes", "web_search"]

    @model_validator(mode="after")
    def validate_literature_status(self) -> LiteratureItem:
        if self.status == SourceStatus.source_backed:
            raise ValueError("Literature Review may only propose candidates; Referencing decides source_backed")
        if self.status == SourceStatus.misapplied:
            raise ValueError("misapplied is a Referencing judgment, not a Literature Review status")
        return self

def _contains_literal_string(value: Any, literal: str) -> bool:
    if isinstance(value, str):
        return literal in value
    if isinstance(value, dict):
        return any(_contains_literal_string(item, literal) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_literal_string(item, literal) for item in value)
    return False

def _validate_literature_candidate_only_meanings(value: Any, *, artifact_name: str) -> None:
    forbidden = "literature_implies"
    if _contains_literal_string(value, forbidden):
        raise ValueError(
            f"{artifact_name} cannot emit literature_implies; only source-grounding "
            "or theorem applicability may use it for a specific proof step"
        )

class LiteratureReviewReport(StrictBaseModel):
    claim_id: str
    query_summary: str
    candidate_references: list[LiteratureItem] = Field(default_factory=list)
    known_examples: list[str] = Field(default_factory=list)
    known_counterexample_families: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_candidate_only_meanings(self) -> LiteratureReviewReport:
        _validate_literature_candidate_only_meanings(
            self.model_dump(mode="json"),
            artifact_name="Literature Review",
        )
        return self

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
    source_locator: dict[str, Any] = Field(default_factory=dict)
    # Additive provenance: hash of the extracted statement string. Source
    # grounding still owns statement_sha256 on SourceRef; this field just makes
    # extraction artifacts easier to audit and never grants trust by itself.
    statement_sha256: str = ""

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


__all__ = [
    "AutonomousLiteratureRetrievalReport",
    "AutonomousRetrievalCandidate",
    "ComparisonUncertainty",
    "ConclusionMatch",
    "DefinitionRef",
    "ExtractedTheorem",
    "LiteratureCandidate",
    "LiteratureCandidates",
    "LiteratureItem",
    "LiteratureRelevanceSummary",
    "LiteratureRetrievalReport",
    "LiteratureReviewAgentReport",
    "LiteratureReviewReport",
    "LiteratureSearchPlan",
    "LiteratureSourceAccess",
    "LiteratureUncertaintySummary",
    "ProofStepCandidates",
    "RetrievalMetadata",
    "RetrievalResolutionReport",
    "RetrievedSourceHit",
    "RetrievedSourceStatus",
    "ReviewedSource",
    "ReviewedTheorem",
    "ReviewedTheoremHypothesis",
    "StrongestLiteratureSource",
    "TheoremClaimComparison",
    "TheoremExtractionReport",
    "TheoremHypothesisComparison",
    "TheoremRelevance",
    "TheoremStatementSpan",
]
