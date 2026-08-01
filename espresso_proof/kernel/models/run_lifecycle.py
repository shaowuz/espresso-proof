"""Run manifest/state, workflow spec, stage status, pressure lanes/status, agent run status, memory index."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path, PureWindowsPath
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict, Field, model_validator
from pydantic import Field, model_validator
from typing import Any, Literal
from typing import Literal
import re

from espresso_proof.kernel.models.foundations import FlexibleBaseModel, StrictBaseModel


def _parse_stage_status_timestamp(value: str, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"stage_status {field_name} must be ISO 8601") from exc

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

def _memory_forbidden_text_reason(text: str) -> str | None:
    full_text_reason = _memory_full_text_status_reason(text)
    if full_text_reason:
        return full_text_reason

    for clause in _memory_text_clauses(text):
        no_pressure_patterns = [
            (r"\bno\s+counterexamples?\s+(?:remain|exists?|found|pressure)\b"),
            (r"\bcounterexample[_ -]?pressure\s*(?:is|=|:)\s*(?:none|clear|resolved)\b"),
        ]
        if any(re.search(pattern, clause) for pattern in no_pressure_patterns):
            return "counterexample no-pressure status"
        if _memory_clause_is_disclaimer(clause):
            segments = _memory_non_disclaimer_segments(clause)
            if not segments:
                continue
            clause = " ".join(segments)
        if any(token in clause for token in _MEMORY_FORBIDDEN_FIELD_TOKENS):
            return "trusted status field token"
        status_patterns = [
            (r"\b(?:proved|proven|proves|verified|refuted)\b", "proof/refutation status"),
            (r"\b(?:complete proof|proof complete|proof-close)\b", "proof-complete status"),
            (r"\b(?:claim|theorem|route|statement)\s+is\s+true\b", "current theorem truth"),
            (r"\btheorem\s+(?:is\s+)?(?:established|settled)\b", "current theorem status"),
            (r"\bsource[_ -]?backed\b", "source support status"),
            (
                r"\bsource\s+status\s+(?:is|=|:)\s*(?:source[_ -]?backed|backed|supported)\b",
                "source support status",
            ),
            (r"\breference\s+supports?\s+(?:this|the)\s+(?:claim|route|step)\b", "source support status"),
            (
                r"\b(?:theorem\s+)?applicability(?:\s+status)?\s+"
                r"(?:is|=)\s+applicable\b",
                "theorem applicability status",
            ),
            (
                r"\bapplicability[_ -]?status\s*(?:is|=|:)\s*applicable\b",
                "theorem applicability status",
            ),
            (r"\btheorem\s+applies\s+to\s+(?:this|the)\s+step\b", "theorem applicability status"),
            (r"\bapplies\s+to\s+(?:this|the)\s+claim\b", "theorem applicability status"),
            (r"\bchecked\s+evidence\b", "checked evidence status"),
            (r"\bexamples?\s+(?:survived|failed|checked)\b", "example evidence status"),
            (r"\b(?:survived|failed|checked)\s+examples?\b", "example evidence status"),
            (r"\bcounterexamples?\s+refutes?\b", "refutation status"),
        ]
        for pattern, reason in status_patterns:
            if re.search(pattern, clause):
                return reason
    return None

def _validate_memory_text_boundary(where: str, values: list[str]) -> None:
    for value in values:
        reason = _memory_forbidden_text_reason(value)
        if reason:
            raise ValueError(f"{where} cannot store current mathematical status ({reason})")

def _has_run_artifact_provenance(provenance: list[str]) -> bool:
    for item in provenance:
        parts = Path(str(item)).parts
        if (
            "projects" in parts
            and "runs" in parts
            and len(parts) >= parts.index("runs") + 3
        ):
            return True
    return False

class MemoryEntry(StrictBaseModel):
    id: str
    timestamp: str
    project: str
    type: Literal["failed_attempt", "tested_example", "counterexample", "source_gap", "repaired_claim", "user_decision", "theorem_applicability_failure", "reviewer_objection", "human_example_feedback"]
    claim_id: str
    # Which CLAIM this memory came from, as a hash of the normalized statement.
    # `claim_id` is a per-run slot name and is "claim_001" in every run of every
    # project, so comparing it told the retrieval layer that memory from an
    # unrelated claim was same-claim material needing no revalidation. Optional
    # for artifacts written before this field existed; absent means "unknown",
    # which the guard treats as a different claim (fail closed).
    claim_fingerprint: str = ""
    summary: str
    provenance: list[str]
    reusable_lesson: str

    @model_validator(mode="after")
    def validate_memory_entry(self) -> MemoryEntry:
        text_fields = {
            "id": self.id,
            "timestamp": self.timestamp,
            "project": self.project,
            "claim_id": self.claim_id,
            "summary": self.summary,
            "reusable_lesson": self.reusable_lesson,
        }
        blank = [name for name, value in text_fields.items() if not str(value).strip()]
        if blank:
            raise ValueError(f"memory entry fields cannot be blank: {blank}")
        if not self.provenance:
            raise ValueError("memory entry requires provenance")
        if not all(str(item).strip() for item in self.provenance):
            raise ValueError("memory entry provenance cannot be blank")
        if not _has_run_artifact_provenance(self.provenance):
            raise ValueError("memory entry provenance requires a run artifact reference")
        _validate_memory_text_boundary(
            "memory entry",
            [self.summary, self.reusable_lesson, *self.provenance],
        )
        return self

class MemoryContextEntry(StrictBaseModel):
    """One prior-memory entry deemed relevant to the current run, with an advisory
    recommended use. It carries NO source/evidence/proof status field, so reusing
    memory can only change ranking / time-allocation / what-to-test-first, never a
    trusted status."""

    id: str
    type: Literal[
        "failed_attempt", "tested_example", "counterexample", "source_gap",
        "repaired_claim", "user_decision", "theorem_applicability_failure",
        "reviewer_objection", "human_example_feedback",
    ]
    relevance_reason: str
    recommended_use: Literal["avoid", "retest", "request_source", "prioritize", "ask_human", "note"]
    summary: str
    memory_claim_id: str
    provenance: list[str]
    revalidation_required: bool
    revalidated_against_locked_claim: bool

    @model_validator(mode="after")
    def validate_memory_context_entry(self) -> MemoryContextEntry:
        if not self.provenance:
            raise ValueError("memory context entry requires provenance")
        if not all(str(item).strip() for item in self.provenance):
            raise ValueError("memory context entry provenance cannot be blank")
        if not _has_run_artifact_provenance(self.provenance):
            raise ValueError(
                "memory context entry provenance requires a run artifact reference"
            )
        _validate_memory_text_boundary(
            "memory context entry",
            [
                self.relevance_reason,
                self.summary,
                self.memory_claim_id,
                *self.provenance,
            ],
        )
        if self.revalidated_against_locked_claim and self.revalidation_required:
            raise ValueError("memory context cannot be both revalidated and pending revalidation")
        return self

class MemoryContext(StrictBaseModel):
    """Advisory memory influence for a run: prior-run lessons matched to this
    claim, with recommended uses. Advisory only — never upgrades any status and
    never overrides current-run artifacts (StrictBaseModel forbids status fields)."""

    schema_version: int = 2
    claim_id: str
    run_id: str
    advisory_only: Literal[True] = True
    generated_at: str = ""
    matched_entries: list[MemoryContextEntry] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_memory_context(self) -> MemoryContext:
        _validate_memory_text_boundary("memory context constraints", self.constraints)
        return self

class MemoryIndexEntry(StrictBaseModel):
    id: str
    entry_path: str
    claim_id: str
    claim_fingerprint: str = ""
    type: Literal["failed_attempt", "tested_example", "counterexample", "source_gap", "repaired_claim", "user_decision", "theorem_applicability_failure", "reviewer_objection", "human_example_feedback"]
    tags: list[str] = Field(default_factory=list)
    summary: str
    provenance: list[str]

    @model_validator(mode="after")
    def validate_memory_index_entry(self) -> MemoryIndexEntry:
        if not self.id.strip() or not self.claim_id.strip() or not self.entry_path.strip():
            raise ValueError("memory index entry requires id, claim_id, and entry_path")
        if not self.provenance:
            raise ValueError("memory index entry requires provenance")
        if not _has_run_artifact_provenance(self.provenance):
            raise ValueError("memory index entry provenance requires a run artifact reference")
        _validate_memory_text_boundary(
            "memory index entry",
            [self.summary, self.entry_path, *self.tags, *self.provenance],
        )
        return self

class MemoryIndex(StrictBaseModel):
    project_id: str
    last_updated: str
    lookup_keys: list[str] = Field(default_factory=list)
    entries: list[MemoryIndexEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_entries(self) -> MemoryIndex:
        ids = [entry.id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("memory index entry ids must be unique")
        return self

class RunState(str, Enum):
    """Closed, kernel-enforced set of run_manifest ``current_state`` values.

    A ``str`` Enum so every existing string comparison (``== "COMPLETED_MEMO"``)
    and ``ORCHESTRATION_STATES.index(state)`` keeps working, and a plain YAML
    string coerces to a member on load (StrictBaseModel only forbids extra keys;
    it is not strict-typed). ``WAITING_HUMAN_MAJOR_DECISION`` is the legacy genesis
    state written by ``run_mock_critique``.

    Note: a prior ``EVIDENCE_FANOUT_RUNNING`` member (reserved for a never-built
    context-lane fan-out SEED/RUN/JOIN barrier) was removed — it was never emitted,
    was absent from ``ORCHESTRATION_STATES`` (so a persisted run carrying it gave a
    misleading "not at the human gate" error on resume), and the evidence fan-out is
    intentionally opt-in (no barrier). A persisted run with that string now fails
    enum validation as an unknown value (clean fail-closed). Re-add this member only
    when the real fan-out barrier is actually implemented and wired into
    ``ORCHESTRATION_STATES`` with resume tests.
    """

    SCAFFOLDED = "SCAFFOLDED"
    WAITING_HUMAN_MAJOR_DECISION = "WAITING_HUMAN_MAJOR_DECISION"
    WAITING_CLAIM_CLARIFICATION = "WAITING_CLAIM_CLARIFICATION"
    FRONT_HALF_DONE = "FRONT_HALF_DONE"
    CLAIM_CLEAR = "CLAIM_CLEAR"
    LITERATURE_DONE = "LITERATURE_DONE"
    WAITING_LLM_AGENT_HUMAN_GATE = "WAITING_LLM_AGENT_HUMAN_GATE"
    HUMAN_CHOICE_CONTINUE_SELECTED = "HUMAN_CHOICE_CONTINUE_SELECTED"
    HUMAN_CHOICE_REVISE_SELECTED = "HUMAN_CHOICE_REVISE_SELECTED"
    HUMAN_CHOICE_STOP_SELECTED = "HUMAN_CHOICE_STOP_SELECTED"
    # Honest auto-provenance peer of HUMAN_CHOICE_CONTINUE_SELECTED: machinery
    # (not a human) selected the continue route. Written by
    # orchestrate_run(auto_continue=True) just before the same-process resume;
    # a persisted run carrying it validates and resumes. It must NEVER be
    # spelled as a HUMAN_CHOICE_* state — that would lie about provenance.
    AUTO_CHOICE_CONTINUE_SELECTED = "AUTO_CHOICE_CONTINUE_SELECTED"
    # The prover loop stopped to ASK the human a scheduling question (repeated
    # reviewer objections on a route). The run is blocked on a person, not
    # finished: finalizing it to COMPLETED_MEMO made the question terminal and
    # forfeited the rest of the approved budget, because every re-entry path
    # refuses a completed run. Sits after the choice states and before
    # COMPLETED_MEMO; `espresso answer-scheduler` moves it forward.
    WAITING_TIME_ALLOCATION_DECISION = "WAITING_TIME_ALLOCATION_DECISION"
    COMPLETED_MEMO = "COMPLETED_MEMO"

class RunManifest(StrictBaseModel):
    run_id: str
    project_id: str
    created_at: str
    current_state: RunState
    input_file: str
    artifacts: dict[str, str]
    optional_artifacts: dict[str, str] = Field(default_factory=dict)
    dependencies: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    retries: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # The human's declared spend ceiling for this run, in model tokens. Advisory
    # SCHEDULING input only: it widens which effort tiers a route may be approved
    # for. It carries no mathematical authority and can never grant or raise a
    # proof, source, or evidence status.
    token_budget: int | None = None

    @model_validator(mode="after")
    def validate_token_budget(self) -> RunManifest:
        if self.token_budget is not None and self.token_budget <= 0:
            raise ValueError("token_budget must be a positive number of tokens")
        return self

    @model_validator(mode="after")
    def validate_required_artifacts(self) -> RunManifest:
        required = {
            "locked_claim",
            "agent_contracts",
            "hypothesis_ledger",
            "literature_review",
            "examples",
            "computation_checks",
            "counterexamples",
            "tests",
            "source_grounding",
            "review",
            "idea_grade",
            "loop_control",
            "strategy_synthesis",
            "strategy_brief",
            "human_choice",
            "final_report",
        }
        missing = required.difference(self.artifacts)
        if missing:
            raise ValueError(f"missing run artifacts: {sorted(missing)}")
        return self

class CoreCorpusItem(StrictBaseModel):
    id: str
    title: str
    url: str
    role: Literal["core", "background", "example_source", "counterexample_source"]

class WorkflowSpec(FlexibleBaseModel):
    name: str
    mode: str
    status: Literal["draft", "human_confirmed", "deprecated"]
    domain: str
    core_corpus: list[CoreCorpusItem]
    agent_flow: list[str]
    default_critique_flow: list[str]
    required_examples: list[str]
    canonical_example_templates: list[str]
    pathology_templates: list[str]
    test_operators: list[str]
    non_goals: list[str]

    @model_validator(mode="after")
    def validate_non_goals(self) -> WorkflowSpec:
        joined = " ".join(self.non_goals).lower()
        if "lean formalization" not in joined:
            raise ValueError("workflow must explicitly reject Lean formalization as a default goal")
        if not self.canonical_example_templates:
            raise ValueError("workflow requires canonical example templates")
        if not self.test_operators:
            raise ValueError("workflow requires test operators")
        return self

class FrontHalfStageResult(StrictBaseModel):
    """Audit record for ONE front-half stage. Process evidence only.

    ``extra="forbid"`` (StrictBaseModel) rejects any status-upgrade field such as
    ``source_backed`` / ``checked`` / ``proved`` / ``human_confirmed`` /
    ``refutes_claim`` / ``lean_checked`` / ``proof_status``: this record can never
    carry mathematical status. ``unsafe_language`` is an internal downgrade
    ledger, not proof text.
    """

    stage: str
    status: Literal["ok", "skipped", "error"]
    input_artifacts: list[str] = Field(default_factory=list)
    output_artifacts: list[str] = Field(default_factory=list)
    validation: Literal["ok", "error", "not_run"] = "not_run"
    unsafe_language: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_stage_result(self) -> FrontHalfStageResult:
        # An error stage can never be silently recorded as a passing validation.
        if self.status == "error" and self.validation == "ok":
            raise ValueError("error stage cannot have validation=ok")
        for path in (*self.input_artifacts, *self.output_artifacts):
            if path.startswith("/") or ".." in path.split("/"):
                raise ValueError(f"front_half_run artifact path must be run-relative: {path!r}")
        return self

class FrontHalfRunReport(StrictBaseModel):
    """Durable, audit-only record of a front-half agent pass (``front_half_run``).

    It records WHICH front-half stage produced what and any unsafe-language
    downgrades, for debugging and auditing. It is evidence about process, not
    about the theorem: it must not affect grades, proof/source/evidence status,
    or lifecycle state, and (via StrictBaseModel) cannot carry a status-upgrade
    field.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    provider: str
    model: str
    stages: list[FrontHalfStageResult]
    notes: list[str] = Field(default_factory=list)

