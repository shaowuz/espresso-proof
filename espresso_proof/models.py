from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    # Goal 41: sha256 of the EXTRACTED theorem statement this citation quotes.
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
    ]
    expression: str
    expected: str
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
    earned_source_backed: bool = False
    notes: list[str] = Field(default_factory=list)


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
        from .proof_language import contains_proof_language

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


class LiteratureReviewReport(StrictBaseModel):
    claim_id: str
    query_summary: str
    candidate_references: list[LiteratureItem] = Field(default_factory=list)
    known_examples: list[str] = Field(default_factory=list)
    known_counterexample_families: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


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


class TheoremExtractionReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    generated_at: str
    sources: list[SourceRef]
    theorems: list[ExtractedTheorem]
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_theorem_source_refs(self) -> TheoremExtractionReport:
        source_ids = {source.id for source in self.sources}
        unknown = {
            theorem.source_ref_id
            for theorem in self.theorems
            if theorem.source_ref_id not in source_ids
        }
        if unknown:
            raise ValueError(f"theorems reference unknown sources: {sorted(unknown)}")
        return self


class LiteratureSourceAccess(StrictBaseModel):
    status: Literal["full_text_read", "abstract_only", "metadata_only", "unreadable", "unavailable"]
    access_path: str | None = None
    pages_or_sections_read: list[str] = Field(default_factory=list)
    extraction_method: Literal["pdf_text", "tex_source", "html", "plain_text", "metadata", "human_supplied", "unknown"]


class ReviewedSource(StrictBaseModel):
    source_ref: SourceRef
    access: LiteratureSourceAccess
    bibliographic_confidence: Literal["high", "medium", "low"]

    @model_validator(mode="after")
    def validate_review_source_status(self) -> ReviewedSource:
        if self.source_ref.status in {SourceStatus.source_backed, SourceStatus.misapplied}:
            raise ValueError("literature review agent may not emit source_backed or misapplied sources")
        return self


class TheoremStatementSpan(StrictBaseModel):
    page: str | None = None
    section: str | None = None
    char_start: int | None = None
    char_end: int | None = None


class DefinitionRef(StrictBaseModel):
    source_ref_id: str
    locator: Locator
    definition_text: str


class ReviewedTheoremHypothesis(StrictBaseModel):
    id: str
    text: str
    normalized: str
    definition_refs: list[DefinitionRef] = Field(default_factory=list)
    status: Literal["explicit", "inferred", "implicit", "unclear"]


class ReviewedTheorem(StrictBaseModel):
    id: str
    source_ref_id: str
    kind: Literal["theorem", "proposition", "lemma", "corollary", "definition", "remark"]
    label: str
    locator: Locator
    statement_exact: str
    statement_is_verbatim: bool
    statement_span: TheoremStatementSpan = Field(default_factory=TheoremStatementSpan)
    theorem_hypotheses: list[ReviewedTheoremHypothesis] = Field(default_factory=list)
    theorem_conclusion: str
    extraction_confidence: Literal["high", "medium", "low"]


class TheoremHypothesisComparison(StrictBaseModel):
    claim_hypothesis: str
    theorem_hypothesis_id: str | None = None
    relation: Literal[
        "same",
        "stronger",
        "weaker",
        "missing_in_theorem",
        "extra_in_theorem",
        "incompatible",
        "unclear",
    ]
    reason: str
    support_locator: Locator | None = None


class ConclusionMatch(StrictBaseModel):
    relation: Literal["same", "stronger", "weaker", "orthogonal", "incompatible", "unclear"]
    reason: str


class TheoremRelevance(StrictBaseModel):
    role: Literal[
        "key_theorem",
        "supporting_lemma",
        "definition_source",
        "example_source",
        "counterexample_source",
        "background",
    ]
    summary: str


class ComparisonUncertainty(StrictBaseModel):
    confidence: Literal["high", "medium", "low"]
    failure_modes: list[
        Literal[
            "missing_exact_locator",
            "unread_source",
            "ambiguous_statement",
            "missing_definition",
            "hypothesis_mismatch",
            "translation_unclear",
            "weak_term_match",
            "no_theorem_found",
        ]
    ] = Field(default_factory=list)
    explanation: str

    @model_validator(mode="after")
    def validate_uncertainty_detail(self) -> ComparisonUncertainty:
        if self.confidence != "high" and not self.failure_modes:
            raise ValueError("medium/low comparison uncertainty must name at least one failure mode")
        return self


