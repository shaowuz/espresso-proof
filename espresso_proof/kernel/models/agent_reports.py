"""LLM agent attempts/reviews/diagnostics, prover loop and rounds, AI review."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict, Field, model_validator
from pydantic import Field, model_validator
from typing import Any, Literal
from typing import Literal

from espresso_proof.kernel.models.foundations import StrictBaseModel


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
    # Advisory proposals only: concrete machine-checkable requests this agent
    # wants run. The kernel validates the shape here, a harvester merges them
    # into scoped_verification_requests.yaml, and only the trusted deterministic
    # backend can turn one into earned evidence. Carrying a request grants
    # nothing.
    verification_requests: list[ScopedVerificationRequest] = Field(default_factory=list)

class ScopedObjection(StrictBaseModel):
    """A reviewer objection bound to a named proof-search target so it
    becomes repairable state, not just prose.

    Kernel-safety contract (advisory only):
    - an objection can only BLOCK or PRIORITIZE repair; it can never prove,
      refute, close, or upgrade any proof/source/evidence/grade/claim status;
    - ``counterexample_pressure`` is unchecked pressure, never a refutation;
    - ``subgoal_id`` / ``proof_step_id`` are UNVERIFIED provenance, deliberately
      NOT hard-reconciled against the live proof graph in ``validate_run`` (precedent: blocker-derived node ids renumber across rebuilds). Reconciliation
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
    # Additive structured objections bound to proof-search targets.
    # Legacy free-text fields above are preserved; the normalizer also mirrors them
    # into route/global scoped_objections (scope_source='legacy_fallback') so older
    # producers stay valid and downstream has one structured surface.
    scoped_objections: list[ScopedObjection] = Field(default_factory=list)
    # Advisory proposals only: concrete machine-checkable requests this agent
    # wants run. The kernel validates the shape here, a harvester merges them
    # into scoped_verification_requests.yaml, and only the trusted deterministic
    # backend can turn one into earned evidence. Carrying a request grants
    # nothing.
    verification_requests: list[ScopedVerificationRequest] = Field(default_factory=list)
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

class LLMAgentProviderDiagnostic(StrictBaseModel):
    provider: Literal["openai", "external"]
    model: str
    # Positive record of the resolved external executor (basename only — a
    # full argv may embed secrets). Additive; old artifacts validate.
    command_basename: str | None = None
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

class LLMAgentCallDiagnostic(StrictBaseModel):
    """Out-of-band provider diagnostic for one actual agent call.

    This is process provenance only. It is deliberately kept outside prover and
    reviewer content so adapter readiness/fallback status can be audited without
    becoming mathematical evidence.
    """

    call_index: int = Field(ge=1)
    role: str
    provider: Literal["openai", "stub", "external"]
    model: str
    adapter: str
    parse_status: str
    fallback_used: bool = False
    fallback_reason: str = ""
    request_id: str = ""
    verified_real_provider: bool = False
    # Resolved-executor provenance (basename only). Additive; old artifacts
    # validate.
    command_basename: str = ""

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
    agent_call_diagnostics: list[LLMAgentCallDiagnostic] = Field(default_factory=list)
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
    # A reviewer pointer DECLARES its scope; "route" is the honest
    # default for unscoped reviewer output. subgoal_id is allowed only for
    # subgoal-scoped pointers. Like target_subgoal_id, it is UNVERIFIED
    # historical provenance: deliberately not reconciled against the live graph
    # (blocker-derived node ids renumber across rebuilds), and a pointer can
    # never change subgoal state (graph states require earned evidence).
    scope: Literal["subgoal", "route", "global"] = "route"
    subgoal_id: str | None = None

    @model_validator(mode="after")
    def validate_pointer_scope(self) -> ProverLoopReviewPointer:
        if self.subgoal_id is not None and self.scope != "subgoal":
            raise ValueError("subgoal_id requires scope='subgoal'")
        return self