StageProcessStatus = Literal[
    "not_started",
    "queued",
    "running",
    "done",
    "skipped",
    "blocked",
    "failed",
]

StageEvidenceRole = Literal[
    "none",
    "candidate_only",
    "checked_evidence_possible",
    "proof_candidate_only",
    "advisory_only",
    "presentation_only",
]

class StageStatusEntry(StrictBaseModel):
    """Process-status projection for one conceptual AGENT_FLOW stage.

    This is deliberately not a proof/source/evidence status object. It says
    whether a process stage has been observed, and may bind process artifacts by
    hash so stale UI/trace projections fail closed. StrictBaseModel rejects
    extra status-smuggling fields by construction.
    """

    stage_id: str
    display_name: str
    status: StageProcessStatus = "not_started"
    status_reason: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    reads_from: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    parallel_group: str | None = None
    evidence_role: StageEvidenceRole = "none"
    advisory_only: Literal[True] = True
    claim_clear_blocked: bool = False
    error_summary: str | None = None
    artifact_hashes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_process_shape(self) -> StageStatusEntry:
        if self.status == "running" and self.started_at is None:
            raise ValueError("running stage_status entry requires started_at")
        if self.status == "running" and self.finished_at is not None:
            raise ValueError("running stage_status entry cannot have finished_at")
        if self.status in {"done", "skipped", "blocked", "failed"} and self.finished_at is None:
            raise ValueError(f"{self.status} stage_status entry requires finished_at")
        if self.status in {"not_started", "queued"} and self.started_at is not None:
            raise ValueError(f"{self.status} stage_status entry cannot have started_at")
        if self.status in {"not_started", "queued"} and self.finished_at is not None:
            raise ValueError(f"{self.status} stage_status entry cannot have finished_at")
        started = (
            _parse_stage_status_timestamp(self.started_at, "started_at")
            if self.started_at is not None
            else None
        )
        finished = (
            _parse_stage_status_timestamp(self.finished_at, "finished_at")
            if self.finished_at is not None
            else None
        )
        if started is not None and finished is not None:
            try:
                finished_before_started = finished < started
            except TypeError as exc:
                raise ValueError(
                    "stage_status timestamps must use comparable timezone offsets"
                ) from exc
            if finished_before_started:
                raise ValueError("stage_status finished_at cannot be before started_at")
        if self.status == "failed" and not (self.error_summary or self.status_reason):
            raise ValueError("failed stage_status entry requires error_summary or status_reason")
        if self.status == "blocked" and not (
            self.status_reason or self.error_summary or self.claim_clear_blocked
        ):
            raise ValueError("blocked stage_status entry requires a reason")
        if self.claim_clear_blocked and self.status not in {"blocked", "skipped"}:
            raise ValueError("claim_clear_blocked stage must be blocked or skipped")
        for path in (*self.outputs, *self.artifact_hashes):
            raw_path = str(path)
            path_obj = Path(raw_path)
            windows_path = PureWindowsPath(raw_path)
            if (
                path_obj.is_absolute()
                or windows_path.is_absolute()
                or bool(windows_path.drive)
                or bool(windows_path.root)
            ):
                raise ValueError(f"stage_status path must be run-relative: {path!r}")
            if ".." in path_obj.parts or ".." in windows_path.parts:
                raise ValueError(f"stage_status path cannot contain '..': {path!r}")
            if "\\" in raw_path:
                raise ValueError(
                    f"stage_status path must use POSIX '/' separators: {path!r}"
                )
        for artifact_hash in self.artifact_hashes.values():
            if artifact_hash != "missing" and not artifact_hash.strip():
                raise ValueError("stage_status artifact hash must be non-empty")
        return self

