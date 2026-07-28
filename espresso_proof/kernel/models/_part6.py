"""Kernel data models — file-order slice 6/8. Imported via espresso_proof.kernel.models."""


from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator


from espresso_proof.kernel.models._part1 import (
    ExplorationMode,
    StrictBaseModel,
)
from espresso_proof.kernel.models._part5 import (
    LLMAgentAttempt,
    LLMAgentHumanGate,
    LLMAgentVerifierReview,
)


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


class SubgoalState(str, Enum):
    """The proof-search state of a single subgoal node.

    These are SEARCH-MANAGEMENT states, not trusted statuses. There is
    deliberately NO ``proved`` / ``verified`` / ``proof_complete`` value: a
    subgoal graph can never record proof completion (the kernel boundary owns
    proof/source/evidence status, never this artifact).

    - ``open``: nothing earned yet; still to be shown.
    - ``sketched``: an informal route/argument exists but nothing is grounded.
    - ``source_supported``: backed ONLY as a reference by an earned
      source-grounding step (still not a proof of the whole claim).
    - ``scoped_checked``: confirmed ONLY in a finite / computational scope by a
      deterministic verifier.
    - ``blocked_missing_source``: cannot progress without a missing source.
    - ``blocked_reviewer_objection``: held open by a reviewer objection.
    - ``blocked_external_tool``: needs an external tool/verifier that is
      Unavailable or was refused. A non-evidence state.
    - ``conditional``: progress is contingent on an unresolved gap or a research
      Blocker. A non-evidence state.
    - ``contradicted``: refuted/rejected under the current hypotheses by an
      existing counterexample/refutation validator (never by prose).
    """

    open = "open"
    sketched = "sketched"
    source_supported = "source_supported"
    scoped_checked = "scoped_checked"
    blocked_missing_source = "blocked_missing_source"
    blocked_reviewer_objection = "blocked_reviewer_objection"
    blocked_external_tool = "blocked_external_tool"
    conditional = "conditional"
    contradicted = "contradicted"


# States a subgoal may carry only when matching EARNED evidence is referenced.
_SUBGOAL_EVIDENCE_STATES = {SubgoalState.source_supported, SubgoalState.scoped_checked}


# Non-evidence "blocked/conditional" repair states. A node in one of these
# states is advisory search-management only: it may carry a typed ``block_reason``
# and a ``suggested_next_action``, but it can NEVER reference earned support
# (source/computation/scoped checks) -- that would smuggle trusted status into a
# blocked node. The run validator and the node validator both fail closed.
_SUBGOAL_BLOCKED_STATES = {
    SubgoalState.blocked_missing_source,
    SubgoalState.blocked_reviewer_objection,
    SubgoalState.blocked_external_tool,
    SubgoalState.conditional,
}


class BlockReason(str, Enum):
    """Why a subgoal is blocked/conditional (advisory typing only).

    Pure search-management metadata. A ``block_reason`` NEVER sets or upgrades
    any trusted status; a node carrying it is still just blocked repair state.
    The set is closed -- an unknown reason fails closed at parse time.
    """

    false_key_lemma = "false_key_lemma"
    theorem_condition_gap = "theorem_condition_gap"
    hard_research_blocker = "hard_research_blocker"
    external_tool_blocked = "external_tool_blocked"


# Tokens that must never appear in a subgoal graph (it cannot claim proof
# completion). Mirrors the final-report proof-language guard.
_SUBGOAL_FORBIDDEN_TOKENS = (
    "proved",
    "proven",
    "proof-close",
    "verified",
    "proof complete",
    "proof_complete",
    "proof-complete",
)


class SubgoalRole(str, Enum):
    main_goal = "main_goal"
    lemma = "lemma"
    reduction = "reduction"
    hypothesis_check = "hypothesis_check"
    computation_check = "computation_check"
    reference_check = "reference_check"


