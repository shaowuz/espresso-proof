"""Examples, computation checks/verification, failure patterns, judgment, counterexamples, tests."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import BaseModel, Field, model_validator
from pydantic import ConfigDict, Field, model_validator
from pydantic import Field, model_validator
from typing import Any, Literal
from typing import Literal
import re

from espresso_proof.kernel.models.foundations import ComputationStatus, ExampleStatus, StrictBaseModel


class InvariantValue(StrictBaseModel):
    value: str
    status: ComputationStatus
    source_ref: str | None = None

class ExampleHypotheses(StrictBaseModel):
    smooth: bool | Literal["unknown"]
    projective: bool | Literal["unknown"]
    proper: bool | Literal["unknown"]
    base_field: str | Literal["unknown"]
    characteristic: str | Literal["unknown"]
    additional: list[str] = Field(default_factory=list)

class ExampleRecord(StrictBaseModel):
    id: str
    name: str
    domain: str
    role: Literal["toy", "classical", "canonical", "boundary", "pathology", "counterexample_in_literature", "open_case"]
    source_refs: list[str] = Field(default_factory=list)
    hypotheses: ExampleHypotheses
    known_invariants: dict[str, InvariantValue] = Field(default_factory=dict)
    computable_fields: list[str] = Field(default_factory=list)
    what_it_tests: list[str]
    claim_applicability: Literal["applies", "does_not_apply", "unclear"]
    notes: str = ""

class ExampleBank(StrictBaseModel):
    version: float | str
    project_id: str
    purpose: str
    source_refs: list[SourceRef]
    examples: list[ExampleRecord]

class TestedExample(StrictBaseModel):
    id: str
    template_id: str
    name: str
    satisfies_all_claim_hypotheses: bool | Literal["unclear"]
    hypothesis_mismatches: list[str] = Field(default_factory=list)
    what_it_tests: list[str]
    expected_conclusion: str
    observed_or_inferred_behavior: str
    result_for_current_idea: ExampleStatus
    source_or_reason: str
    nontriviality: Literal["routine", "standard", "literature_counterexample", "research_level"]

class ExampleSelection(StrictBaseModel):
    claim_id: str
    examples: list[TestedExample]

class MachineCheckSpec(StrictBaseModel):
    """A machine-runnable specification a real verifier backend can execute.

    This is what lets ``computation_checked`` mean something: instead of an
    asserted ``result``, the check carries an ``expression`` the kernel can
    actually evaluate and compare against ``expected``.
    """

    kind: Literal[
        "exact_arithmetic",
        "permutation_order",
        "matrix_rank",
        "matrix_determinant",
        "polynomial_identity",
        "finite_enumeration",
    ]
    expression: str
    expected: str
    target_step_id: str | None = None
    notes: str = ""

class ExampleComputationCheck(StrictBaseModel):
    id: str
    example_id: str
    claim_id: str
    computed_quantity: str
    input_data: list[str] = Field(default_factory=list)
    method: Literal["exact_arithmetic", "CAS", "lean", "source_lookup", "manual_review_needed"]
    tool_or_source: str
    result: str
    cross_check: list[str] = Field(default_factory=list)
    status: ComputationStatus
    machine_check: MachineCheckSpec | None = None
    notes: str = ""

    @model_validator(mode="after")
    def validate_evidence_method(self) -> ExampleComputationCheck:
        evidence_statuses = {ComputationStatus.checked, ComputationStatus.source_checked}
        if self.status in evidence_statuses and self.method == "manual_review_needed":
            raise ValueError("manual_review_needed cannot support checked/source_checked evidence")
        if self.status == ComputationStatus.source_checked and self.method != "source_lookup":
            raise ValueError("source_checked evidence must use source_lookup")
        return self

class ComputationChecks(StrictBaseModel):
    claim_id: str
    checks: list[ExampleComputationCheck]

    @model_validator(mode="after")
    def validate_nested_claim_ids(self) -> ComputationChecks:
        mismatches = [
            check.id
            for check in self.checks
            if check.claim_id != self.claim_id
        ]
        if mismatches:
            raise ValueError(
                f"computation check claim_id mismatch: {sorted(mismatches)}"
            )
        return self

class VerificationVerdict(str, Enum):
    confirmed = "confirmed"
    refuted = "refuted"
    error = "error"
    unsupported = "unsupported"

class PolynomialWitness(StrictBaseModel):
    """A durable rational witness that a polynomial identity is FALSE over Q.

    Records an exact assignment of the declared variables and the exact lhs / rhs
    values plus their nonzero difference (all rationals serialized as strings, so
    no float rounding ever enters). This witness refutes ONLY the exact identity
    (the proof step); the scope flags on the persisted ``CounterexampleTest``
    enforce that it can never refute the broader claim.
    """

    assignment: dict[str, str]
    lhs_value: str
    rhs_value: str
    difference: str

class ComputationVerificationResult(StrictBaseModel):
    check_id: str
    claim_id: str
    kind: str
    expression: str = ""
    backend: str
    executed: bool
    verdict: VerificationVerdict
    computed: str
    expected: str
    detail: str = ""
    # Present only for a refuted polynomial_identity that yielded a witness.
    witness: PolynomialWitness | None = None

class ComputationVerificationReport(StrictBaseModel):
    """Output of a real verifier backend that actually executed machine checks.

    The verifier never upgrades status on its own. It only records, per check,
    whether an executed computation reproduced (``confirmed``) or contradicted
    (``refuted``) the claimed result. Validators then forbid a check from
    claiming ``checked`` when the machine refuted it.
    """

    schema_version: int = 1
    claim_id: str
    generated_at: str
    backend: str
    results: list[ComputationVerificationResult]

    @model_validator(mode="after")
    def validate_claim_ids(self) -> ComputationVerificationReport:
        mismatches = sorted(
            result.check_id
            for result in self.results
            if result.claim_id != self.claim_id
        )
        if mismatches:
            raise ValueError(f"verification result claim_id mismatch: {mismatches}")
        return self

class FailureCategory(str, Enum):
    claim_ambiguous = "claim_ambiguous"
    claim_refuted = "claim_refuted"
    strategy_failed_by_example = "strategy_failed_by_example"
    proof_gap = "proof_gap"
    theorem_condition_gap = "theorem_condition_gap"
    source_gap = "source_gap"
    misapplied_source = "misapplied_source"
    computation_unchecked = "computation_unchecked"
    budget_exhausted = "budget_exhausted"
    no_progress = "no_progress"
    human_blocked = "human_blocked"

class FailureClassification(StrictBaseModel):
    category: FailureCategory
    signal: str
    recommended_route: str
    explanation: str

class FailureClassificationReport(StrictBaseModel):
    """Typed classification of reviewer objections / failed tests into actionable
    failure categories with a routing recommendation.

    This is advisory only: it changes routing, never status. The model carries no
    source/evidence/proof status field, so a classification cannot upgrade
    anything. ``claim_refuted`` is allowed only when an admissible all-hypotheses
    counterexample exists (enforced by the validator), so a dropped-hypothesis or
    proof-step failure can never be classified as a claim refutation here.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    classifications: list[FailureClassification]
    notes: list[str] = Field(default_factory=list)