class StageStatusReport(StrictBaseModel):
    """AGENT_FLOW process-status projection for one run.

    Optional/additive. The report is advisory process metadata only; it must
    never own proof/source/evidence/refutation/grade status. Validators bind it
    to the current run, AGENT_FLOW spec, and artifact hashes.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    advisory_only: Literal[True] = True
    effect: Literal["process_status_only"] = "process_status_only"
    stages: list[StageStatusEntry]
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_stages(self) -> StageStatusReport:
        _parse_stage_status_timestamp(self.generated_at, "generated_at")
        ids = [stage.stage_id for stage in self.stages]
        if len(ids) != len(set(ids)):
            raise ValueError("stage_status entries must be unique by stage_id")
        return self

PressureLaneBarrierState = Literal[
    "seeded",
    "running",
    "joining",
    "completed",
    "failed",
]

PressureLaneBarrierEntryStatus = Literal[
    "seeded",
    "computed",
    "compute_failed",
    "committing",
    "committed",
    "failed",
]

PressureLaneBarrierAgent = Literal[
    "literature",
    "examples",
    "counterexamples",
    "failure_patterns",
]

PressureLaneBarrierRole = Literal[
    "pressure_lane",
    "dependent_audit",
]

class PressureLaneBarrierEntry(StrictBaseModel):
    """One lane in the explicit pressure-lane execution barrier.

    This is process state for scheduling only. A worker may compute an in-memory
    candidate, but the candidate is never serialized here; only the coordinator
    may later commit artifacts through the existing validated single-agent path.
    """

    agent: PressureLaneBarrierAgent
    stage_id: str
    role: PressureLaneBarrierRole
    status: PressureLaneBarrierEntryStatus = "seeded"
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    error_summary: str | None = None
    worker_shared_write: Literal[False] = False
    candidate_persisted: Literal[False] = False

    @model_validator(mode="after")
    def validate_entry_shape(self) -> PressureLaneBarrierEntry:
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("pressure lane barrier duration_ms must be non-negative")
        if self.status in {"committed", "failed", "computed", "compute_failed"}:
            if self.finished_at is None:
                raise ValueError(
                    f"{self.status} pressure lane barrier entry requires finished_at"
                )
        if self.status in {"seeded", "committing"} and self.finished_at is not None:
            raise ValueError(
                f"{self.status} pressure lane barrier entry cannot have finished_at"
            )
        if self.status in {"compute_failed", "failed"} and not self.error_summary:
            raise ValueError(
                f"{self.status} pressure lane barrier entry requires error_summary"
            )
        if self.status in {"computed", "committed"} and self.error_summary:
            raise ValueError(
                f"{self.status} pressure lane barrier entry cannot carry error_summary"
            )
        return self

class PressureLaneBarrierReport(StrictBaseModel):
    """Durable SEED/RUN/JOIN/RESUME barrier for explicit pressure-lane batches.

    Optional/additive and advisory-only. It records deterministic scheduling
    state, not mathematical evidence. It cannot carry source/evidence/proof/
    grade status, and it never stores worker candidates.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    updated_at: str
    mode: Literal["explicit_batch_parallel_compute_serial_commit"]
    barrier_state: PressureLaneBarrierState
    requested_agents: list[PressureLaneBarrierAgent]
    commit_order: list[PressureLaneBarrierAgent]
    entries: list[PressureLaneBarrierEntry]
    advisory_only: Literal[True] = True
    effect: Literal["scheduling_only"] = "scheduling_only"
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_report_shape(self) -> PressureLaneBarrierReport:
        if not self.requested_agents:
            raise ValueError("pressure lane barrier must request at least one agent")
        if not self.entries:
            raise ValueError("pressure lane barrier must contain at least one entry")
        if len(self.requested_agents) != len(set(self.requested_agents)):
            raise ValueError("pressure lane barrier requested_agents must be unique")
        if len(self.commit_order) != len(set(self.commit_order)):
            raise ValueError("pressure lane barrier commit_order must be unique")
        if set(self.requested_agents) != set(self.commit_order):
            raise ValueError(
                "pressure lane barrier commit_order must cover requested_agents"
            )
        if tuple(self.requested_agents) != tuple(self.commit_order):
            raise ValueError(
                "pressure lane barrier requested_agents must follow commit_order"
            )
        entry_agents = [entry.agent for entry in self.entries]
        if len(entry_agents) != len(set(entry_agents)):
            raise ValueError("pressure lane barrier entries must be unique by agent")
        if set(entry_agents) != set(self.requested_agents):
            raise ValueError(
                "pressure lane barrier entries must cover requested_agents"
            )
        if tuple(entry_agents) != tuple(self.commit_order):
            raise ValueError("pressure lane barrier entries must follow commit_order")
        entry_statuses = {entry.status for entry in self.entries}
        allowed_by_state = {
            "seeded": {"seeded"},
            "running": {"seeded", "computed", "compute_failed"},
            "joining": {
                "seeded",
                "computed",
                "compute_failed",
                "committing",
                "committed",
                "failed",
            },
        }
        invalid_statuses = entry_statuses - allowed_by_state.get(
            self.barrier_state,
            entry_statuses,
        )
        if invalid_statuses:
            raise ValueError(
                f"{self.barrier_state} pressure lane barrier cannot contain "
                f"entry status(es): {', '.join(sorted(invalid_statuses))}"
            )
        commit_phase_statuses = {"committing", "committed", "failed"}
        if self.barrier_state == "joining" and entry_statuses.isdisjoint(
            commit_phase_statuses
        ):
            raise ValueError(
                "joining pressure lane barrier requires at least one "
                "commit-phase entry"
            )
        if self.barrier_state == "completed" and entry_statuses != {"committed"}:
            raise ValueError(
                "completed pressure lane barrier requires every entry committed"
            )
        if self.barrier_state == "failed" and "failed" not in entry_statuses:
            raise ValueError(
                "failed pressure lane barrier requires at least one failed entry"
            )
        return self