class SubgoalNode(StrictBaseModel):
    """One subgoal in the proof-search graph.

    ``supported_by_*`` lists are REFERENCES into earned artifacts; the run
    validator checks each reference actually earned its support (a node cannot
    self-certify). Reviewer objections / counterexample pressure are advisory
    attachments and never close a subgoal as proof.
    """

    id: str
    statement: str
    role: SubgoalRole
    state: SubgoalState
    closure_contract: str
    depends_on: list[str] = Field(default_factory=list)
    supported_by_source_steps: list[str] = Field(default_factory=list)
    supported_by_computations: list[str] = Field(default_factory=list)
    supported_by_scoped_checks: list[str] = Field(default_factory=list)
    reviewer_objections: list[str] = Field(default_factory=list)
    counterexample_pressure: list[str] = Field(default_factory=list)
    remaining_risk: str = ""
    # Advisory blocked/conditional typing. Optional + default so every
    # pre-57B graph still validates unchanged.
    block_reason: BlockReason | None = None
    suggested_next_action: str = ""

    @model_validator(mode="after")
    def _validate_block_typing(self) -> SubgoalNode:
        # A typed block_reason only makes sense on a blocked/conditional node.
        if self.block_reason is not None and self.state not in _SUBGOAL_BLOCKED_STATES:
            raise ValueError(
                f"block_reason is only allowed on a blocked/conditional subgoal "
                f"state, not {self.state.value!r}"
            )
        # Non-evidence repair states can never reference earned support: that
        # would let a blocked node smuggle trusted source/checked status.
        if self.state in _SUBGOAL_BLOCKED_STATES and (
            self.supported_by_source_steps
            or self.supported_by_computations
            or self.supported_by_scoped_checks
        ):
            raise ValueError(
                f"{self.state.value} is a non-evidence repair state and cannot "
                "reference earned source/computation/scoped-check support"
            )
        return self


