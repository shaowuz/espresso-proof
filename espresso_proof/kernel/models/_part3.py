"""Kernel data models — file-order slice 3/8. Imported via espresso_proof.kernel.models."""


from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


from espresso_proof.kernel.models._part1 import *  # noqa: F401,F403


from espresso_proof.kernel.models._part2 import *  # noqa: F401,F403


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


class VerifierType(str, Enum):
    """A trusted-verifier backend kind.

    - ``finite_group_cayley``: deterministic brute-force check of an explicitly
      given finite group (implemented; trusted).
    - ``finite_identity_table``: exhaustive check of an algebraic identity on an
      explicitly-given finite operation table (implemented; trusted).
    - ``finite_ring_identity``: exhaustive check of a named algebraic identity
      over Z/n for an explicit modulus n (implemented; trusted).
    - ``exact_computation``: the exact_arithmetic / permutation_order backend
      (implemented elsewhere; trusted).
    - ``lean_bridge``: a Lean/mathlib proof check (NOT executed in V0; registry
      only -- it cannot earn a checked status here).
    - ``human_checked``: a human attestation (NOT machine verification; a
      separate, clearly-labelled status).
    """

    finite_group_cayley = "finite_group_cayley"
    finite_identity_table = "finite_identity_table"
    finite_ring_identity = "finite_ring_identity"
    matrix_rank = "matrix_rank"
    exact_computation = "exact_computation"
    lean_bridge = "lean_bridge"
    human_checked = "human_checked"


class ProofVerificationStatus(str, Enum):
    """What a verifier entry EARNED. There is deliberately NO ``proved`` value:
    a finite/instance check can never earn a universal proof in V0.

    - ``instance_checked``: a specific finite instance was machine-checked.
    - ``computation_checked``: a computation was confirmed by a trusted backend.
    - ``lean_checked``: a Lean proof compiled (reserved; not earnable in V0).
    - ``human_attested``: a human checked it (separate from machine verification).
    - ``unverified``: nothing earned / the verifier failed (fail closed).
    """

    instance_checked = "instance_checked"
    computation_checked = "computation_checked"
    lean_checked = "lean_checked"
    human_attested = "human_attested"
    unverified = "unverified"


class ProofVerificationEntry(StrictBaseModel):
    """One verifier run. ``trusted`` marks a deterministic machine backend; only a
    trusted backend with a confirming result may earn a checked status (enforced by
    the validator). ``scope_note`` must state exactly what was (and was not) shown.

    The reproducibility fields (Goal 38) are provenance only — they record HOW to
    replay a checked computation (adapter, versions, input/output hashes, exact
    replay command). They are optional/additive and never earn or upgrade status.
    """

    id: str
    verifier_type: VerifierType
    claim_id: str
    target: str
    input_summary: str
    result: str
    trusted: bool
    confirmed: bool
    earned_status: ProofVerificationStatus
    scope_note: str
    adapter_id: str = ""
    code_version: str = ""
    dependency_version: str = ""
    input_hash: str = ""
    output_hash: str = ""
    replay_command: str = ""


class ProofVerificationReport(StrictBaseModel):
    """Proof Verification Bridge V0.

    Records trusted-verifier runs and the SCOPED status each earned. Natural-
    language prover/reviewer approval is not verification and cannot reach this
    report. The model has no ``proved`` status: a finite/instance check earns only
    ``instance_checked`` / ``computation_checked`` for what it actually covered;
    ``lean_checked`` is reserved and not earnable in V0; a human check is recorded
    as the separate ``human_attested``. The validator fails closed.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    entries: list[ProofVerificationEntry] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FiniteObjectWitnessReport(StrictBaseModel):
    """Scoped finite-object witness sidecar.

    The sidecar is reviewer-facing evidence packaging only. It must stay scoped
    and advisory; the checked status, if any, is owned by proof_verification.yaml.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    witness_id: str
    kind: Literal["gl2_f3_solvability"]
    advisory_only: Literal[True]
    witness: dict[str, Any]
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_gl2_witness_shape(self) -> FiniteObjectWitnessReport:
        required = {
            "order",
            "derived_series_orders",
            "is_solvable",
            "confirmed",
            "transcript_sha256",
        }
        missing = sorted(required - set(self.witness))
        if missing:
            raise ValueError(f"finite object witness missing required keys: {missing}")
        if self.witness["order"] != 48:
            raise ValueError("GL_2(F_3) witness must record order 48")
        if self.witness["derived_series_orders"] != [48, 24, 8, 2, 1]:
            raise ValueError("GL_2(F_3) witness must record the expected derived series")
        if self.witness["is_solvable"] is not True or self.witness["confirmed"] is not True:
            raise ValueError("GL_2(F_3) witness must be confirmed solvable")
        if not str(self.witness["transcript_sha256"]).strip():
            raise ValueError("finite object witness requires transcript_sha256")
        return self


