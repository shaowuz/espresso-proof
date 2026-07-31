"""Base model classes, shared enums, and the locked claim."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict, Field, model_validator
from pydantic import Field, model_validator
from typing import Any, Literal
from typing import Literal


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
    # Agent-consensus mock-check: three reviewers adversarially found no fatal flaw.
    # Advisory/amber, NOT machine-verified; strictly weaker than any reference- or
    # machine-backed status below it (it makes no source/computation claim).
    agent_consensus_mock_checked = "agent_consensus_mock_checked"
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
    # Human-readable matched-pair reasons (e.g. '"group of order p^2" matched
    # "|G| = p^2"'). Advisory display detail only: it explains WHICH hypotheses
    # matched and never relaxes the source_backed gate (exact locator +
    # attribution + matches is True are still required by the validator below).
    matched: list[str] = Field(default_factory=list)

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
    # Sha256 of the EXTRACTED theorem statement this citation quotes.
    # Optional/additive provenance binding: when set, the run validator requires
    # statement_or_summary to hash to it AND (when theorem_extraction.yaml is
    # present) the hash to name a really-extracted theorem — editing the cited
    # statement after grounding fails closed. Never earns or upgrades status.
    statement_sha256: str = ""

    @model_validator(mode="after")
    def validate_source_backed(self) -> SourceRef:
        if self.status == SourceStatus.source_backed:
            if not self.locator.is_exact:
                raise ValueError("source_backed requires an exact locator")
            if self.hypothesis_match.matches is not True:
                raise ValueError("source_backed requires matching hypotheses")
            # Anti-plagiarism: a source_backed citation MUST be attributed — a
            # cited theorem can never be presented without author + title.
            if not [a for a in self.authors if str(a).strip()]:
                raise ValueError("source_backed requires attributed author(s)")
            if not self.title.strip():
                raise ValueError("source_backed requires a source title")
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
    # Agent-supplied judgement of whether the claim is precise enough to study.
    # Advisory only: it gates whether Grade/Strategy move past claim-locking; it
    # can NEVER set source_backed, mark evidence checked, or upgrade proof status.
    claim_precision: Literal["precise", "imprecise", "unclear"] = "unclear"


__all__ = [
    "AttemptStatus",
    "Claim",
    "ComputationStatus",
    "ContinueDecision",
    "ExampleStatus",
    "ExplorationMode",
    "FlexibleBaseModel",
    "HumanChoiceStatus",
    "HumanFeedbackAction",
    "HumanFeedbackLabel",
    "HumanFeedbackScope",
    "HumanFeedbackTargetType",
    "Hypothesis",
    "HypothesisLedger",
    "HypothesisMatch",
    "HypothesisStatus",
    "IdeaStatus",
    "Locator",
    "ProofLanguageStatus",
    "RankingDirection",
    "RankingWeight",
    "ReviewerApplicability",
    "ReviewerVerdict",
    "SourceRef",
    "SourceStatus",
    "StrictBaseModel",
]
