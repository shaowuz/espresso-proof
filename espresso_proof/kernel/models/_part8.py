"""Kernel data models — file-order slice 8/8. Imported via espresso_proof.kernel.models."""


from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


from espresso_proof.kernel.models._part1 import (
    Claim,
    HypothesisLedger,
    SourceRef,
    StrictBaseModel,
)
from espresso_proof.kernel.models._part2 import (
    ComputationChecks,
    ComputationVerificationReport,
    ComputationVerificationResult,
    ExampleBank,
    ExampleComputationCheck,
    ExampleSelection,
    FailureClassificationReport,
    FailurePatternAudit,
    FailurePatternBank,
    MachineCheckSpec,
    MathematicalJudgmentReport,
)
from espresso_proof.kernel.models._part3 import (
    AutonomousLiteratureRetrievalReport,
    ExternalToolCandidatesReport,
    ExternalToolRunsReport,
    ExtractedTheorem,
    FiniteObjectWitnessReport,
    LiteratureCandidate,
    LiteratureCandidates,
    LiteratureRetrievalReport,
    LiteratureReviewReport,
    LiteratureSearchPlan,
    ProofVerificationReport,
    RetrievalResolutionReport,
    ScopedVerificationRequest,
    ScopedVerificationRequests,
)
from espresso_proof.kernel.models._part4 import (
    CounterexampleReport,
    LiteratureReviewAgentReport,
    SourceGroundingReport,
    TestReport,
    TheoremApplicabilityReport,
    TheoremApplicabilityReviewArtifact,
    TheoremExtractionReport,
)
from espresso_proof.kernel.models._part5 import (
    AdmissionCheck,
    AgentContracts,
    ExampleFeedbackSummary,
    HumanChoice,
    HumanExampleFeedback,
    IdeaGrade,
    LoopControl,
    Review,
    StrategyBrief,
    StrategySynthesis,
    TimeAllocationPlan,
    UpdateInboxReport,
)
from espresso_proof.kernel.models._part6 import (
    LLMAgentRunReport,
    ProofGoalGraph,
    ProverLoopReport,
    ProverRoundIndex,
    ProverRoundReport,
)
from espresso_proof.kernel.models._part7 import (
    AgentRunStatusReport,
    FrontHalfRunReport,
    MemoryContext,
    MemoryEntry,
    MemoryIndex,
    PressureLaneBarrierReport,
    PressureState,
    RunManifest,
    StageStatusReport,
    WorkflowSpec,
)


class PressureLateMarker(StrictBaseModel):
    """Local late-pressure marker for later Update Inbox conversion.

    This is not an Update Inbox entry. It is deterministic scheduling metadata
    only: ``dedupe_key`` lets Task 05 convert it into exactly one inbox event
    without changing proof/source/evidence/refutation/grade ownership here.
    """

    id: str
    type: Literal["example_pressure", "counterexample_pressure"]
    origin_stage: Literal["examples", "counterexamples"]
    origin_artifact: str
    origin_artifact_id: str
    artifact_revision_or_hash: str
    locked_claim_id: str
    route_id_or_step_id: str
    scope: Literal["whole_claim", "proof_step", "route"]
    pressure_state: PressureState
    lifecycle_state_at_creation: str
    late_after_strategy: bool
    dedupe_key: str
    created_at: str
    effect: Literal["scheduling_only"] = "scheduling_only"
    advisory_only: Literal[True] = True
    update_inbox_entry: Literal[False] = False
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_marker_scope(self) -> PressureLateMarker:
        expected_type = (
            "example_pressure" if self.origin_stage == "examples" else "counterexample_pressure"
        )
        if self.type != expected_type:
            raise ValueError("late pressure marker type must match origin_stage")
        if not self.dedupe_key.strip():
            raise ValueError("late pressure marker requires a dedupe_key")
        if self.origin_artifact.startswith("/") or ".." in self.origin_artifact.split("/"):
            raise ValueError("late pressure origin_artifact must be run-relative")
        if not self.artifact_revision_or_hash.strip():
            raise ValueError("late pressure marker requires artifact_revision_or_hash")
        if not self.route_id_or_step_id.strip():
            raise ValueError("late pressure marker requires route_id_or_step_id")
        return self


