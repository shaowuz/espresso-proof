"""Line-numbered proof, gap resolution, agent consensus."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict, Field, model_validator
from pydantic import Field, model_validator
from typing import Any, Literal
from typing import Literal
import re

from espresso_proof.kernel.models.foundations import StrictBaseModel


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


__all__ = [
    "AgentConsensusEntry",
    "AgentConsensusReview",
    "AgentConsensusVerdict",
    "LineNumberedProof",
    "PROOF_CONSENSUS_REVIEWERS",
    "ProofGapResolutionCandidate",
    "ProofGapResolutionReport",
    "ProofLine",
    "ProofLineDependency",
]
