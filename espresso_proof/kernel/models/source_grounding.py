"""Source grounding steps/refs, theorem applicability, condition ledger."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict, Field, model_validator
from pydantic import Field, model_validator
from typing import Any, Literal
from typing import Literal
import re

from espresso_proof.kernel.models.foundations import SourceStatus, StrictBaseModel


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
    # Default-deny usages derived from the advisory theorem
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
    # Sha256 of source_grounding.yaml's bytes AT AUDIT TIME. Optional/
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
            # A ledger obligation that is not fully resolved (non-exact
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

# ---------------------------------------------------------------------------
# Theorem Condition Ledger (slice 1 — model only).
#
# A durable, advisory ledger of the theorems a reviewer asked for (the verbatim
# ``source_requests``) together with the conditions/hypotheses that still have to
# be checked against the current claim before any such theorem could be reused.
#
# SAFETY: this ledger is NOT proof/source/evidence. It carries NO
# ``source_backed`` / ``checked`` / ``proof`` / ``grade`` / ``status`` field of
# any kind. ``match_status`` is a bare bookkeeping state that starts ``unknown``
# and must NEVER be inferred from claim text — only a real downstream hypothesis-
# match step (a later slice) may move it, and even then it earns no trusted
# status. ``locator_status`` starts ``needs_exact_locator``; a candidate/exact
# locator here is a search-management hint, not source grounding. Being a
# ``StrictBaseModel`` (``extra='forbid'``), the model rejects any smuggled
# status-bearing field at validation time.
# ---------------------------------------------------------------------------
class LocatorStatus(str, Enum):
    needs_exact_locator = "needs_exact_locator"
    candidate = "candidate"
    exact = "exact"

class MatchStatus(str, Enum):
    matched = "matched"
    unmatched = "unmatched"
    unknown = "unknown"

class SourceRequestOrigin(str, Enum):
    prover = "prover"
    reviewer = "reviewer"

class LedgerCondition(StrictBaseModel):
    """One condition/hypothesis recorded verbatim, with a bare match-bookkeeping
    state. ``match_status`` defaults to ``unknown`` and is never inferred from the
    claim — it carries no trusted status."""

    text: str
    claim_value: str = ""
    match_status: MatchStatus = MatchStatus.unknown

class TheoremConditionLedgerEntry(StrictBaseModel):
    """A reviewer's request for a theorem plus the conditions to check against the
    claim. Carries no proof/source/evidence/grade/status field."""

    id: str
    requested_theorem: str
    locator_status: LocatorStatus = LocatorStatus.needs_exact_locator
    locator: str | None = None
    conditions: list[LedgerCondition]
    drift_kind: str = ""  # open advisory free-text tag; never a trusted status
    repair_subgoal_id: str
    source_request_origin: SourceRequestOrigin = SourceRequestOrigin.reviewer

    @model_validator(mode="after")
    def validate_conditions(self) -> TheoremConditionLedgerEntry:
        if not self.conditions:
            raise ValueError("a ledger entry requires at least one condition")
        return self

class TheoremConditionLedger(StrictBaseModel):
    """Advisory ledger of requested theorems + conditions to check. NOT proof
    evidence: it sets no source/evidence/grade/claim status."""

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    entries: list[TheoremConditionLedgerEntry] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


__all__ = [
    "LedgerCondition",
    "LocatorStatus",
    "MatchStatus",
    "SourceGroundingReport",
    "SourceGroundingStep",
    "SourceRequestOrigin",
    "TheoremApplicabilityCondition",
    "TheoremApplicabilityReport",
    "TheoremApplicabilityReviewArtifact",
    "TheoremApplicabilityReviewRecord",
    "TheoremApplicabilityUsage",
    "TheoremConditionLedger",
    "TheoremConditionLedgerEntry",
]