class SubgoalEdge(StrictBaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_node: str = Field(alias="from")
    to: str
    relation: Literal["needed_for", "supports", "refines", "blocks"]


class SubgoalGraphSummary(StrictBaseModel):
    open: int = Field(ge=0)
    sketched: int = Field(ge=0)
    source_supported: int = Field(ge=0)
    finite_or_computation_checked: int = Field(ge=0)
    blocked: int = Field(ge=0)


class SubgoalGraphGeneratedFrom(StrictBaseModel):
    strategy_brief: str | None = None
    prover_loop: str | None = None
    source_grounding: str | None = None


class ProofGoalGraph(StrictBaseModel):
    """A durable, advisory proof-SEARCH state artifact (``proof_goal_graph.yaml``).

    It records what must be shown, what is open/sketched, what is only
    reference-supported, what is checked only in a finite/computational scope,
    and what is blocked. It is NOT proof evidence: it cannot set or upgrade
    ``idea_grade`` / ``proof_language_status`` / source / evidence / claim
    status, and it can never contain proof-complete language. The model and the
    run validator both fail closed.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    selected_strategy: str | None = None
    generated_from: SubgoalGraphGeneratedFrom = Field(
        default_factory=SubgoalGraphGeneratedFrom
    )
    nodes: list[SubgoalNode]
    edges: list[SubgoalEdge] = Field(default_factory=list)
    summary: SubgoalGraphSummary
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph_structure(self) -> ProofGoalGraph:
        if not self.nodes:
            raise ValueError("proof goal graph requires at least one node")
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("proof goal graph node ids must be unique")
        known = set(node_ids)
        main_goals = [node for node in self.nodes if node.role == SubgoalRole.main_goal]
        if len(main_goals) != 1:
            raise ValueError("proof goal graph requires exactly one main_goal node")
        for edge in self.edges:
            if edge.from_node not in known:
                raise ValueError(f"proof goal graph edge from unknown node {edge.from_node!r}")
            if edge.to not in known:
                raise ValueError(f"proof goal graph edge to unknown node {edge.to!r}")
        for node in self.nodes:
            for dep in node.depends_on:
                if dep not in known:
                    raise ValueError(
                        f"{node.id}: depends_on references unknown node {dep!r}"
                    )
        # A subgoal graph can never carry proof-complete language.
        haystack = " ".join(self._text_fields()).lower()
        for token in _SUBGOAL_FORBIDDEN_TOKENS:
            if token in haystack:
                raise ValueError(
                    f"proof goal graph uses forbidden proof-complete language: {token!r}"
                )
        return self

    def _text_fields(self) -> list[str]:
        parts: list[str] = list(self.notes)
        for node in self.nodes:
            parts.extend(
                [
                    node.statement,
                    node.closure_contract,
                    node.remaining_risk,
                    *node.reviewer_objections,
                    *node.counterexample_pressure,
                ]
            )
        return parts


_MEMORY_FORBIDDEN_FIELD_TOKENS = {
    "source_backed",
    "source_status",
    "proof_status",
    "proof_language_status",
    "evidence_status",
    "checked_evidence",
    "theorem_applicability_status",
    "refutation_status",
    "grade_status",
    "memory_evidence",
}


def _memory_text_clauses(text: str) -> list[str]:
    return [
        clause.strip().lower()
        for clause in re.split(r"[.;:\n]", str(text))
        if clause.strip()
    ]


def _memory_clause_is_disclaimer(clause: str) -> bool:
    return bool(
        re.search(
            r"\b(?:not|never|cannot|can't|must not|does not|do not|no)\b",
            clause,
        )
    )


def _memory_non_disclaimer_segments(clause: str) -> list[str]:
    segments = [
        segment.strip()
        for segment in re.split(r",|\b(?:and|but|however|yet)\b", clause)
        if segment.strip()
    ]
    return [segment for segment in segments if not _memory_clause_is_disclaimer(segment)]


def _memory_normalized_status_text(text: str) -> str:
    normalized = str(text).lower()
    normalized = re.sub(r"[_-]+", " ", normalized)
    normalized = re.sub(r"\s*(?:=|:)\s*", " is ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _memory_full_text_status_reason(text: str) -> str | None:
    normalized = _memory_normalized_status_text(text)
    status_patterns = [
        (
            r"\bno\s+counterexamples?\s+"
            r"(?:remain|exists?|appeared?|produced|found|"
            r"(?:(?:was|were)\s+)?(?:found|produced)|pressure|"
            r"refutes?\s+(?:this|the)\s+(?:claim|route|step))\b",
            "counterexample no-pressure status",
        ),
        (
            r"\bno\s+counterexamples?\s+to\s+(?:this|the)\s+"
            r"(?:claim|route|step|theorem)\s+(?:was|were)\s+found\b",
            "counterexample no-pressure status",
        ),
        (
            r"\b(?:this|the)\s+(?:search|run)\s+"
            r"(?:produced\s+no|found\s+(?:zero|no))\s+counterexamples?\b",
            "counterexample no-pressure status",
        ),
        (
            r"\bcounterexamples?\s+[a-z0-9./_-]+\s+"
            r"(?:is\s+)?(?:this\s+)?refutes?\s+(?:this|the)?\s*(?:claim|route|step)\b",
            "refutation status",
        ),
        (r"\bno\s+examples?\s+(?:failed|fail|failing)\b", "example evidence status"),
        (r"\bno\s+failing\s+examples?\b", "example evidence status"),
        (r"\b(?:the\s+)?examples?\s+did\s+not\s+fail\b", "example evidence status"),
        (
            r"\bexamples?\s+(?:[a-z0-9./]+(?:\s+|$))?"
            r"(?:does\s+not|did\s+not|do\s+not)\s+fail\b",
            "example evidence status",
        ),
        (
            r"\bexamples?\s+(?:[a-z0-9./]+(?:\s+|$))?"
            r"(?:survived|failed|checked|passed|passes)\b",
            "example evidence status",
        ),
        (
            r"\bexamples?\s+(?:verify|verifies|confirmed?|validated?)\s+"
            r"(?:this|the)?\s*(?:claim|route|step|theorem)\b",
            "example evidence status",
        ),
        (r"\b(?:survived|failed|checked|passed)\s+examples?\b", "example evidence status"),
        (
            r"\bcounterexample\s+search\s+"
            r"(?:(?:did\s+not\s+find|found\s+no)\s+pressure|found\s+none)\b",
            "counterexample no-pressure status",
        ),
        (
            r"\bcounterexample\s+search\s+came\s+back\s+empty\b",
            "counterexample no-pressure status",
        ),
        (
            r"\b(?:empty\s+counterexample\s+search|"
            r"(?:the\s+)?counterexample\s+search\s+(?:was|returned)\s+empty)\b",
            "counterexample no-pressure status",
        ),
        (
            r"\bno\s+obstruction\s+(?:was|were)?\s*found\b",
            "counterexample no-pressure status",
        ),
        (
            r"\b(?:this|the)?\s*(?:claim|theorem|route|statement)\s+survived\s+"
            r"counterexample\s+search\b",
            "counterexample no-pressure status",
        ),
        (
            r"\b(?:this|the)?\s*(?:claim|theorem|route|statement)\s+"
            r"(?:was|is)\s+not\s+refuted\b",
            "refutation status",
        ),
        (
            r"\bno\s+(?:refutation|counterexample\s+pressure)\s+"
            r"(?:remain|remains|exists?|found)\b",
            "refutation status",
        ),
        (
            r"\b(?:this|the)?\s*(?:claim|theorem|route|statement)\s+"
            r"(?:is\s+)?unrefuted\b",
            "refutation status",
        ),
        (
            r"\b(?:this|the)\s+establishes\s+"
            r"(?:this|the)?\s*(?:claim|theorem|route|statement)\b",
            "current theorem status",
        ),
        (
            r"\b(?:this|the)?\s*proof\s+establishes\s+"
            r"(?:this|the)?\s*(?:claim|theorem|route|statement)\b",
            "proof status",
        ),
        (
            r"\b(?:this|the)?\s*(?:claim|theorem|route|statement)\s+"
            r"(?:(?:is|has\s+been)\s+)?established\b",
            "current theorem status",
        ),
        (
            r"\b(?:this|the)?\s*(?:claim|theorem|route|statement)\s+"
            r"(?:is\s+)?false\b",
            "current theorem/refutation status",
        ),
        (
            r"\b(?:this|the)?\s*(?:claim|theorem|route|statement)\s+holds\b",
            "current theorem status",
        ),
        (
            r"\b(?:this|the)?\s*(?:disproves|contradicts)\s+"
            r"(?:this|the)?\s*(?:claim|theorem|route|statement)\b",
            "refutation status",
        ),
        (
            r"\b(?:claim|theorem|route|statement)\s+is\s+true\b",
            "current theorem truth",
        ),
        (
            r"\b(?:this|the)?\s*(?:claim|theorem|route|statement)\s+"
            r"(?:is\s+)?settled\b",
            "current theorem status",
        ),
        (
            r"\b(?:this|the)?\s*(?:paper|source|reference)\s+"
            r"(?:supports?|confirms?)\s+"
            r"(?:this|the)?\s*(?:claim|route|step|theorem)\b",
            "source support status",
        ),
        (
            r"\b(?:this|the)?\s*(?:paper|source|reference)\s+"
            r"(?:establishes|validates)\s+"
            r"(?:this|the)?\s*(?:claim|route|step|theorem|proof\s+step)"
            r"(?:\s+[a-z0-9._-]+)?\b",
            "source support status",
        ),
        (
            r"\b(?:literature\s+candidate|paper|source|reference|theorem)\s+"
            r"is\s+(?:source\s+support|reference\s+support|support)\b",
            "source/theorem support status",
        ),
        (
            r"\b(?:this|the)?\s*(?:literature\s+candidate|theorem)\s+"
            r"(?:supports?|confirms?)\s+"
            r"(?:this|the)?\s*(?:claim|route|step|theorem)\b",
            "source/theorem support status",
        ),
        (
            r"\b(?:this|the)?\s*(?:source|reference)\s+verifies\s+"
            r"(?:this|the)?\s*(?:claim|route|step|theorem)\b",
            "source support status",
        ),
        (
            r"\b[a-z][a-z0-9._ -]*\s+theorem\s+verifies\s+"
            r"(?:this|the)?\s*(?:claim|route|step|theorem)\b",
            "theorem applicability status",
        ),
        (
            r"\b(?:this|the)?\s*(?:claim|route|step|theorem)\s+"
            r"(?:is\s+)?supported\s+by\s+(?:the\s+)?"
            r"(?:cited\s+)?(?:paper|source|reference|candidate\s+reference)\b",
            "source support status",
        ),
        (
            r"\b(?:this|the)?\s*(?:source|reference|candidate\s+reference)\s+"
            r"backs\s+(?:this|the)?\s*(?:claim|route|step|theorem)\b",
            "source support status",
        ),
        (
            r"\bsource\s+status\s+is\s+(?:source\s+backed|backed|supported)\b",
            "source support status",
        ),
        (r"\bsource\s+is\s+(?:source\s+backed|backed|supported)\b", "source support status"),
        (
            r"\b(?:this|the)?\s*(?:claim|route|step|theorem)\s+"
            r"has\s+(?:source|reference)\s+support\b",
            "source support status",
        ),
        (
            r"\bstep\s+[a-z0-9._-]+\s+has\s+(?:source|reference)\s+support\b",
            "source support status",
        ),
        (
            r"\b(?:this|the)?\s*(?:claim|route|step|theorem)\s+"
            r"(?:is\s+)?source\s+supported\b",
            "source support status",
        ),
        (
            r"\b(?:theorem\s+)?applicability\s+(?:status\s+)?is\s+applicable\b",
            "theorem applicability status",
        ),
        (
            r"\b(?:this|the)?\s*theorem\s+is\s+applicable\b",
            "theorem applicability status",
        ),
        (r"\btheorem\s+applicable\s+here\b", "theorem applicability status"),
        (
            r"\bapplicable\s+theorem\s+for\s+(?:this|the)\s+"
            r"(?:step|claim|route)\b",
            "theorem applicability status",
        ),
        (
            r"\btheorem\s+is\s+applicable\s+to\s+(?:this|the)\s+(?:step|claim|route)\b",
            "theorem applicability status",
        ),
        (
            r"\btheorem\s+applies\s+to\s+(?:this|the)\s+(?:step|claim|route)\b",
            "theorem applicability status",
        ),
        (
            r"\btheorem\s+applies\s+(?:for\s+(?:this|the)\s+"
            r"(?:step|claim|route)|to\s+(?:this|the)\s+"
            r"(?:step|claim|route))\b",
            "theorem applicability status",
        ),
        (
            r"\btheorem\s+[a-z0-9._-]+\s+applies\s+to\s+"
            r"(?:step\s+[a-z0-9._-]+|(?:this|the)\s+(?:step|claim|route))\b",
            "theorem applicability status",
        ),
        (r"\btheorem\s+applies\s+here\b", "theorem applicability status"),
        (r"\btheorem\s+applies\b", "theorem applicability status"),
        (
            r"\btheorem\s+applies\s+(?:is\s+)?(?:true|yes|applicable)\b",
            "theorem applicability status",
        ),
        (
            r"\bproof\s+status\s+is\s+"
            r"(?:complete|checked|verified|proved|proven|source\s+checked(?:\s+argument)?)\b",
            "proof status",
        ),
        (
            r"\b(?:this|the)?\s*proof\s+is\s+complete\b",
            "proof-complete status",
        ),
        (
            r"\b(?:this|the)\s+gives\s+a\s+proof\s+of\s+"
            r"(?:this|the)?\s*(?:claim|theorem|route|statement)\b",
            "proof status",
        ),
        (
            r"\b(?:this\s+)?theorem\s+gives\s+(?:this|the)?\s*proof\b",
            "theorem/proof status",
        ),
        (
            r"\b(?:this|the)?\s*theorem\s+covers\s+(?:this|the)\s+case\b",
            "theorem applicability status",
        ),
        (
            r"\b(?:this|the)?\s*proof\s+is\s+valid\b",
            "proof status",
        ),
        (
            r"\b(?:route|strategy)\s+[a-z0-9._-]+\s+"
            r"(?:is\s+(?:a\s+)?proof\s+of\s+(?:this|the)?\s*"
            r"(?:claim|theorem|statement)|gives\s+(?:this|the)?\s*proof)\b",
            "route proof status",
        ),
        (r"\b(?:this|the)?\s*proof\s+checks\s+out\b", "proof status"),
        (
            r"\bno\s+proof\s+gaps?\s+remain\b",
            "proof-complete status",
        ),
        (
            r"\b(?:reviewer|prover)\s+"
            r"(?:approved|certified|completed|verified|checked)\s+(?:this|the)?\s*proof\b",
            "proof status",
        ),
        (
            r"\b(?:reviewer|prover)\s+[a-z0-9._-]+\s+"
            r"(?:approved|certified|completed|verified|checked)\s+"
            r"(?:proof|the\s+proof|proof\s+of\s+step\s+[a-z0-9._-]+)\b",
            "proof status",
        ),
        (
            r"\bprover\s+(?:found|produced)\s+a\s+proof\b",
            "proof status",
        ),
        (
            r"\b(?:paper|source|reference)\s+[a-z0-9._-]+\s+"
            r"supports?\s+(?:proof\s+)?step\s+[a-z0-9._-]+\b",
            "source/proof support status",
        ),
        (r"\b(?:this|the)\s+is\s+a\s+complete\s+argument\b", "proof-complete status"),
        (r"\bexamples?\s+pass\b", "example evidence status"),
        (
            r"\bhuman\s+(?:preference|choice|feedback|marked|chose)\b.*\b"
            r"(?:supports?|evidence|proof|proves?|refutes?|true|false)\b",
            "human preference as evidence",
        ),
        (
            r"\bhuman\s+(?:preference|preferred)\b.*\b(?:as\s+evidence|evidence)\b",
            "human preference as evidence",
        ),
        (
            r"\bhuman\s+preference\s+shows\b.*\b(?:route|claim|theorem|statement)\b.*\bworks\b",
            "human preference as evidence",
        ),
        (
            r"\bproof\s+language\s+status\s+is\s+"
            r"(?:source\s+checked(?:\s+argument)?|checked|complete|verified|proved|proven)\b",
            "proof language status",
        ),
        (
            r"\bgrade(?:\s+status)?\s+is\s+"
            r"(?:promising|complete|accepted|passing|pass|verified|proof\s+complete|source\s+backed)\b",
            "grade status",
        ),
    ]
    for pattern, reason in status_patterns:
        if re.search(pattern, normalized):
            return reason
    return None


__all__ = [
    'BlockReason',
    'LLMAgentCallDiagnostic',
    'LLMAgentProviderDiagnostic',
    'LLMAgentRunReport',
    'ProofGoalGraph',
    'ProverLoopAttempt',
    'ProverLoopBlocker',
    'ProverLoopBudget',
    'ProverLoopReport',
    'ProverLoopReviewPointer',
    'ProverRoundIndex',
    'ProverRoundIndexEntry',
    'ProverRoundReport',
    'SubgoalEdge',
    'SubgoalGraphGeneratedFrom',
    'SubgoalGraphSummary',
    'SubgoalNode',
    'SubgoalRole',
    'SubgoalState',
    '_MEMORY_FORBIDDEN_FIELD_TOKENS',
    '_SUBGOAL_BLOCKED_STATES',
    '_SUBGOAL_EVIDENCE_STATES',
    '_SUBGOAL_FORBIDDEN_TOKENS',
    '_memory_clause_is_disclaimer',
    '_memory_full_text_status_reason',
    '_memory_non_disclaimer_segments',
    '_memory_normalized_status_text',
    '_memory_text_clauses',
]
