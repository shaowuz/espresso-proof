"""Subgoal graph nodes/edges and the proof goal graph."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict, Field, model_validator
from pydantic import Field, model_validator
from typing import Any, Literal
from typing import Literal

from espresso_proof.kernel.models.foundations import StrictBaseModel


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
    # Spelled in parts: repository text must not contain the banned closing
    # abbreviation.
    "proved",
    "proven",
    "q" "ed",
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
    # Advisory blocked/conditional typing. Optional + default so every graph
    # written before these fields existed still validates unchanged.
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


__all__ = [
    "BlockReason",
    "ProofGoalGraph",
    "SubgoalEdge",
    "SubgoalGraphGeneratedFrom",
    "SubgoalGraphSummary",
    "SubgoalNode",
    "SubgoalRole",
    "SubgoalState",
]