class JudgmentRiskKind(str, Enum):
    """The kind of mathematical risk a judgment finding flags."""

    claim_precision_risk = "claim_precision_risk"
    theorem_condition_risk = "theorem_condition_risk"
    missing_hypothesis_in_prose = "missing_hypothesis_in_prose"
    route_validity_risk = "route_validity_risk"
    unsupported_confidence = "unsupported_confidence"
    reviewer_objection_decisiveness = "reviewer_objection_decisiveness"
    reviewer_approval_without_verifier = "reviewer_approval_without_verifier"
    grade_route_conflict = "grade_route_conflict"

class JudgmentRiskTier(str, Enum):
    """How strong the advisory signal is. None of these upgrade any status.

    - ``info``: a note for the reader.
    - ``warn``: a caution / request more evidence.
    - ``block_candidate``: ADVISORY recommendation to keep a step candidate (i.e.
      NOT source_backed). It does not itself set or clear status -- the kernel's
      source-backing / applicability validators remain the only gate. It can never
      mark anything checked, source_backed, refuted, or proved.
    """

    info = "info"
    warn = "warn"
    block_candidate = "block_candidate"

class JudgmentAction(str, Enum):
    """A conservative next action the layer may recommend (advisory only)."""

    request_source = "request_source"
    request_example = "request_example"
    request_computation = "request_computation"
    request_human_review = "request_human_review"
    downgrade_confidence = "downgrade_confidence"
    none = "none"