class TheoremClaimComparison(StrictBaseModel):
    theorem_id: str
    claim_id: str
    comparison_status: Literal[
        "applicable",
        "partially_applicable",
        "stronger_than_needed",
        "weaker_than_needed",
        "contradicts",
        "unclear",
    ]
    hypothesis_matches: list[TheoremHypothesisComparison] = Field(default_factory=list)
    conclusion_match: ConclusionMatch
    relevance: TheoremRelevance
    uncertainty: ComparisonUncertainty

    @model_validator(mode="after")
    def validate_applicable_comparison(self) -> TheoremClaimComparison:
        if self.comparison_status == "applicable":
            bad_relations = {"missing_in_theorem", "incompatible", "unclear"}
            if any(match.relation in bad_relations for match in self.hypothesis_matches):
                raise ValueError("applicable comparison requires non-unclear matching hypotheses")
            if self.conclusion_match.relation in {"orthogonal", "incompatible", "unclear"}:
                raise ValueError("applicable comparison requires a compatible conclusion")
        return self


class StrongestLiteratureSource(StrictBaseModel):
    source_ref_id: str
    theorem_id: str | None = None
    reason: str


class LiteratureRelevanceSummary(StrictBaseModel):
    overall_status: Literal[
        "supports_route",
        "contradicts_route",
        "background_only",
        "no_relevant_source",
        "unclear",
    ]
    strongest_sources: list[StrongestLiteratureSource] = Field(default_factory=list)
    main_blockers: list[str] = Field(default_factory=list)


class LiteratureUncertaintySummary(StrictBaseModel):
    overall_confidence: Literal["high", "medium", "low"]
    blocking_uncertainties: list[str] = Field(default_factory=list)
    needs_human_review: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_uncertainty_summary(self) -> LiteratureUncertaintySummary:
        if self.overall_confidence != "high" and not (
            self.blocking_uncertainties or self.needs_human_review
        ):
            raise ValueError("medium/low literature uncertainty must include concrete blockers")
        return self


class LiteratureReviewAgentReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    generated_at: str
    input_sources: list[ReviewedSource]
    extracted_theorems: list[ReviewedTheorem] = Field(default_factory=list)
    theorem_comparisons: list[TheoremClaimComparison] = Field(default_factory=list)
    relevance_summary: LiteratureRelevanceSummary
    uncertainty: LiteratureUncertaintySummary
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_literature_review_agent_refs(self) -> LiteratureReviewAgentReport:
        source_by_id = {source.source_ref.id: source for source in self.input_sources}
        unknown_theorem_sources = {
            theorem.source_ref_id
            for theorem in self.extracted_theorems
            if theorem.source_ref_id not in source_by_id
        }
        if unknown_theorem_sources:
            raise ValueError(f"reviewed theorems reference unknown sources: {sorted(unknown_theorem_sources)}")
        theorem_by_id = {theorem.id: theorem for theorem in self.extracted_theorems}
        unknown_comparisons = {
            comparison.theorem_id
            for comparison in self.theorem_comparisons
            if comparison.theorem_id not in theorem_by_id
        }
        if unknown_comparisons:
            raise ValueError(f"comparisons reference unknown theorems: {sorted(unknown_comparisons)}")
        for theorem in self.extracted_theorems:
            source = source_by_id[theorem.source_ref_id]
            if theorem.statement_is_verbatim and (
                source.access.status != "full_text_read" or not theorem.locator.is_exact
            ):
                raise ValueError("verbatim theorem statements require full text access and an exact locator")
        return self


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
        if self.satisfies_all_claim_hypotheses is False and self.refutes_claim:
            raise ValueError("dropped-hypothesis tests cannot refute the original claim")
        if self.satisfies_all_claim_hypotheses is not True and self.refutes_claim:
            raise ValueError(
                "a counterexample can refute the claim only when every claim hypothesis "
                "is explicitly satisfied; unclear admissibility cannot refute"
            )
        if self.refutes_step_only and self.refutes_claim:
            raise ValueError("a proof-step attack cannot refute the original claim")
        return self