class ExternalToolCandidate(StrictBaseModel):
    """A discovered external tool proposed for a scoped check (Goal 25, slice 1).

    Discovery is candidate-only: ``status`` is an allowlisting decision, NOT an
    evidence status. The model carries no source/evidence/proof status field (and
    StrictBaseModel forbids extras), so proposing a tool can never earn anything.
    Only an allowlisted deterministic adapter + the existing proof-verification
    bridge can earn scoped evidence; an unknown tool stays ``candidate``.
    """

    name: str
    source_url: str = ""
    version: str = ""
    why_relevant: str = ""
    status: Literal["candidate", "allowlisted", "rejected"] = "candidate"


class ExternalToolCandidatesReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    candidates: list[ExternalToolCandidate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ExternalToolRun(StrictBaseModel):
    """One audited external-tool execution record (Goal 25, slice 1).

    This is RAW evidence with full provenance, NOT an earned status: the model has
    no proof/source/evidence/grade field, so an external tool can never write a
    trusted status here. Only a kernel-gated bridge (proof_verification, a trusted
    deterministic backend) may later earn a SCOPED instance/computation status
    from a confirming run. ``trusted`` records only whether the producing adapter
    is allowlisted; it does not by itself earn anything. Paths are relative to the
    run directory (absolute / ``..`` are rejected by the validator). No secrets or
    full environment are recorded.
    """

    id: str
    tool_name: str
    source_url: str = ""
    version: str = ""
    install_command: str = ""
    argv: list[str] = Field(default_factory=list)
    working_dir: str = "."
    input_files: list[str] = Field(default_factory=list)
    output_files: list[str] = Field(default_factory=list)
    stdout_excerpt: str = ""
    stderr_excerpt: str = ""
    exit_code: int | None = None
    duration_seconds: float | None = None
    timeout_seconds: float | None = None
    timed_out: bool = False
    parsed_result: str = ""
    ai_interpretation: str = ""
    conclusion: str = ""
    statement_checked: str = ""
    scope_note: str
    not_universal_proof: Literal[True] = True
    network_used: bool = False
    trusted: bool = False

    @model_validator(mode="after")
    def validate_no_proof_complete_interpretation(self) -> ExternalToolRun:
        # Goal 38: stdout/stderr excerpts stay RAW audited evidence (the display
        # layer scrubs them), but the INTERPRETIVE fields are adapter/agent-
        # authored and must never assert global proof completion. Fail closed at
        # parse time so "the tool proved the theorem" can never persist as an
        # interpretation.
        from espresso_proof.kernel.proof_language import contains_proof_language

        for field in ("ai_interpretation", "conclusion", "statement_checked", "scope_note"):
            phrase = contains_proof_language(getattr(self, field))
            if phrase:
                raise ValueError(
                    f"external tool run field {field!r} contains proof-complete "
                    f"language ({phrase!r}); tool evidence is scoped and never a "
                    "universal proof"
                )
        return self


class ExternalToolRunsReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    runs: list[ExternalToolRun] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ScopedVerificationRequest(StrictBaseModel):
    """A CONCRETE, machine-runnable scoped-verification request proposed by the
    strategy/prover stage so a finite/computational check can be auto-wired into a
    run instead of only run by hand.

    It is ONLY a proposal: a trusted deterministic backend runs it and only a
    confirming result earns ``instance_checked`` (via the existing proof-
    verification gate). It carries NO status / earned field, so a request can never
    self-certify, and natural-language prose is never read here. Auto-wiring fails
    closed on a missing / ambiguous / too-expensive request (it earns nothing).
    """

    id: str
    kind: Literal["finite_identity_table", "finite_ring_identity", "matrix_rank"]
    identity: str = ""
    table: list[list[int]] | None = None  # operation table (finite_identity_table) or matrix (matrix_rank)
    modulus: int | None = None  # required for finite_ring_identity
    expected_rank: int | None = None  # matrix_rank
    expected_determinant: int | None = None  # matrix_rank (square only)
    target: str = ""


class ScopedVerificationRequests(StrictBaseModel):
    """The strategy/prover stage's structured scoped-verification proposals for a
    run. The auto-wiring reader executes only these explicit, concrete requests;
    prose in other artifacts is never parsed into a request."""

    claim_id: str
    requests: list[ScopedVerificationRequest] = Field(default_factory=list)


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


__all__ = [
    'AutonomousLiteratureRetrievalReport',
    'AutonomousRetrievalCandidate',
    'ExternalToolCandidate',
    'ExternalToolCandidatesReport',
    'ExternalToolRun',
    'ExternalToolRunsReport',
    'ExtractedTheorem',
    'FiniteObjectWitnessReport',
    'LiteratureCandidate',
    'LiteratureCandidates',
    'LiteratureItem',
    'LiteratureRetrievalReport',
    'LiteratureReviewReport',
    'LiteratureSearchPlan',
    'ProofStepCandidates',
    'ProofVerificationEntry',
    'ProofVerificationReport',
    'ProofVerificationStatus',
    'RetrievalMetadata',
    'RetrievalResolutionReport',
    'RetrievedSourceHit',
    'RetrievedSourceStatus',
    'ScopedVerificationRequest',
    'ScopedVerificationRequests',
    'VerifierType',
    '_contains_literal_string',
    '_validate_literature_candidate_only_meanings',
]