class ProverLoopBlocker(StrictBaseModel):
    """One open reviewer blocker with its priority provenance (additive; the
    canonical list stays ``ProverLoopAttempt.blockers`` — details may never
    disagree with it, enforced below). Display/ordering metadata only; carries
    no evidence or status semantics."""

    text: str
    severity: Literal["P0", "P1", "P2", "P3"] = "P2"
    kind: str = "other"
    source_field: Literal["objections", "missing_hypotheses", "source_requests"]
    verifier_id: str

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
    # Additive priority/provenance sidecar for blockers (old artifacts: []).
    blocker_details: list[ProverLoopBlocker] = Field(default_factory=list)
    next_action: str
    # Which named subgoal this round targeted (the scheduler's pick at
    # round time). Optional/additive: rounds appended before a graph exists (or
    # when no subgoal is selectable) record None. Search-management state only;
    # carrying a subgoal id never earns or upgrades any status.
    target_subgoal_id: str | None = None
    target_local_claim: str = ""

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
        if self.blocker_details and [b.text for b in self.blocker_details] != list(self.blockers):
            raise ValueError(
                "blocker_details must match blockers exactly (same texts, same order); "
                "details can never disagree with the canonical blocker list"
            )
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

class ProverRoundReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    round_index: int = Field(ge=1)
    generated_at: str
    source: Literal["initial_llm_agent_run", "appended_prover_loop_round"]
    strategy_id: str
    loop_attempt_id: str
    parent_attempt_id: str | None = None
    input_artifacts: list[str]
    context_summary: str
    prover_attempt: LLMAgentAttempt
    verifier_reviews: list[LLMAgentVerifierReview]
    reviewer_blockers: list[str] = Field(default_factory=list)
    human_gate_status: Literal["pending", "resolved"]
    time_allocation: Literal["expand", "repair", "cheap_check", "pause", "ask_human", "stop"]
    status: Literal["candidate", "needs_repair", "blocked", "waiting_human", "stopped"]
    next_action: str
    safety_note: str

    @model_validator(mode="after")
    def validate_round_report(self) -> ProverRoundReport:
        if self.prover_attempt.role != "prover":
            raise ValueError("prover_attempt must have role=prover")
        if len(self.verifier_reviews) < 3:
            raise ValueError("proof-close-style prover round requires at least three verifier reviews")
        reviewer_ids = [review.verifier_id for review in self.verifier_reviews]
        if len(reviewer_ids) != len(set(reviewer_ids)):
            raise ValueError("prover round verifier ids must be unique")
        if self.status in {"needs_repair", "blocked"} and not self.reviewer_blockers:
            raise ValueError("repair/blocked prover round requires reviewer_blockers")
        if self.human_gate_status == "pending":
            if self.time_allocation not in {"ask_human", "stop"}:
                raise ValueError("pending human gate cannot allocate prover work")
            if self.status not in {"waiting_human", "stopped"}:
                raise ValueError("pending human gate requires waiting_human or stopped status")
        lowered_note = self.safety_note.lower()
        if "does not upgrade" not in lowered_note and "not proof status" not in lowered_note:
            raise ValueError("prover round safety_note must preserve proof-status boundary")
        return self

class ProverRoundIndexEntry(StrictBaseModel):
    round_index: int = Field(ge=1)
    strategy_id: str
    loop_attempt_id: str
    artifact_path: str
    status: Literal["candidate", "needs_repair", "blocked", "waiting_human", "stopped"]
    time_allocation: Literal["expand", "repair", "cheap_check", "pause", "ask_human", "stop"]

class ProverRoundIndex(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    source_prover_loop: str
    rounds: list[ProverRoundIndexEntry]

    @model_validator(mode="after")
    def validate_round_index(self) -> ProverRoundIndex:
        keys = [(entry.round_index, entry.loop_attempt_id) for entry in self.rounds]
        if len(keys) != len(set(keys)):
            raise ValueError("prover round index entries must be unique by round_index and loop_attempt_id")
        if not self.rounds:
            raise ValueError("prover round index requires at least one round")
        return self

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


__all__ = [
    "AIReviewAssessment",
    "AIReviewVerdict",
    "LLMAgentAttempt",
    "LLMAgentCallDiagnostic",
    "LLMAgentHumanGate",
    "LLMAgentProviderDiagnostic",
    "LLMAgentRunReport",
    "LLMAgentVerifierReview",
    "ProverLoopAttempt",
    "ProverLoopBlocker",
    "ProverLoopBudget",
    "ProverLoopReport",
    "ProverLoopReviewPointer",
    "ProverRoundIndex",
    "ProverRoundIndexEntry",
    "ProverRoundReport",
    "ScopedObjection",
]