class AgentRunStatusEntry(StrictBaseModel):
    """Process status for ONE evidence / understanding agent.

    This record tracks ONLY process state for the on-demand / parallel evidence
    runner and the dashboard. ``extra="forbid"`` (StrictBaseModel) rejects any
    mathematical status-upgrade field (``source_backed`` / ``checked`` /
    ``proved`` / ``refutes_claim`` / ``proof_status`` / ``grade`` / ...): an agent
    can never carry mathematical status here.

    ``safe_to_use_as_evidence`` is a DISPLAY gate, not a status. It may be True
    only when ``status == 'done'`` (and the validator additionally requires the
    artifact to exist). Even when True it never upgrades a kernel status: the
    dashboard/memo read evidence from the validated mathematical artifacts, never
    from this flag. A running / failed / skipped / not-run agent never counts as
    evidence.
    """

    agent: Literal[
        "claim_understanding",
        "literature",
        "examples",
        "counterexamples",
        "failure_patterns",
    ]
    status: Literal["not_run", "queued", "running", "done", "failed", "skipped"] = "not_run"
    started_at: str | None = None
    finished_at: str | None = None
    artifact_path: str | None = None
    error_summary: str | None = None
    attempts: int = 0
    last_updated: str
    safe_to_use_as_evidence: bool = False

    @model_validator(mode="after")
    def validate_entry(self) -> AgentRunStatusEntry:
        if self.safe_to_use_as_evidence and self.status != "done":
            raise ValueError(
                "safe_to_use_as_evidence may be True only when status='done'"
            )
        if self.attempts < 0:
            raise ValueError("attempts must be non-negative")
        if self.artifact_path is not None:
            path = self.artifact_path
            if path.startswith("/") or ".." in path.split("/"):
                raise ValueError(
                    f"agent artifact_path must be run-relative: {path!r}"
                )
        return self