class CounterexampleReport(StrictBaseModel):
    claim_id: str
    tests: list[CounterexampleTest]


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
    applicability_status: Literal["applicable", "blocked", "misapplied", "unclear"]
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
            if has_bad_condition:
                raise ValueError("applicable theorem usage requires all conditions satisfied")
            if self.conclusion_status in {"orthogonal", "contradicts", "unclear"}:
                raise ValueError("applicable theorem usage requires compatible conclusion")
            if self.blocks_source_backed:
                raise ValueError("applicable theorem usage cannot block source_backed")
        if has_bad_condition and not self.blocks_source_backed:
            raise ValueError("unsatisfied theorem conditions must block source_backed")
        return self


class TheoremApplicabilityReport(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    source_grounding_artifact: str
    literature_review_agent_artifact: str | None = None
    usages: list[TheoremApplicabilityUsage]
    # Goal 57A slice-2: default-deny usages derived from the advisory theorem
    # condition ledger (reviewer source_requests). These are NOT source-grounding
    # usages: they never map to a proof step, never earn source_backed, and (being
    # default-deny) are NEVER ``applicable`` unless a condition is explicitly
    # ``matched`` AND the locator is exact. Their only safety effect is to forbid
    # ``all_applicable`` while any ledger obligation is unresolved. Additive and
    # optional — absent/empty means behaviour is unchanged.
    ledger_usages: list[TheoremApplicabilityUsage] = Field(default_factory=list)
    overall_status: Literal["all_applicable", "blocked", "misapplied", "unclear"]
    notes: list[str] = Field(default_factory=list)
    # Goal 41: sha256 of source_grounding.yaml's bytes AT AUDIT TIME. Optional/
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
            # Goal 57A: a ledger obligation that is not fully resolved (non-exact
            # locator or any non-satisfied condition surfaces as a non-applicable
            # ledger usage) must forbid all_applicable. Default-deny, fail closed.
            if any(usage.applicability_status != "applicable" for usage in self.ledger_usages):
                raise ValueError(
                    "all_applicable requires every theorem condition ledger usage applicable"
                )
        if any(usage.applicability_status == "misapplied" for usage in self.usages):
            if self.overall_status != "misapplied":
                raise ValueError("misapplied theorem usage requires overall_status=misapplied")
        if any(usage.applicability_status == "blocked" for usage in self.usages):
            if self.overall_status not in {"blocked", "misapplied"}:
                raise ValueError("blocked theorem usage requires blocked or misapplied overall status")
        return self


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


class ReviewerReport(StrictBaseModel):
    reviewer_id: Literal["strategy_reviewer", "evidence_reviewer", "line_proof_reviewer"]
    scope: Literal["proof_strategy", "examples_counterexamples", "line_by_line_proof"]
    verdict: ReviewerVerdict
    applicability: ReviewerApplicability = ReviewerApplicability.applicable
    summary: str
    objections: list[str] = Field(default_factory=list)
    checked_artifacts: list[str] = Field(default_factory=list)
    required_reruns: list[str] = Field(default_factory=list)
    severity: Literal["low", "medium", "high"]

    @model_validator(mode="after")
    def validate_applicability(self) -> ReviewerReport:
        if self.reviewer_id != "line_proof_reviewer" and self.applicability != ReviewerApplicability.applicable:
            raise ValueError("only line_proof_reviewer may be sketch_only or not_applicable")
        if self.applicability != ReviewerApplicability.applicable and self.verdict == ReviewerVerdict.failed:
            raise ValueError("sketch_only/not_applicable cannot have fail verdict")
        return self


class Review(StrictBaseModel):
    claim_id: str
    attempt_status: AttemptStatus
    verdict: Literal["continue", "repair_first", "likely_broken", "refuted", "unclear"]
    reviewers: list[ReviewerReport]
    strongest_objection: str
    theorem_drift: list[str] = Field(default_factory=list)
    missing_hypotheses: list[str] = Field(default_factory=list)
    source_risks: list[str] = Field(default_factory=list)
    example_risks: list[str] = Field(default_factory=list)
    downgrade_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_parallel_reviewers(self) -> Review:
        required = {"strategy_reviewer", "evidence_reviewer", "line_proof_reviewer"}
        seen = {reviewer.reviewer_id for reviewer in self.reviewers}
        if seen != required:
            raise ValueError(f"review requires exactly these reviewers: {sorted(required)}")

        any_fail = any(reviewer.verdict == ReviewerVerdict.failed for reviewer in self.reviewers)
        any_unclear = any(reviewer.verdict == ReviewerVerdict.unclear for reviewer in self.reviewers)
        if any_fail and self.attempt_status != AttemptStatus.failed:
            raise ValueError("any failed reviewer forces attempt_status=failed")
        if not any_fail and any_unclear and self.attempt_status != AttemptStatus.unclear:
            raise ValueError("unclear reviewers without failures force attempt_status=unclear")
        if not any_fail and not any_unclear and self.attempt_status != AttemptStatus.passed:
            raise ValueError("all reviewers passed but attempt_status is not passed")
        return self


class IdeaGrade(StrictBaseModel):
    claim_id: str
    status: IdeaStatus
    score: int = Field(ge=0, le=100)
    confidence: Literal["low", "medium", "high"]
    reasons: list[str]
    survived_tests: list[str] = Field(default_factory=list)
    failed_tests: list[str] = Field(default_factory=list)
    counterexample_candidates: list[str] = Field(default_factory=list)
    missing_hypotheses: list[str] = Field(default_factory=list)
    source_gaps: list[str] = Field(default_factory=list)
    repair_options: list[str] = Field(default_factory=list)
    next_cheapest_test: str
    human_review_required: bool
    proof_language_status: ProofLanguageStatus


class StrategyBriefItem(StrictBaseModel):
    id: str
    title: str
    score: int = Field(ge=0, le=100)
    status: IdeaStatus
    why_this_route: str
    survived_tests: list[str] = Field(default_factory=list)
    failed_tests: list[str] = Field(default_factory=list)
    main_risk: str
    source_status: Literal["source_backed", "candidate", "unverified", "mixed", "not_found"]
    estimated_effort: ExplorationMode
    next_loop_plan: str


class StrategySynthesis(StrictBaseModel):
    claim_id: str
    synthesized_from: list[str]
    routes: list[StrategyBriefItem]
    discarded_routes: list[str] = Field(default_factory=list)
    synthesis_note: str


class AlsoConsideredStrategy(StrictBaseModel):
    title: str
    reason_not_top_three: str


class HumanChoiceOption(StrictBaseModel):
    strategy_id: str
    effort_options: list[ExplorationMode]


class StrategyBrief(StrictBaseModel):
    claim_id: str
    generated_after_runs: list[str]
    top_strategies: list[StrategyBriefItem]
    also_considered: list[AlsoConsideredStrategy] = Field(default_factory=list)
    question_for_mathematician: str
    allowed_human_choices: list[HumanChoiceOption]

    @model_validator(mode="after")
    def validate_choices_and_scores(self) -> StrategyBrief:
        strategy_ids = {strategy.id for strategy in self.top_strategies}
        choice_ids = {choice.strategy_id for choice in self.allowed_human_choices}
        missing_choices = strategy_ids.difference(choice_ids)
        if missing_choices:
            raise ValueError(
                f"missing human choice options for strategies: {sorted(missing_choices)}"
            )
        if "none" not in choice_ids:
            raise ValueError("strategy brief must include a none human choice option")
        for choice in self.allowed_human_choices:
            if not choice.effort_options:
                raise ValueError(f"human choice {choice.strategy_id} requires effort options")
            if choice.strategy_id not in strategy_ids and choice.strategy_id != "none":
                raise ValueError(f"unknown strategy_id in human choice: {choice.strategy_id}")
        for strategy in self.top_strategies:
            if strategy.score >= 75 and strategy.source_status in {"unverified", "not_found"}:
                raise ValueError("high-scoring strategies require at least candidate or mixed source status")
        return self


class HumanChoice(StrictBaseModel):
    claim_id: str
    status: HumanChoiceStatus
    selected_strategy: str | None = None
    effort: ExplorationMode | None = None
    taste_judgment: str = ""
    continue_decision: ContinueDecision
    # human_confirmed records ONLY that a human made THIS strategy selection (it is
    # required true whenever status == selected, see validate_choice_state). It is
    # NOT a proof/claim confirmation and NOT the LLM-agent human gate: a valid run
    # can carry human_confirmed=true here while llm_agent_run.human_gate.status is
    # still 'pending' (the human picked a route; the prover-pass output still awaits
    # review). Do not read it as "the human confirmed the result".
    human_confirmed: bool = False

    @model_validator(mode="after")
    def validate_choice_state(self) -> HumanChoice:
        if self.status == HumanChoiceStatus.selected:
            if not self.selected_strategy:
                raise ValueError("selected human choice requires selected_strategy")
            if self.effort is None:
                raise ValueError("selected human choice requires effort")
            if self.continue_decision == ContinueDecision.pending:
                raise ValueError("selected human choice requires a non-pending continue_decision")
            if not self.human_confirmed:
                raise ValueError("selected human choice requires human_confirmed=true")
        if self.status == HumanChoiceStatus.pending:
            if self.selected_strategy is not None or self.effort is not None:
                raise ValueError("pending human choice cannot have selected_strategy or effort")
            if self.continue_decision != ContinueDecision.pending:
                raise ValueError("pending human choice requires continue_decision=pending")
        return self


class HumanFeedbackItem(StrictBaseModel):
    feedback_id: str
    target_type: HumanFeedbackTargetType
    target_artifact: str
    target_id: str
    labels: list[HumanFeedbackLabel]
    reason: str
    scope: HumanFeedbackScope
    requested_actions: list[HumanFeedbackAction] = Field(default_factory=list)
    human_confirmed: bool
    supersedes_feedback_id: str | None = None

    @model_validator(mode="after")
    def validate_feedback_item(self) -> HumanFeedbackItem:
        if len(self.labels) != len(set(self.labels)):
            raise ValueError("feedback labels must be unique")
        if not self.labels:
            raise ValueError("feedback requires at least one label")
        if not self.reason.strip():
            raise ValueError("feedback reason cannot be empty")
        return self


class HumanExampleFeedback(StrictBaseModel):
    schema_version: Literal[1] = 1
    project_id: str
    claim_id: str
    run_id: str
    created_at: str
    feedback: list[HumanFeedbackItem]

    @model_validator(mode="after")
    def validate_feedback_ids(self) -> HumanExampleFeedback:
        ids = [item.feedback_id for item in self.feedback]
        if len(ids) != len(set(ids)):
            raise ValueError("feedback ids must be unique")
        return self


class FeedbackRankingAdjustment(StrictBaseModel):
    target_id: str
    target_type: HumanFeedbackTargetType
    target_artifact: str
    feedback_ids: list[str]
    direction: RankingDirection
    weight: RankingWeight
    reason: str

    @model_validator(mode="after")
    def validate_ranking_adjustment(self) -> FeedbackRankingAdjustment:
        if not self.feedback_ids:
            raise ValueError("ranking adjustment requires feedback_ids")
        if len(self.feedback_ids) != len(set(self.feedback_ids)):
            raise ValueError("ranking adjustment feedback_ids must be unique")
        if not self.reason.strip():
            raise ValueError("ranking adjustment reason cannot be empty")
        return self


class FeedbackMemoryEntryPointer(StrictBaseModel):
    memory_entry_id: str
    entry_path: str


class ExampleFeedbackSummary(StrictBaseModel):
    schema_version: Literal[1] = 1
    claim_id: str
    run_id: str
    generated_at: str
    generated_by: Literal["summarize-feedback", "mock_pipeline"]
    feedback_artifact_path: str
    summarized_from: list[str]
    input_artifact_digests: dict[str, str] = Field(default_factory=dict)
    ranking_adjustments: list[FeedbackRankingAdjustment] = Field(default_factory=list)
    project_preferences: list[str] = Field(default_factory=list)
    playbook_tuning_candidates: list[str] = Field(default_factory=list)
    memory_entries: list[FeedbackMemoryEntryPointer] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_summary_references(self) -> ExampleFeedbackSummary:
        if not self.summarized_from:
            raise ValueError("feedback summary requires summarized_from")
        if len(self.summarized_from) != len(set(self.summarized_from)):
            raise ValueError("summarized_from ids must be unique")
        return self


class AgentContract(StrictBaseModel):
    agent_name: str
    input_artifacts: list[str]
    output_artifact: str
    output_schema: str
    failure_modes: list[str]
    must_not: list[str]
    human_gate: Literal["none", "major_decision", "required"]


class AgentContracts(StrictBaseModel):
    project_id: str
    contracts: list[AgentContract]

    @model_validator(mode="after")
    def validate_unique_contracts(self) -> AgentContracts:
        names = [contract.agent_name for contract in self.contracts]
        if len(names) != len(set(names)):
            raise ValueError("agent contract names must be unique")
        return self


class AdmissionCheck(StrictBaseModel):
    project_id: str
    workflow_spec_valid: bool
    agent_contracts_exist: bool
    required_artifacts_declared: bool
    trial_fixture_exists: bool
    human_confirmed: bool
    admitted: bool
    blockers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_admission(self) -> AdmissionCheck:
        required = [
            self.workflow_spec_valid,
            self.agent_contracts_exist,
            self.required_artifacts_declared,
            self.trial_fixture_exists,
            self.human_confirmed,
        ]
        if self.admitted and not all(required):
            raise ValueError("admitted workflow requires all admission checks to pass")
        if self.admitted and self.blockers:
            raise ValueError("admitted workflow cannot have blockers")
        return self


class LoopControl(StrictBaseModel):
    claim_id: str
    cycles_analyzed: int = Field(ge=1)
    no_new_counterexamples_cycles: int = Field(ge=0)
    no_new_references_cycles: int = Field(ge=0)
    grade_delta_last_two: int
    budget_exhausted: bool = False
    should_stop: bool
    stop_reasons: list[str] = Field(default_factory=list)
    next_action: Literal["continue_loop", "run_strategy_brief", "ask_human", "stop"]

    @model_validator(mode="after")
    def validate_stop_rule(self) -> LoopControl:
        no_progress = (
            self.no_new_counterexamples_cycles >= 2
            and self.no_new_references_cycles >= 2
            and self.grade_delta_last_two <= 0
        )
        if (no_progress or self.budget_exhausted) and not self.should_stop:
            raise ValueError("two no-progress cycles or exhausted budget require should_stop=true")
        if self.should_stop and self.next_action == "continue_loop":
            raise ValueError("should_stop=true cannot continue the loop")
        if self.next_action == "stop" and not self.should_stop:
            raise ValueError("next_action=stop requires should_stop=true")
        if self.should_stop and not self.stop_reasons:
            raise ValueError("stopped loops require stop_reasons")
        return self


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


class ScopedObjection(StrictBaseModel):
    """Goal 54B: a reviewer objection bound to a named proof-search target so it
    becomes repairable state, not just prose.

    Kernel-safety contract (advisory only):
    - an objection can only BLOCK or PRIORITIZE repair; it can never prove,
      refute, close, or upgrade any proof/source/evidence/grade/claim status;
    - ``counterexample_pressure`` is unchecked pressure, never a refutation;
    - ``subgoal_id`` / ``proof_step_id`` are UNVERIFIED provenance, deliberately
      NOT hard-reconciled against the live proof graph in ``validate_run`` (Goal-39
      precedent: blocker-derived node ids renumber across rebuilds). Reconciliation
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
    # Goal 54B: additive structured objections bound to proof-search targets.
    # Legacy free-text fields above are preserved; the normalizer also mirrors them
    # into route/global scoped_objections (scope_source='legacy_fallback') so older
    # producers stay valid and downstream has one structured surface.
    scoped_objections: list[ScopedObjection] = Field(default_factory=list)
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
    created_at: str
    resolved_at: str | None = None

    @model_validator(mode="after")
    def validate_resolution(self) -> LLMAgentHumanGate:
        if self.status == "resolved":
            if self.decision is None:
                raise ValueError("resolved human gate requires a decision")
            if not self.human_confirmed:
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
    # Goal 39: a reviewer pointer DECLARES its scope; "route" is the honest
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
    next_action: str
    # Goal 39: which named subgoal this round targeted (the scheduler's pick at
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
      unavailable or was refused (Goal 57B). A non-evidence state.
    - ``conditional``: progress is contingent on an unresolved gap or a research
      blocker (Goal 57B). A non-evidence state.
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
    """Why a subgoal is blocked/conditional (advisory typing only, Goal 57B).

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
    # Goal 57B: advisory blocked/conditional typing. Optional + default so every
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


class MemoryEntry(StrictBaseModel):
    id: str
    timestamp: str
    project: str
    type: Literal["failed_attempt", "tested_example", "counterexample", "source_gap", "repaired_claim", "user_decision", "theorem_applicability_failure", "reviewer_objection", "human_example_feedback"]
    claim_id: str
    summary: str
    provenance: list[str]
    reusable_lesson: str


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


class MemoryContext(StrictBaseModel):
    """Advisory memory influence for a run: prior-run lessons matched to this
    claim, with recommended uses. Advisory only — never upgrades any status and
    never overrides current-run artifacts (StrictBaseModel forbids status fields)."""

    schema_version: int = 1
    claim_id: str
    generated_at: str = ""
    matched_entries: list[MemoryContextEntry] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class MemoryIndexEntry(StrictBaseModel):
    id: str
    entry_path: str
    claim_id: str
    type: Literal["failed_attempt", "tested_example", "counterexample", "source_gap", "repaired_claim", "user_decision", "theorem_applicability_failure", "reviewer_objection", "human_example_feedback"]
    tags: list[str] = Field(default_factory=list)
    summary: str
    provenance: list[str]


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


class RunManifest(StrictBaseModel):
    run_id: str
    project_id: str
    created_at: str
    current_state: str
    input_file: str
    artifacts: dict[str, str]
    optional_artifacts: dict[str, str] = Field(default_factory=dict)
    dependencies: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    retries: dict[str, dict[str, Any]] = Field(default_factory=dict)

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


class AgentRunStatusEntry(StrictBaseModel):
    """Process status for ONE evidence / understanding agent (Goal 27).

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
    """Per-agent run-status board for a run (``agent_run_status.yaml``, Goal 27).

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


class RouteAllocationEntry(StrictBaseModel):
    """Goal 54C: one candidate route's advisory scheduling line.

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
    """Goal 54C: deterministic, advisory-only time-allocation context derived from
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
# Goal 57A: Theorem Condition Ledger (slice 1 — model only).
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


MODEL_REGISTRY: dict[str, type[BaseModel]] = {
    "claim": Claim,
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
    "review": Review,
    "idea_grade": IdeaGrade,
    "loop_control": LoopControl,
    "strategy_synthesis": StrategySynthesis,
    "strategy_brief": StrategyBrief,
    "human_choice": HumanChoice,
    "human_example_feedback": HumanExampleFeedback,
    "example_feedback_summary": ExampleFeedbackSummary,
    "llm_agent_run": LLMAgentRunReport,
    "prover_loop": ProverLoopReport,
    "prover_round": ProverRoundReport,
    "prover_round_index": ProverRoundIndex,
    "proof_goal_graph": ProofGoalGraph,
    "theorem_condition_ledger": TheoremConditionLedger,
    "human_time_allocation_context": HumanTimeAllocationContext,
    "front_half_run": FrontHalfRunReport,
    "agent_run_status": AgentRunStatusReport,
    "memory_entry": MemoryEntry,
    "memory_index": MemoryIndex,
    "memory_context": MemoryContext,
    "run_manifest": RunManifest,
    "workflow_spec": WorkflowSpec,
    "source_ref": SourceRef,
    "extracted_theorem": ExtractedTheorem,
    "example_computation_check": ExampleComputationCheck,
}