class PressureStatusEntry(StrictBaseModel):
    """Derived example/counterexample pressure state.

    Advisory process context only. It records whether pressure is pending,
    empty, admissible, inadmissible, or awaiting admissibility. It cannot carry
    checked evidence or refutation authority; those remain owned by examples,
    counterexamples, computation checks, and validators.
    """

    kind: Literal["examples", "counterexamples"]
    pressure_state: PressureState
    agent_status: Literal["not_run", "queued", "running", "done", "failed", "skipped", "no_board"]
    artifact_path: str
    candidate_count: int = Field(ge=0)
    admissible_refutation_count: int = Field(ge=0, default=0)
    inadmissible_pressure_count: int = Field(ge=0, default=0)
    needs_admissibility_count: int = Field(ge=0, default=0)
    late_markers: list[PressureLateMarker] = Field(default_factory=list)
    effect: Literal["pressure_context_only"] = "pressure_context_only"
    advisory_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_counts_match_state(self) -> PressureStatusEntry:
        if self.kind == "examples" and self.admissible_refutation_count:
            raise ValueError("example pressure cannot carry refutation counts")
        if self.pressure_state == "admissible_refutation" and not self.admissible_refutation_count:
            raise ValueError("admissible_refutation state requires an admissible refutation count")
        if self.pressure_state == "candidate_present_needs_admissibility" and not self.needs_admissibility_count:
            raise ValueError(
                "candidate_present_needs_admissibility state requires a pending-admissibility count"
            )
        if self.pressure_state == "inadmissible_pressure" and not self.inadmissible_pressure_count:
            raise ValueError("inadmissible_pressure state requires an inadmissible pressure count")
        if self.artifact_path.startswith("/") or ".." in self.artifact_path.split("/"):
            raise ValueError("pressure artifact_path must be run-relative")
        return self


class PressureStatusReport(StrictBaseModel):
    """Run-level async pressure sidecar.

    Optional/additive. It is derived from existing artifacts and is intended for
    UI/scheduling context only; it never changes grade, evidence, source,
    theorem, proof, final-report, memory, or Update Inbox ownership.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    entries: list[PressureStatusEntry]
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_entries(self) -> PressureStatusReport:
        kinds = [entry.kind for entry in self.entries]
        if len(kinds) != len(set(kinds)):
            raise ValueError("pressure_status entries must be unique by kind")
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


class AIReviewVerdict(str, Enum):
    """The advisory outcome of the AI-review signal.

    None of these values is a verification, a proof, or checked evidence:
    ``no_blocking_objection`` records only that >=2 independent AI reviewers
    checked the run against the kernel-locked checklist and recorded NO blocking
    objection. Absence of an objection is never proof.
    """

    no_blocking_objection = "no_blocking_objection"
    objections_found = "objections_found"
    insufficient_review = "insufficient_review"


class AIReviewAssessment(StrictBaseModel):
    """Advisory "AI review passed" signal (Goal: advisory AI-review signal).

    Records that N independent AI reviewers checked a run against the kernel-
    locked checklist and the absence (or presence) of any blocking objection. It
    is ADVISORY ONLY by construction:

    - it has NO proof / source / evidence / grade / checked / trust status field
      of any kind (and StrictBaseModel forbids extras), so it can never set or
      upgrade idea_grade / proof / source / evidence / claim status;
    - the ``ai_reviewed`` trust level it maps to is amber and NEVER in
      EARNED_LEVELS, so the signal can never render green;
    - ``verdict`` is DERIVED only from reviewer artifacts + kernel checks; prose,
      memory, or a human cannot set it, and extra fields are rejected at load.

    Absence of a blocking objection is not a verification, a proof, or checked
    evidence.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    verdict: AIReviewVerdict
    reviewer_count: int = Field(ge=0)
    dimensions_reviewed: list[str] = Field(default_factory=list)
    unresolved_objection_count: int = Field(ge=0)
    disclaimer: str
    notes: list[str] = Field(default_factory=list)