class AgentRunStatusReport(StrictBaseModel):
    """Per-agent run-status board for a run (``agent_run_status.yaml``).

    Advisory process audit only. It must never affect grades, proof/source/
    evidence status, or lifecycle state, and (via StrictBaseModel) cannot carry a
    status-upgrade field. ``validate_run`` binds it to the run and re-checks
    everything; nothing here can promote an artifact into evidence.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    last_updated: str
    agents: list[AgentRunStatusEntry]
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_report(self) -> AgentRunStatusReport:
        seen: set[str] = set()
        for entry in self.agents:
            if entry.agent in seen:
                raise ValueError(f"duplicate agent_run_status entry: {entry.agent}")
            seen.add(entry.agent)
        return self

PressureState = Literal[
    "pending_not_run_or_partial",
    "completed_no_candidate_found_in_scope",
    "candidate_present_needs_admissibility",
    "admissible_refutation",
    "inadmissible_pressure",
]

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


__all__ = [
    "AgentRunStatusEntry",
    "AgentRunStatusReport",
    "CoreCorpusItem",
    "FrontHalfRunReport",
    "FrontHalfStageResult",
    "MemoryContext",
    "MemoryContextEntry",
    "MemoryEntry",
    "MemoryIndex",
    "MemoryIndexEntry",
    "PressureLaneBarrierAgent",
    "PressureLaneBarrierEntry",
    "PressureLaneBarrierEntryStatus",
    "PressureLaneBarrierReport",
    "PressureLaneBarrierRole",
    "PressureLaneBarrierState",
    "PressureLateMarker",
    "PressureState",
    "PressureStatusEntry",
    "PressureStatusReport",
    "RunManifest",
    "RunState",
    "StageEvidenceRole",
    "StageProcessStatus",
    "StageStatusEntry",
    "StageStatusReport",
    "WorkflowSpec",
]