class MathematicalJudgmentFinding(StrictBaseModel):
    kind: JudgmentRiskKind
    tier: JudgmentRiskTier
    subject: str
    signal: str
    recommended_action: JudgmentAction
    explanation: str

class MathematicalJudgmentReport(StrictBaseModel):
    """Conservative middle layer: records mathematical RISK judgments without
    becoming a proof verifier.

    Hard boundary by construction: this model carries NO source/evidence/proof
    status field (and StrictBaseModel forbids extras), so a judgment can only
    warn / request evidence / downgrade confidence / ask for human review. It can
    never set source_backed, mark a computation/example checked, mark a claim
    refuted, upgrade proof status, or emit proof-complete language. The kernel
    validators stay the only gate that earns any status.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    findings: list[MathematicalJudgmentFinding]
    notes: list[str] = Field(default_factory=list)

class FailurePatternTier(str, Enum):
    """How strong a failure pattern is allowed to act. None earns/clears status.

    - ``candidate``: WARN only (newly discovered / AI/reviewer-suggested patterns
      enter here; they may never block or change ranking).
    - ``audited``: may affect RANKING / TIME ALLOCATION only (advisory weight); it
      never sets proof/source/evidence status and never blocks.
    - ``hard``: maps to an EXISTING structural validator gate (named in
      ``enforced_by``). The hard pattern does not itself block; it documents that
      the kernel validator already fails closed on the unsafe use. A pattern can
      never be promoted to ``hard`` at run time, by memory, or by human input.
    """

    candidate = "candidate"
    audited = "audited"
    hard = "hard"

class FailurePatternAction(str, Enum):
    warn = "warn"
    rank_downgrade = "rank_downgrade"
    block_via_validator = "block_via_validator"

class FailurePattern(StrictBaseModel):
    """One catalogued mathematical failure pattern.

    Carries no status field: a pattern is a *risk shape*, never evidence of truth
    or falsehood. ``enforced_by`` is set only for ``hard`` patterns and must name
    an existing kernel validator (the pattern does not add new blocking logic).
    """

    id: str
    family: str
    summary: str
    tier: FailurePatternTier
    action: FailurePatternAction
    enforced_by: str = ""
    origin: Literal["seed", "ai_suggested", "reviewer_suggested"] = "seed"

class FailurePatternBank(StrictBaseModel):
    schema_version: Literal[1] = 1
    patterns: list[FailurePattern]

class FailurePatternMatch(StrictBaseModel):
    pattern_id: str
    family: str
    tier: FailurePatternTier
    action: FailurePatternAction
    subject: str
    signal: str
    explanation: str
    enforced_by: str = ""

class FailurePatternAudit(StrictBaseModel):
    """Per-run record of which catalogued failure patterns the run's signals match.

    Advisory by construction: no source/evidence/proof status field (and extras
    are forbidden), so a match can never upgrade any status and is never a
    counterexample / refutation. A ``hard`` match merely points at the existing
    validator that already fails closed; a ``candidate`` match warns; an
    ``audited`` match may inform ranking/time only. The validator re-checks that
    each match's tier/action/enforced_by equals the canonical seed bank, so a run
    cannot relabel a candidate pattern as hard.
    """

    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    matches: list[FailurePatternMatch]
    notes: list[str] = Field(default_factory=list)

class CounterexampleTest(StrictBaseModel):
    id: str
    operator_id: str
    example_id: str
    satisfies_all_claim_hypotheses: bool | Literal["unclear"]
    target: Literal["theorem", "proof_step", "hypothesis", "analogy"]
    result: ExampleStatus
    refutes_claim: bool
    refutes_step_only: bool
    exposes_missing_hypothesis: bool
    missing_hypothesis: str | None = None
    support: str | None = None
    explanation: str
    repaired_claim_suggestion: str | None = None

    @model_validator(mode="after")
    def validate_refutation_scope(self) -> CounterexampleTest:
        # A counterexample can refute the claim only with a checked 'failed'
        # result: refutes_claim is honest only once the example was actually
        # tested against the claim and the claim FAILED on it. not_tested /
        # inconclusive / survived can never carry a refutation (mirrors the
        # dropped-hypothesis / proof-step guards below; the kernel must enforce
        # this, not rely on agent good behavior).
        if self.refutes_claim and self.result != ExampleStatus.failed:
            raise ValueError(
                "a counterexample can refute the claim only with a checked 'failed' "
                "result; an untested / survived / inconclusive result cannot refute"
            )
        if self.refutes_claim and (self.exposes_missing_hypothesis or self.missing_hypothesis):
            raise ValueError(
                "dropped-hypothesis / missing-hypothesis pressure cannot refute "
                "the original claim"
            )
        if self.satisfies_all_claim_hypotheses is False and self.refutes_claim:
            raise ValueError("dropped-hypothesis tests cannot refute the original claim")
        if self.satisfies_all_claim_hypotheses is not True and self.refutes_claim:
            raise ValueError(
                "a counterexample can refute the claim only when every claim hypothesis "
                "is explicitly satisfied; unclear admissibility cannot refute"
            )
        if self.refutes_step_only and self.refutes_claim:
            raise ValueError("a proof-step attack cannot refute the original claim")
        if self.refutes_claim and self.target != "theorem":
            raise ValueError("claim refutation requires target='theorem'")
        return self

class CounterexampleReport(StrictBaseModel):
    claim_id: str
    tests: list[CounterexampleTest]

class TestReportItem(StrictBaseModel):
    id: str
    description: str
    uses_examples: list[str] = Field(default_factory=list)
    uses_counterexamples: list[str] = Field(default_factory=list)
    result: ExampleStatus
    evidence_status: ComputationStatus
    main_observation: str
    next_action: str

class TestReport(StrictBaseModel):
    claim_id: str
    target: Literal["claim", "user_sketch", "proof_step", "strategy"]
    tests: list[TestReportItem]
    summary: str


__all__ = [
    "ComputationChecks",
    "ComputationVerificationReport",
    "ComputationVerificationResult",
    "CounterexampleReport",
    "CounterexampleTest",
    "ExampleBank",
    "ExampleComputationCheck",
    "ExampleHypotheses",
    "ExampleRecord",
    "ExampleSelection",
    "FailureCategory",
    "FailureClassification",
    "FailureClassificationReport",
    "FailurePattern",
    "FailurePatternAction",
    "FailurePatternAudit",
    "FailurePatternBank",
    "FailurePatternMatch",
    "FailurePatternTier",
    "InvariantValue",
    "JudgmentAction",
    "JudgmentRiskKind",
    "JudgmentRiskTier",
    "MachineCheckSpec",
    "MathematicalJudgmentFinding",
    "MathematicalJudgmentReport",
    "PolynomialWitness",
    "TestReport",
    "TestReportItem",
    "TestedExample",
    "VerificationVerdict",
]
