"""Goal 42 loop-harness artifact models.

Strict (extra=forbid) models for every cycle-level and campaign-level file the
autonomous dogfood improvement loop writes. These are META-HARNESS records
about how Espresso behaved — they are NOT run artifacts, carry no
proof/source/evidence/grade/refutation/claim status fields, and are never read
by the kernel validators. Nothing in a loop record can upgrade any
mathematical status (Hard Rules, Goal 42).

The models are deliberately observational: free-text fields describe what
happened and what to try next; the only enums are the closed vocabularies the
goal file fixes (event kinds, bottleneck categories, reviewer verdicts, gap
statuses, stop-gate statuses).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LoopBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# cases.yaml
# --------------------------------------------------------------------------

CaseFamily = Literal[
    "true_theorem",
    "false_theorem",
    "missing_hypothesis",
    "source_sensitive",
    "computation_scoped",
    "hard_research_sketch",
]


class DogfoodCase(LoopBaseModel):
    case_id: str
    claim: str
    case_family: CaseFamily
    expected_difficulty: Literal["smoke", "normal", "hard"]
    # Evaluator-only ground truth. NEVER injected into Espresso agent prompts
    # or run inputs (negative-tested); never proof evidence.
    known_truth_status: Literal[
        "evaluator_only_true", "evaluator_only_false", "evaluator_only_unknown"
    ]
    what_this_case_tests: list[str] = Field(default_factory=list)
    must_not_claim: list[str] = Field(default_factory=list)


class CycleCases(LoopBaseModel):
    cycle_id: str
    cases: list[DogfoodCase]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> CycleCases:
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id values must be unique within a cycle")
        return self


# --------------------------------------------------------------------------
# run_manifest.yaml (loop level — how each Espresso run was launched)
# --------------------------------------------------------------------------


class LoopRunRecord(LoopBaseModel):
    case_id: str
    command: str
    run_dir: str
    provider: str
    mode: str
    selected_route: str | None = None
    started_at: str
    finished_at: str
    validation_result: Literal["valid", "invalid", "not_run"]
    validation_error: str = ""
    final_visible_state: str = ""


class LoopRunManifest(LoopBaseModel):
    cycle_id: str
    runs: list[LoopRunRecord]


# --------------------------------------------------------------------------
# agent_events.jsonl entries
# --------------------------------------------------------------------------

AgentEventKind = Literal["queued", "started", "completed", "failed", "skipped", "timed_out"]


class AgentEvent(LoopBaseModel):
    timestamp: str
    cycle_id: str
    case_id: str
    run_id: str
    agent_name: str
    stage: str
    event: AgentEventKind
    duration_ms: int | None = None
    output_artifact: str | None = None
    error_summary: str | None = None


# --------------------------------------------------------------------------
# timings.yaml
# --------------------------------------------------------------------------


class CaseTimings(LoopBaseModel):
    case_id: str
    time_to_first_feedback_ms: int | None = None
    time_to_first_route_ms: int | None = None
    total_case_duration_s: float | None = None
    agent_timings_ms: dict[str, int] = Field(default_factory=dict)
    slowest_agent: str | None = None
    first_user_wait_bottleneck: str | None = None


class CycleTimings(LoopBaseModel):
    cycle_id: str
    total_cycle_duration_s: float
    cases: list[CaseTimings]


# --------------------------------------------------------------------------
# bottleneck_report.yaml
# --------------------------------------------------------------------------

BottleneckCategory = Literal[
    "latency",
    "artifact_invalidity",
    "proof_search_stall",
    "source_mismatch",
    "theorem_applicability_gap",
    "refutation_gate_confusion",
    "reviewer_miss",
    "ui_wait",
    "harness_gap",
    "external_tool_gap",
    "unknown",
]


class BottleneckEntry(LoopBaseModel):
    bottleneck_id: str
    case_id: str
    run_id: str
    stage_or_agent: str
    category: BottleneckCategory
    metric_or_evidence_path: str
    user_visible_impact: str
    proposed_next_diagnostic: str


class BottleneckReport(LoopBaseModel):
    cycle_id: str
    bottlenecks: list[BottleneckEntry]


# --------------------------------------------------------------------------
# failures.yaml (technical failures only)
# --------------------------------------------------------------------------


class TechnicalFailure(LoopBaseModel):
    failure_id: str
    case_id: str
    run_id: str
    stage: str
    kind: Literal[
        "timeout",
        "bad_json",
        "invalid_artifact",
        "validate_run_failure",
        "provider_failure",
        "ui_endpoint_failure",
        "gate_failure",
        "harness_error",
    ]
    error_summary: str
    evidence_path: str = ""


class FailureReport(LoopBaseModel):
    cycle_id: str
    failures: list[TechnicalFailure]


# --------------------------------------------------------------------------
# refutations.yaml
# --------------------------------------------------------------------------

RefutationFailureClass = Literal[
    "claim_refuted",
    "strategy_failed_by_example",
    "theorem_condition_gap",
    "source_gap",
    "definition_gap",
    "computation_unchecked",
    "no_progress",
]


class RefutationRecord(LoopBaseModel):
    case_id: str
    run_id: str
    claimed_by: str
    candidate_counterexample: str
    satisfies_all_locked_hypotheses: bool | None = None
    conclusion_fails: bool | None = None
    counterexample_checked: bool = False
    espresso_allowed_refuted: bool = False
    locked_claim_id: str = ""
    hypothesis_ledger_path: str = ""
    affected_route_id: str | None = None
    affected_subgoal_id: str | None = None
    failure_class: RefutationFailureClass
    attacked_step_or_tactic: str = ""
    failed_lemma: str = ""
    check_method: str = ""
    recurrence_fingerprint: str = ""
    repair_suggestion: str = ""
    next_action: str = ""
    success_criterion: str = ""
    # Must make proof-step attacks visibly distinct from claim refutations.
    is_proof_step_attack_only: bool = False


class RefutationReport(LoopBaseModel):
    cycle_id: str
    refutations: list[RefutationRecord]


# --------------------------------------------------------------------------
# blockers.yaml
# --------------------------------------------------------------------------


class BlockerRecord(LoopBaseModel):
    case_id: str
    run_id: str
    subgoal_id: str | None = None
    local_claim: str = ""
    blocker_text: str
    blocking_evidence_type: Literal[
        "source_gap",
        "missing_definition",
        "failed_proof_step",
        "missing_computation",
        "reviewer_objection",
        "human_judgment_needed",
    ]
    recurrence: Literal["new", "recurring", "resolved"]
    locked_claim_id: str = ""
    hypothesis_ledger_path: str = ""
    affected_route_id: str | None = None
    attempted_tactic: str = ""
    missing_item: str = ""
    repair_suggestion: str = ""
    next_action: str = ""
    success_criterion: str = ""
    recurrence_fingerprint: str = ""


class BlockerReport(LoopBaseModel):
    cycle_id: str
    blockers: list[BlockerRecord]


# --------------------------------------------------------------------------
# strategy_trace.yaml / subgoal_progress.yaml / theorem_use_ledger.yaml
# --------------------------------------------------------------------------


class StrategyRouteTrace(LoopBaseModel):
    route_id: str
    core_idea: str
    score: int | None = None
    selected: bool = False
    attempted_tactics: list[str] = Field(default_factory=list)
    outcome: str = ""


class StrategyTrace(LoopBaseModel):
    cycle_id: str
    case_id: str
    run_id: str
    routes: list[StrategyRouteTrace]
    selected_route_id: str | None = None
    ranking_evidence: list[str] = Field(default_factory=list)


class StrategyTraceReport(LoopBaseModel):
    cycle_id: str
    traces: list[StrategyTrace]


class SubgoalSnapshot(LoopBaseModel):
    subgoal_id: str
    local_claim: str
    state: str
    parent: str | None = None
    blockers: list[str] = Field(default_factory=list)
    scoped_checks: list[str] = Field(default_factory=list)


class SubgoalProgress(LoopBaseModel):
    case_id: str
    run_id: str
    graph_present: bool
    subgoals: list[SubgoalSnapshot] = Field(default_factory=list)
    next_target_subgoal_id: str | None = None


class SubgoalProgressReport(LoopBaseModel):
    cycle_id: str
    cases: list[SubgoalProgress]


class TheoremUseEntry(LoopBaseModel):
    case_id: str
    run_id: str
    cited_source: str
    exact_locator: str = ""
    statement_excerpt_hash: str = ""
    required_hypotheses: list[str] = Field(default_factory=list)
    matched_hypotheses: list[str] = Field(default_factory=list)
    missing_or_unclear_hypotheses: list[str] = Field(default_factory=list)
    step_status: Literal["candidate", "reference_supported", "needs_check"]


class TheoremUseLedger(LoopBaseModel):
    cycle_id: str
    entries: list[TheoremUseEntry]


# --------------------------------------------------------------------------
# review_reports/*.yaml (42B)
# --------------------------------------------------------------------------

ReviewerName = Literal[
    "math_logic",
    "source_applicability",
    "counterexample",
    "proof_search",
    "kernel_safety",
    "product_ux",
]

ReviewVerdict = Literal[
    "NO_BLOCKING_OBJECTION_AT_CURRENT_SCOPE", "PASS_WITH_NOTES", "BLOCKER"
]

FindingSeverity = Literal["P0", "P1", "P2", "P3"]


class ActionableFinding(LoopBaseModel):
    finding_id: str
    severity: FindingSeverity
    evidence_path: str
    what_happened: str
    why_it_matters: str
    recommended_fix: str
    gap_kind: Literal[
        "kernel_safety",
        "status_overclaim",
        "math_capability",
        "product_workflow",
        "source_tool_capability",
        "coverage_gap",
        "polish",
    ] = "math_capability"

    @model_validator(mode="after")
    def validate_evidence(self) -> ActionableFinding:
        if not self.evidence_path.strip():
            raise ValueError(
                "actionable findings require an evidence path; unevidenced "
                "impressions belong in advisory_findings"
            )
        return self


class AdvisoryFinding(LoopBaseModel):
    note: str


class ForbiddenContextCheck(LoopBaseModel):
    known_truth_status_hidden_from_espresso_prompt: bool


class ReviewReport(LoopBaseModel):
    reviewer: ReviewerName
    verdict: ReviewVerdict
    input_artifacts: list[str]
    forbidden_context_checked: ForbiddenContextCheck
    reviewer_model_or_mode: str
    actionable_findings: list[ActionableFinding] = Field(default_factory=list)
    advisory_findings: list[AdvisoryFinding] = Field(default_factory=list)
    planted_flaw_caught: bool | None = None

    @model_validator(mode="after")
    def validate_blocker_has_finding(self) -> ReviewReport:
        if self.verdict == "BLOCKER" and not self.actionable_findings:
            raise ValueError("a BLOCKER verdict requires at least one actionable finding")
        return self


# --------------------------------------------------------------------------
# triage.yaml / autofix_plan.yaml (42C)
# --------------------------------------------------------------------------


class UnselectedFinding(LoopBaseModel):
    finding_id: str
    severity: FindingSeverity
    why_not_selected: str


class TriageDecision(LoopBaseModel):
    cycle_id: str
    selected_gap_id: str
    selected_gap_kind: Literal[
        "math_capability", "kernel_safety", "product_workflow", "harness_gap"
    ]
    selected_evidence_paths: list[str]
    selection_rationale: str
    unselected_findings: list[UnselectedFinding] = Field(default_factory=list)
    new_goal_proposal_path: str | None = None

    @model_validator(mode="after")
    def validate_selection(self) -> TriageDecision:
        if not self.selected_gap_id.strip():
            raise ValueError("triage must choose exactly one selected_gap_id")
        if not self.selected_evidence_paths:
            raise ValueError("the selected gap requires evidence paths")
        return self


class AutofixPlan(LoopBaseModel):
    cycle_id: str
    selected_gap_id: str
    why_this_gap_now: str
    files_expected_to_change: list[str]
    max_files: int = 8
    max_effective_diff_lines: int = 800
    focused_tests: list[str]
    required_reviewers: list[str]
    replay_cases: list[str]
    abort_if: list[str] = Field(
        default_factory=lambda: [
            "fix requires weakening validators",
            "fix needs broad rewrite",
            "replay cannot be run",
        ]
    )

    @model_validator(mode="after")
    def validate_bounds(self) -> AutofixPlan:
        if len(self.files_expected_to_change) > self.max_files:
            raise ValueError(
                f"autofix plan exceeds the {self.max_files}-file budget; write a "
                "new goal proposal instead of a broad fix"
            )
        if self.max_files > 8 or self.max_effective_diff_lines > 800:
            raise ValueError("autofix budgets cannot be raised above the Goal 42 caps")
        if not self.replay_cases:
            raise ValueError("an autofix plan requires replay cases")
        if "Engineering" not in self.required_reviewers or "Kernel Safety" not in self.required_reviewers:
            raise ValueError("Engineering and Kernel Safety reviewers are mandatory")
        return self


# --------------------------------------------------------------------------
# replay_result.yaml / learning_delta.yaml / next_cycle_policy.yaml /
# capability_delta.yaml (42D)
# --------------------------------------------------------------------------


class ReplayEvidence(LoopBaseModel):
    evidence_path: str
    observed_problem: str = ""
    fixed: bool | None = None
    no_new_p0_or_p1: bool | None = None
    no_status_upgrade_violation: bool | None = None
    validate_run_passed: bool | None = None
    first_feedback_regressed: bool | None = None


class CaseRegressionCheck(LoopBaseModel):
    case_id: str
    no_regression: bool
    evidence_path: str = ""


class ReplayResult(LoopBaseModel):
    cycle_id: str
    selected_gap_id: str
    checkpoint: Literal["baseline", "pre_merge", "post_merge"]
    before: ReplayEvidence
    after: ReplayEvidence
    sibling_cases: list[CaseRegressionCheck] = Field(default_factory=list)
    holdout_cases: list[CaseRegressionCheck] = Field(default_factory=list)
    successful: bool = False

    @model_validator(mode="after")
    def validate_success(self) -> ReplayResult:
        if self.successful:
            if self.after.fixed is not True:
                raise ValueError("a replay without improvement cannot be marked successful")
            if not (self.sibling_cases or self.holdout_cases):
                raise ValueError(
                    "a successful replay requires sibling or holdout no-regression evidence"
                )
            if any(not case.no_regression for case in self.sibling_cases + self.holdout_cases):
                raise ValueError("a replay with a sibling/holdout regression cannot be successful")
        return self


class LearningDelta(LoopBaseModel):
    cycle_id: str
    observed_failure_mode: str
    old_strategy_or_behavior: str
    failure_evidence_path: str
    new_strategy_or_behavior: str
    allowed_scope: str
    forbidden_generalizations: list[str]
    required_rechecks: list[str]
    advisory_only: Literal[True] = True


class NextCyclePolicy(LoopBaseModel):
    cycle_id: str
    prover_tactics_to_prioritize: list[str] = Field(default_factory=list)
    source_checks_to_repeat: list[str] = Field(default_factory=list)
    counterexample_checks_to_strengthen: list[str] = Field(default_factory=list)
    subgoal_types_to_target: list[str] = Field(default_factory=list)
    strategy_patterns_to_avoid: list[str] = Field(default_factory=list)
    holdout_cases_to_replay: list[str] = Field(default_factory=list)
    advisory_only: Literal[True] = True


class CapabilityDelta(LoopBaseModel):
    cycle_id: str
    selected_gap_id: str
    capability: Literal[
        "proof_search_progress",
        "source_grounding",
        "theorem_applicability",
        "counterexample_refutation",
        "scoped_verifier_tool_evidence",
        "product_workflow_latency",
        "reviewer_recall",
        "scorecard_calibration_coverage",
        "harness_quality",
    ]
    before_evidence_path: str
    after_evidence_path: str
    improved: bool
    note: str = ""

    @model_validator(mode="after")
    def validate_evidence(self) -> CapabilityDelta:
        if self.improved and (
            not self.before_evidence_path.strip() or not self.after_evidence_path.strip()
        ):
            raise ValueError(
                "a capability improvement must cite before/after evidence paths; "
                "CI success alone is not capability improvement"
            )
        return self


# --------------------------------------------------------------------------
# Campaign-level files
# --------------------------------------------------------------------------


class CampaignCycleEntry(LoopBaseModel):
    cycle_id: str
    case_ids: list[str]
    run_ids: list[str]
    selected_gap_id: str | None = None
    pr_or_commit: str | None = None
    replay_result: str | None = None
    final_status: Literal[
        "recorded", "reviewed", "triaged", "fixed", "replayed", "clean", "aborted"
    ]


class CampaignIndex(LoopBaseModel):
    campaign_id: str
    cycles: list[CampaignCycleEntry]


GapStatus = Literal["open", "selected", "fixed", "deferred", "converted_to_goal"]


class GapLedgerEntry(LoopBaseModel):
    gap_id: str
    first_seen_cycle: str
    recurrence_count: int = 1
    linked_finding_ids: list[str] = Field(default_factory=list)
    linked_blocker_ids: list[str] = Field(default_factory=list)
    affected_case_families: list[str] = Field(default_factory=list)
    bottleneck_category: BottleneckCategory = "unknown"
    before_metric: str = ""
    after_metric: str = ""
    status: GapStatus = "open"
    selection_note: str = ""


class GapLedger(LoopBaseModel):
    campaign_id: str
    gaps: list[GapLedgerEntry]


class LoopMemoryItem(LoopBaseModel):
    cycle_id: str
    what_failed: str
    where_it_failed: str
    repair_attempted: str
    try_or_avoid_next: str
    scope_limits: str
    forbidden_generalizations: list[str] = Field(default_factory=list)
    status: Literal["memory_suggested"] = "memory_suggested"


class LoopMemory(LoopBaseModel):
    campaign_id: str
    note: str = (
        "Advisory memory only. Every item is memory_suggested until independently "
        "rechecked in a future run. Memory cannot upgrade proof, source, evidence, "
        "grade, refutation, or claim status."
    )
    items: list[LoopMemoryItem]


class GateState(LoopBaseModel):
    passed: bool
    evidence_path: str = ""
    note: str = ""


class StopGate(LoopBaseModel):
    status: Literal["RUNNING", "SUCCESS", "STOP_BUDGET_EXHAUSTED", "NEEDS_HUMAN_REVIEW"]
    minimum_cycles_required: int = 5
    max_cycles: int = 12
    max_wall_time_hours: float = 10
    consecutive_clean_cycles_required: int = 2
    consecutive_clean_cycles_observed: int = 0
    cycles_completed: int = 0
    wall_time_hours_used: float = 0.0
    tested_commit: str = ""
    tested_commit_unchanged_during_clean_streak: bool = False
    safety_gate: GateState
    capability_gate: GateState
    ux_gate: GateState
    dogfood_coverage_gate: GateState
    replay_gate: GateState
    holdout_gate: GateState
    scorecard_gate: GateState
    calibration_gate: GateState
    open_p0_or_p1_in_gap_ledger: bool = True
    decision_evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_success_conditions(self) -> StopGate:
        if self.status == "SUCCESS":
            gates = [
                self.safety_gate,
                self.capability_gate,
                self.ux_gate,
                self.dogfood_coverage_gate,
                self.replay_gate,
                self.holdout_gate,
                self.scorecard_gate,
                self.calibration_gate,
            ]
            if self.cycles_completed < self.minimum_cycles_required:
                raise ValueError("SUCCESS requires at least the minimum cycle count")
            if self.consecutive_clean_cycles_observed < self.consecutive_clean_cycles_required:
                raise ValueError("SUCCESS requires the consecutive clean-cycle streak")
            if not self.tested_commit_unchanged_during_clean_streak:
                raise ValueError("SUCCESS requires an unchanged tested commit during the streak")
            if not all(gate.passed for gate in gates):
                raise ValueError("SUCCESS requires every stop-gate condition to pass")
            if self.open_p0_or_p1_in_gap_ledger:
                raise ValueError("SUCCESS is impossible with open P0/P1 gaps in the ledger")
        if self.status == "STOP_BUDGET_EXHAUSTED" and self.cycles_completed == 0:
            raise ValueError("budget exhaustion requires at least one attempted cycle")
        return self


__all__ = [name for name in dir() if name[0].isupper()]