class ProofLineDependency(StrictBaseModel):
    """Structured dependency edge for one line of a candidate proof.

    This is not a proof checker. It only makes provenance explicit enough for the
    kernel to route repairs and block overclaiming: examples are not support,
    missing theorem refs stay gaps, and human-allowed theorem use remains clearly
    advisory rather than source-grounded or machine-verified.
    """

    type: Literal[
        "prior_line",
        "definition",
        "arithmetic",
        "theorem_ref",
        "human_assumption",
        "unchecked_gap",
    ]
    ref: str = Field(min_length=1)
    status: Literal[
        "resolved",
        "missing",
        "candidate",
        "rejected",
        "accepted_by_human",
    ] = "resolved"
    role: str = ""
    needed_statement: str = ""
    source_locator: str = ""
    reviewer_verdict: Literal["", "pass", "gap", "fatal"] = ""
    authority: Literal[
        "",
        "source_grounded_theorem_ref",
        "human_allowed_theorem_ref",
        "agent_claimed_theorem_ref",
    ] = ""
    source_grounded: bool = False
    machine_verified: bool = False


class ProofLine(StrictBaseModel):
    """One line of a structured, line-numbered proof. It is a CANDIDATE
    proof line: ``justification`` says WHY the line follows (a rule of inference, an
    algebraic step, a cited lemma); ``dependencies`` is the structured proof graph.
    Legacy ``depends_on`` / ``source_refs`` / ``gaps`` are preserved for compatibility
    and normalized into dependency edges. The model carries no proof/source/checked
    status field (StrictBaseModel forbids extras)."""

    line_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    justification: str = Field(min_length=1)
    dependencies: list[ProofLineDependency] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_dependency_graph(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        line_id = str(normalized.get("line_id") or "line").strip() or "line"
        dependencies = list(normalized.get("dependencies") or [])

        depends_on = [str(dep).strip() for dep in (normalized.get("depends_on") or []) if str(dep).strip()]
        source_refs = [str(ref).strip() for ref in (normalized.get("source_refs") or []) if str(ref).strip()]
        gaps = [str(gap).strip() for gap in (normalized.get("gaps") or []) if str(gap).strip()]

        existing_edges = {
            (str(edge.get("type")), str(edge.get("ref")))
            for edge in dependencies
            if isinstance(edge, dict)
        }
        allowed_authorities = {
            "",
            "source_grounded_theorem_ref",
            "human_allowed_theorem_ref",
            "agent_claimed_theorem_ref",
        }
        for edge in dependencies:
            if not isinstance(edge, dict):
                continue
            edge_type = edge.get("type")
            if edge_type == "prior_line":
                edge["status"] = "resolved"
            if edge_type == "theorem_ref":
                if edge.get("authority") not in allowed_authorities:
                    edge["authority"] = "agent_claimed_theorem_ref"
            elif edge_type in {"prior_line", "definition", "arithmetic", "unchecked_gap"}:
                if edge.get("authority") not in {"", None}:
                    edge["authority"] = ""
        for dep in depends_on:
            key = ("prior_line", dep)
            if key not in existing_edges:
                dependencies.append({"type": "prior_line", "ref": dep, "status": "resolved"})
                existing_edges.add(key)
        for ref in source_refs:
            key = ("theorem_ref", ref)
            if key not in existing_edges:
                dependencies.append(
                    {
                        "type": "theorem_ref",
                        "ref": ref,
                        "status": "candidate",
                        "authority": "agent_claimed_theorem_ref",
                    }
                )
                existing_edges.add(key)
        for index, gap in enumerate(gaps, start=1):
            key = ("unchecked_gap", f"{line_id}.gap.{index}")
            if key not in existing_edges:
                dependencies.append(
                    {
                        "type": "unchecked_gap",
                        "ref": f"{line_id}.gap.{index}",
                        "status": "missing",
                        "role": gap,
                    }
                )
                existing_edges.add(key)

        prior_refs = [
            str(edge.get("ref")).strip()
            for edge in dependencies
            if isinstance(edge, dict)
            and edge.get("type") == "prior_line"
            and str(edge.get("ref")).strip()
        ]
        theorem_refs = [
            str(edge.get("ref")).strip()
            for edge in dependencies
            if isinstance(edge, dict)
            and edge.get("type") == "theorem_ref"
            and str(edge.get("ref")).strip()
        ]
        gap_roles = [
            str(edge.get("role") or edge.get("needed_statement") or edge.get("ref")).strip()
            for edge in dependencies
            if isinstance(edge, dict)
            and edge.get("type") == "unchecked_gap"
            and str(edge.get("role") or edge.get("needed_statement") or edge.get("ref")).strip()
        ]

        normalized["dependencies"] = dependencies
        normalized["depends_on"] = list(dict.fromkeys([*depends_on, *prior_refs]))
        normalized["source_refs"] = list(dict.fromkeys([*source_refs, *theorem_refs]))
        normalized["gaps"] = list(dict.fromkeys([*gaps, *gap_roles]))
        return normalized


class LineNumberedProof(StrictBaseModel):
    """A structured, line-numbered proof artifact — the ONLY thing the
    agent-consensus mock-check may seal.

    A strategy / route / proposed_steps list / advisory_proof_sketch is a PLAN, not a
    proof, and is explicitly NOT sealable. This artifact is a real proof object: each
    line has a statement AND a justification AND its dependencies. It remains ADVISORY
    (``status`` is pinned ``advisory``) and carries no proof/source/checked status
    field, so producing it never earns trusted status — only the kernel's hard gates
    (or the explicitly-labelled mock check) move proof language, and even the mock
    check stays amber / not-machine-verified.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    strategy_id: str
    proof_kind: str
    status: Literal["advisory"] = "advisory"
    lines: list[ProofLine] = Field(min_length=1)
    generated_at: str = ""
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_lines(self) -> LineNumberedProof:
        if not self.lines:
            raise ValueError(
                "line_numbered_proof must have at least one line (a route is not a proof)"
            )
        seen: set[str] = set()
        for line in self.lines:
            if not line.line_id.strip():
                raise ValueError("every proof line needs a non-empty line_id")
            if line.line_id in seen:
                raise ValueError(f"duplicate line_id {line.line_id!r}")
            if not line.statement.strip():
                raise ValueError(f"line {line.line_id} needs a non-empty statement")
            if not line.justification.strip():
                # A line without a justification is a route step, not a proof line.
                raise ValueError(f"line {line.line_id} needs a non-empty justification")
            for dep in line.depends_on:
                if dep not in seen:
                    raise ValueError(
                        f"line {line.line_id} depends_on {dep!r} which is not an earlier line"
                    )
            for dep in line.dependencies:
                if dep.type == "prior_line" and dep.ref not in seen:
                    raise ValueError(
                        f"line {line.line_id} dependency {dep.ref!r} is not an earlier line"
                    )
            seen.add(line.line_id)
        return self


class ProofGapResolutionCandidate(StrictBaseModel):
    """One candidate repair for an unresolved proof dependency.

    Candidate source lookup and LLM applicability notes are planning artifacts.
    They do not earn source-backed, checked, proof, or verified status. Human
    allowance is explicit provenance for advisory use only.
    """

    line_id: str = Field(min_length=1)
    dependency_ref: str = Field(min_length=1)
    dependency_type: Literal["theorem_ref", "human_assumption", "unchecked_gap"]
    action: Literal[
        "attach_candidate_theorem_ref",
        "attach_human_allowed_theorem_ref",
        "keep_gap",
    ] = "keep_gap"
    proposed_ref: str = ""
    needed_statement: str = ""
    proposed_statement: str = ""
    source_locator: str = ""
    source_url: str = ""
    source_kind: Literal[
        "user_material",
        "project_corpus",
        "web",
        "paper",
        "book",
        "unknown",
    ] = "unknown"
    applicability_review: Literal[
        "not_reviewed",
        "candidate_match",
        "mismatch",
        "unclear",
        "human_allowed",
    ] = "not_reviewed"
    reviewer_notes: list[str] = Field(default_factory=list)
    status: Literal["candidate", "accepted_by_human", "rejected"] = "candidate"
    source_grounded: bool = False
    machine_verified: bool = False
    human_confirmation_required: bool = True


class ProofGapResolutionReport(StrictBaseModel):
    """Repair-loop sidecar for unresolved proof dependencies.

    The resolver may find candidate sources or record explicit human-allowed
    theorem use, but this report itself is advisory process evidence only.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    source_policy: Literal["dynamic_sources_only"] = "dynamic_sources_only"
    proof_artifact: str = "line_numbered_proof.yaml"
    line_numbered_proof_sha256: str = Field(min_length=1)
    candidates: list[ProofGapResolutionCandidate] = Field(default_factory=list)
    repair_plan: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AgentConsensusVerdict(str, Enum):
    """One reviewer's ADVERSARIAL verdict for the agent-consensus mock-check.

    The reviewer actively hunts for a flaw that would BREAK the argument. A residual
    source/definition gap is NOT a fatal flaw (it is surfaced, not blocking); only an
    argument-breaking error is. ``unclear`` means the reviewer could not confirm
    soundness (including when a missing source prevents judging applicability) — it
    BLOCKS the consensus, the conservative resolution of the fuzzy fatal/residual line.
    """

    no_fatal_flaw_found = "no_fatal_flaw_found"
    fatal_flaw_found = "fatal_flaw_found"
    unclear = "unclear"


PROOF_CONSENSUS_REVIEWERS = (
    "line_proof_reviewer",
    "theorem_lemma_applicability_reviewer",
    "adversarial_gap_reviewer",
)


class AgentConsensusEntry(StrictBaseModel):
    # The three reviewers review the LINE-NUMBERED PROOF (not route text).
    reviewer_id: Literal[
        "line_proof_reviewer",
        "theorem_lemma_applicability_reviewer",
        "adversarial_gap_reviewer",
    ]
    verdict: AgentConsensusVerdict
    looked_for: str = Field(min_length=1)  # which fatal-flaw classes this reviewer actively hunted
    finding: str = Field(min_length=1)  # the active "no fatal flaw because ..." / "fatal flaw: ..." statement
    # A reviewer's verdict must cite the proof line_id(s) it rests on; a PASS
    # that references no line is not a real review of the line-numbered proof.
    cited_line_ids: list[str]
    residual_objections: list[str] = Field(default_factory=list)  # non-fatal, surfaced


class AgentConsensusReview(StrictBaseModel):
    """Agent-consensus mock-check (NOT machine-verified) — the kernel record that the
    three Espresso reviewers each ran ADVERSARIALLY and the consensus outcome.

    A deliberate, clearly-labelled exception to "agents never grant status":
    when all three reviewers return ``no_fatal_flaw_found`` the run may carry
    ``proof_language_status = agent_consensus_mock_checked`` — an AMBER, advisory tier
    strictly weaker than ``source_checked_argument``. It is honest by construction:

    - it has NO proof/source/evidence/grade/checked status field (StrictBaseModel
      forbids extras), so it can never set or upgrade a machine-verified status;
    - the ``bound_*`` hashes pin it to the exact claim/source/review/prover_loop it
      was rendered against, so the kernel REVOKES it (rejects the run) once any of
      those change — a stale consensus can never ride a modified proof;
    - ``consensus`` is re-derived here from the entries (unanimous no-fatal-flaw), so
      it cannot disagree with the per-reviewer verdicts;
    - any ``fatal_flaw_found`` or ``unclear`` blocks it; residual (non-fatal)
      objections are recorded for surfacing, never silently dropped.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    bound_claim_hash: str
    # The consensus binds the LINE-NUMBERED PROOF it reviewed (not route
    # text). Any edit to the proof after review changes this hash -> seal fails closed.
    bound_line_numbered_proof_hash: str
    bound_source_grounding_hash: str
    bound_review_hash: str
    bound_prover_loop_hash: str
    entries: list[AgentConsensusEntry] = Field(min_length=3, max_length=3)
    consensus: bool
    disclaimer: str
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_consensus(self) -> AgentConsensusReview:
        required = set(PROOF_CONSENSUS_REVIEWERS)
        seen = {entry.reviewer_id for entry in self.entries}
        if seen != required:
            raise ValueError(
                "agent_consensus_review requires exactly the three distinct proof "
                f"reviewers {sorted(required)}"
            )
        all_clear = all(
            entry.verdict == AgentConsensusVerdict.no_fatal_flaw_found
            for entry in self.entries
        )
        if self.consensus != all_clear:
            raise ValueError(
                "consensus must equal unanimous no_fatal_flaw_found across all three "
                "reviewers"
            )
        return self


MODEL_REGISTRY: dict[str, type[BaseModel]] = {
    "claim": Claim,
    "ai_review": AIReviewAssessment,
    "agent_consensus_review": AgentConsensusReview,
    "line_numbered_proof": LineNumberedProof,
    "agent_contracts": AgentContracts,
    "admission_check": AdmissionCheck,
    "hypothesis_ledger": HypothesisLedger,
    "literature_review": LiteratureReviewReport,
    "literature_review_agent": LiteratureReviewAgentReport,
    "example_bank": ExampleBank,
    "examples": ExampleSelection,
    "computation_checks": ComputationChecks,
    "machine_check_spec": MachineCheckSpec,
    "computation_verification": ComputationVerificationReport,
    "computation_verification_result": ComputationVerificationResult,
    "failure_classification": FailureClassificationReport,
    "mathematical_judgment": MathematicalJudgmentReport,
    "failure_pattern_bank": FailurePatternBank,
    "failure_pattern_audit": FailurePatternAudit,
    "literature_retrieval": LiteratureRetrievalReport,
    "literature_search_plan": LiteratureSearchPlan,
    "autonomous_literature_retrieval": AutonomousLiteratureRetrievalReport,
    "proof_verification": ProofVerificationReport,
    "finite_object_witness": FiniteObjectWitnessReport,
    "external_tool_candidates": ExternalToolCandidatesReport,
    "external_tool_runs": ExternalToolRunsReport,
    "scoped_verification_request": ScopedVerificationRequest,
    "scoped_verification_requests": ScopedVerificationRequests,
    "literature_candidate": LiteratureCandidate,
    "literature_candidates": LiteratureCandidates,
    "retrieval_resolution": RetrievalResolutionReport,
    "counterexamples": CounterexampleReport,
    "tests": TestReport,
    "source_grounding": SourceGroundingReport,
    "theorem_extraction": TheoremExtractionReport,
    "theorem_applicability": TheoremApplicabilityReport,
    "theorem_applicability_review": TheoremApplicabilityReviewArtifact,
    "review": Review,
    "idea_grade": IdeaGrade,
    "loop_control": LoopControl,
    "strategy_synthesis": StrategySynthesis,
    "strategy_brief": StrategyBrief,
    "time_allocation": TimeAllocationPlan,
    "update_inbox": UpdateInboxReport,
    "human_choice": HumanChoice,
    "human_example_feedback": HumanExampleFeedback,
    "example_feedback_summary": ExampleFeedbackSummary,
    "llm_agent_run": LLMAgentRunReport,
    "prover_loop": ProverLoopReport,
    "prover_round": ProverRoundReport,
    "prover_round_index": ProverRoundIndex,
    "proof_gap_resolution": ProofGapResolutionReport,
    "proof_goal_graph": ProofGoalGraph,
    "theorem_condition_ledger": TheoremConditionLedger,
    "human_time_allocation_context": HumanTimeAllocationContext,
    "front_half_run": FrontHalfRunReport,
    "stage_status": StageStatusReport,
    "pressure_lane_barrier": PressureLaneBarrierReport,
    "agent_run_status": AgentRunStatusReport,
    "pressure_status": PressureStatusReport,
    "memory_entry": MemoryEntry,
    "memory_index": MemoryIndex,
    "memory_context": MemoryContext,
    "run_manifest": RunManifest,
    "workflow_spec": WorkflowSpec,
    "source_ref": SourceRef,
    "extracted_theorem": ExtractedTheorem,
    "example_computation_check": ExampleComputationCheck,
}


__all__ = [
    'AIReviewAssessment',
    'AIReviewVerdict',
    'AgentConsensusEntry',
    'AgentConsensusReview',
    'AgentConsensusVerdict',
    'HumanTimeAllocationContext',
    'LedgerCondition',
    'LineNumberedProof',
    'LocatorStatus',
    'MODEL_REGISTRY',
    'MatchStatus',
    'PROOF_CONSENSUS_REVIEWERS',
    'PressureLateMarker',
    'PressureStatusEntry',
    'PressureStatusReport',
    'ProofGapResolutionCandidate',
    'ProofGapResolutionReport',
    'ProofLine',
    'ProofLineDependency',
    'RouteAllocationEntry',
    'SourceRequestOrigin',
    'TheoremConditionLedger',
    'TheoremConditionLedgerEntry',
]
