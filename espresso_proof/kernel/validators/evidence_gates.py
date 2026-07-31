"""Computation verification, failure classification/patterns, mathematical judgment, refuted-grade admissibility, human feedback."""

from __future__ import annotations

from espresso_proof.kernel.models import (
    AgentConsensusReview,
    AgentConsensusVerdict,
    AIReviewAssessment,
    Claim,
    ComputationVerificationReport,
    ProofGoalGraph,
    ProofVerificationReport,
    ProofVerificationStatus,
    SubgoalState,
    IdeaGrade,
    IdeaStatus,
    LineNumberedProof,
    LLMAgentRunReport,
    LiteratureReviewAgentReport,
    ProofLanguageStatus,
    ProverLoopReport,
    ProverRoundIndex,
    Review,
    ReviewerVerdict,
    RunManifest,
    SourceGroundingReport,
    SourceStatus,
    StrategyBrief,
    TheoremApplicabilityReport,
    TheoremApplicabilityReviewArtifact,
    TheoremConditionLedger,
    TheoremExtractionReport,
)
from espresso_proof.kernel.models import (
    AgentConsensusReview,
    AutonomousLiteratureRetrievalReport,
    Claim,
    ComputationVerificationReport,
    ExternalToolCandidatesReport,
    ExternalToolRunsReport,
    FiniteObjectWitnessReport,
    LiteratureCandidates,
    MemoryContext,
    RetrievalResolutionReport,
    LiteratureRetrievalReport,
    LiteratureSearchPlan,
    ProofVerificationReport,
    ProofVerificationStatus,
    ProofGapResolutionReport,
    IdeaGrade,
    LineNumberedProof,
    ProofLanguageStatus,
    VerificationVerdict,
    RunManifest,
    SourceGroundingReport,
    SourceStatus,
    TheoremExtractionReport,
)
from espresso_proof.kernel.models import (
    AgentContracts,
    AttemptStatus,
    Claim,
    ComputationChecks,
    CounterexampleReport,
    ContinueDecision,
    ExampleSelection,
    ExampleFeedbackSummary,
    HypothesisLedger,
    HumanChoice,
    HumanExampleFeedback,
    HumanChoiceStatus,
    IdeaGrade,
    IdeaStatus,
    LiteratureReviewReport,
    LoopControl,
    ProofLanguageStatus,
    Review,
    ReviewerVerdict,
    RunManifest,
    SourceGroundingReport,
    SourceStatus,
    StrategyBrief,
    StrategySynthesis,
    TestReport,
    TheoremApplicabilityReport,
    TheoremExtractionReport,
)
from espresso_proof.kernel.models import (
    AgentContracts,
    Claim,
    ComputationChecks,
    CounterexampleReport,
    ExampleSelection,
    HypothesisLedger,
    HumanChoice,
    ExampleStatus,
    IdeaGrade,
    LLMAgentRunReport,
    LiteratureReviewReport,
    LoopControl,
    ProofLanguageStatus,
    ProverLoopReport,
    ProverRoundIndex,
    ProverRoundReport,
    Review,
    ReviewerVerdict,
    RunManifest,
    SourceGroundingReport,
    SourceStatus,
    StrategyBrief,
    TestReport,
    TheoremApplicabilityReport,
    UpdateInboxReport,
)
from espresso_proof.kernel.models import (
    AgentRunStatusReport,
    Claim,
    CounterexampleReport,
    _has_run_artifact_provenance,
    PressureLaneBarrierReport,
    ExampleSelection,
    ExampleStatus,
    PressureStatusReport,
    RunManifest,
    StageStatusReport,
    TimeAllocationPlan,
    UpdateInboxReport,
)
from espresso_proof.kernel.models import (
    Claim,
    ComputationVerificationReport,
    CounterexampleReport,
    FailureClassificationReport,
    FailurePatternAudit,
    LiteratureRetrievalReport,
    MathematicalJudgmentReport,
    ExampleFeedbackSummary,
    HumanExampleFeedback,
    IdeaGrade,
    IdeaStatus,
    RunManifest,
    StrategyBrief,
    StrategySynthesis,
    TimeAllocationPlan,
    UpdateInboxReport,
)
from espresso_proof.kernel.pressure_status import (
    artifact_revision_hash,
    derive_counterexamples_pressure_state,
    derive_examples_pressure_state,
    is_admissible_refutation,
    is_inadmissible_counterexample_pressure,
    needs_counterexample_admissibility,
)
from espresso_proof.kernel.pressure_status import (
    is_admissible_refutation,
)
from espresso_proof.validation.errors import KernelValidationError
from pathlib import Path

from espresso_proof.kernel.validators.runtime_status import validate_yaml_model


def _load_optional_human_feedback(
    run_dir: Path,
    manifest: RunManifest,
) -> tuple[HumanExampleFeedback | None, ExampleFeedbackSummary | None]:
    optional = manifest.optional_artifacts
    feedback = None
    summary = None
    if "human_example_feedback" in optional:
        feedback = validate_yaml_model(
            run_dir / optional["human_example_feedback"],
            HumanExampleFeedback,
        )
    if "example_feedback_summary" in optional:
        summary = validate_yaml_model(
            run_dir / optional["example_feedback_summary"],
            ExampleFeedbackSummary,
        )
    return feedback, summary

def _load_optional_computation_verification(
    run_dir: Path,
    manifest: RunManifest,
) -> ComputationVerificationReport | None:
    optional = manifest.optional_artifacts
    if "computation_verification" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["computation_verification"],
        ComputationVerificationReport,
    )

def _load_optional_failure_classification(
    run_dir: Path,
    manifest: RunManifest,
) -> FailureClassificationReport | None:
    optional = manifest.optional_artifacts
    if "failure_classification" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["failure_classification"],
        FailureClassificationReport,
    )

def _has_admissible_counterexample(counterexamples: CounterexampleReport) -> bool:
    """Single source of truth for "what counts as a real refutation".

    True iff some counterexample is an admissible CHECKED refutation: it satisfies
    every claim hypothesis (``satisfies_all_claim_hypotheses is True``) AND refutes
    the claim. ``refutes_claim`` already requires a checked ``failed`` result and
    forbids dropped-hypothesis / proof-step-only cases at the model level
    (``CounterexampleTest.validate_refutation_scope``). Both the failure-
    classification gate and the idea-grade ``refuted`` gate use this, so the
    refutation definition cannot drift between them.
    """
    return any(
        is_admissible_refutation(test)
        for test in counterexamples.tests
    )

def _validate_failure_classification(
    failure_classification: FailureClassificationReport | None,
    counterexamples: CounterexampleReport,
    claim: Claim,
) -> None:
    """Failure classification is advisory routing; it must not fabricate a refutation.

    Only runs when the optional artifact is present. Rules:

    - claim_id must match the locked claim;
    - a ``claim_refuted`` classification requires an admissible all-hypotheses
      counterexample (satisfies_all_claim_hypotheses is True and refutes_claim),
      so a dropped-hypothesis or proof-step failure can never be promoted to a
      claim refutation through routing.

    The report model carries no source/evidence/proof status field, so a
    classification cannot upgrade any status by construction.
    """
    if failure_classification is None:
        return
    if failure_classification.claim_id != claim.id:
        raise KernelValidationError(
            "failure_classification claim_id does not match locked claim"
        )
    claims_refuted = any(
        item.category.value == "claim_refuted"
        for item in failure_classification.classifications
    )
    if claims_refuted and not _has_admissible_counterexample(counterexamples):
        raise KernelValidationError(
            "failure_classification marks claim_refuted without an admissible "
            "all-hypotheses counterexample"
        )

def _validate_refuted_grade_admissible(
    idea_grade: IdeaGrade,
    counterexamples: CounterexampleReport,
) -> None:
    """A claim may be graded ``refuted`` ONLY when an admissible checked
    counterexample exists: a counterexample that satisfies every claim hypothesis
    AND refutes the claim (``refutes_claim`` already requires a checked ``failed``
    result and forbids dropped-hypothesis / proof-step-only cases at the model
    level). This makes ``refuted`` a kernel-earned status, not something model
    prose, a reviewer objection, memory, or a forged grade can assert -- and it
    retroactively hardens the subgoal-graph ``contradicted`` gate, which trusts
    ``idea_grade.status == refuted``.
    """
    if idea_grade.status != IdeaStatus.refuted:
        return
    if not _has_admissible_counterexample(counterexamples):
        raise KernelValidationError(
            "idea_grade.status=refuted requires an admissible checked counterexample "
            "(satisfies every claim hypothesis and refutes the claim); a route attack, "
            "reviewer objection, dropped-hypothesis example, or unchecked counterexample "
            "cannot refute the claim"
        )

def _load_optional_mathematical_judgment(
    run_dir: Path,
    manifest: RunManifest,
) -> MathematicalJudgmentReport | None:
    optional = manifest.optional_artifacts
    if "mathematical_judgment" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["mathematical_judgment"],
        MathematicalJudgmentReport,
    )

def _validate_mathematical_judgment(
    mathematical_judgment: MathematicalJudgmentReport | None,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    """The mathematical judgment layer is advisory; it must never upgrade status.

    Only runs when the optional artifact is present. The report model carries no
    source/evidence/proof status field and StrictBaseModel forbids extras, so a
    YAML that smuggles in ``source_backed`` / checked evidence / a proof status is
    already rejected at load (validate_yaml_model). Here we additionally bind the
    report to this run: claim_id must match the locked claim and run_id must match
    the manifest, so a judgment for another claim/run cannot be attached here.
    """
    if mathematical_judgment is None:
        return
    if mathematical_judgment.claim_id != claim.id:
        raise KernelValidationError(
            "mathematical_judgment claim_id does not match locked claim"
        )
    if mathematical_judgment.run_id != manifest.run_id:
        raise KernelValidationError(
            "mathematical_judgment run_id does not match the run manifest"
        )

def _load_optional_failure_pattern_audit(
    run_dir: Path,
    manifest: RunManifest,
) -> FailurePatternAudit | None:
    optional = manifest.optional_artifacts
    if "failure_pattern_audit" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["failure_pattern_audit"],
        FailurePatternAudit,
    )

def _validate_failure_pattern_audit(
    failure_pattern_audit: FailurePatternAudit | None,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    """The failure-pattern audit is advisory; it must not relabel or upgrade.

    Only runs when the optional artifact is present. Rules (fail closed):

    - claim_id / run_id must bind to this run;
    - every matched pattern_id must exist in the canonical SEED bank, and its
      tier / action / family / enforced_by must equal the bank verbatim -- so a
      run (or memory, or a human edit) can NEVER promote a candidate pattern to
      ``hard`` or change what it is allowed to do;
    - a ``hard`` match must point at an EXISTING kernel validator
      (``enforced_by`` in ENFORCING_VALIDATORS); it adds no new blocking.

    The audit model carries no source/evidence/proof status field (and extras are
    forbidden), so a match can never upgrade any status or refute the claim.
    """
    if failure_pattern_audit is None:
        return
    # Local import: failure_pattern_bank imports models+io only (no cycle).
    from espresso_proof.kernel.failure_pattern_bank import ENFORCING_VALIDATORS, seed_failure_pattern_bank

    if failure_pattern_audit.claim_id != claim.id:
        raise KernelValidationError(
            "failure_pattern_audit claim_id does not match locked claim"
        )
    if failure_pattern_audit.run_id != manifest.run_id:
        raise KernelValidationError(
            "failure_pattern_audit run_id does not match the run manifest"
        )
    canonical = {p.id: p for p in seed_failure_pattern_bank().patterns}
    for match in failure_pattern_audit.matches:
        pattern = canonical.get(match.pattern_id)
        if pattern is None:
            raise KernelValidationError(
                f"failure_pattern_audit references unknown pattern {match.pattern_id!r}"
            )
        if (
            match.tier != pattern.tier
            or match.action != pattern.action
            or match.family != pattern.family
            or match.enforced_by != pattern.enforced_by
        ):
            raise KernelValidationError(
                f"failure_pattern_audit relabels pattern {match.pattern_id!r} "
                "(tier/action/family/enforced_by must equal the canonical bank)"
            )
        if match.tier.value == "hard" and match.enforced_by not in ENFORCING_VALIDATORS:
            raise KernelValidationError(
                f"hard pattern {match.pattern_id!r} must be enforced by an existing "
                "kernel validator"
            )

def _feedback_target_sets(
    examples: ExampleSelection,
    counterexamples: CounterexampleReport,
    tests: TestReport,
    source_grounding: SourceGroundingReport,
    strategy_brief: StrategyBrief,
) -> dict[str, tuple[str, set[str]]]:
    return {
        "example": ("examples.yaml", {example.id for example in examples.examples}),
        "counterexample_test": (
            "counterexamples.yaml",
            {test.id for test in counterexamples.tests},
        ),
        "test": ("tests.yaml", {test.id for test in tests.tests}),
        "source_ref": ("source_grounding.yaml", {ref.id for ref in source_grounding.source_refs}),
        "source_step": (
            "source_grounding.yaml",
            {step.proof_step_id for step in source_grounding.steps},
        ),
        "strategy": (
            "strategy_brief.yaml",
            {strategy.id for strategy in strategy_brief.top_strategies},
        ),
    }

def _validate_human_feedback(
    manifest: RunManifest,
    claim: Claim,
    examples: ExampleSelection,
    counterexamples: CounterexampleReport,
    tests: TestReport,
    source_grounding: SourceGroundingReport,
    strategy_brief: StrategyBrief,
    feedback: HumanExampleFeedback | None,
    summary: ExampleFeedbackSummary | None,
) -> None:
    if feedback is None:
        if summary is not None:
            raise KernelValidationError("example_feedback_summary requires human_example_feedback")
        return

    if feedback.project_id != manifest.project_id:
        raise KernelValidationError("human_example_feedback project_id does not match manifest")
    if feedback.run_id != manifest.run_id:
        raise KernelValidationError("human_example_feedback run_id does not match manifest")
    if feedback.claim_id != claim.id:
        raise KernelValidationError("human_example_feedback claim_id does not match locked claim")

    target_sets = _feedback_target_sets(
        examples,
        counterexamples,
        tests,
        source_grounding,
        strategy_brief,
    )
    feedback_by_id = {}
    for item in feedback.feedback:
        feedback_by_id[item.feedback_id] = item
        if not item.human_confirmed:
            raise KernelValidationError(f"{item.feedback_id}: unconfirmed feedback cannot enter a run")
        expected_artifact, allowed_ids = target_sets[item.target_type.value]
        if item.target_artifact != expected_artifact:
            raise KernelValidationError(
                f"{item.feedback_id}: {item.target_type.value} feedback must target {expected_artifact}"
            )
        if item.target_id not in allowed_ids:
            raise KernelValidationError(
                f"{item.feedback_id}: unknown {item.target_type.value} target {item.target_id!r}"
            )

    if summary is None:
        return

    if summary.claim_id != claim.id:
        raise KernelValidationError("example_feedback_summary claim_id does not match locked claim")
    if summary.run_id != manifest.run_id:
        raise KernelValidationError("example_feedback_summary run_id does not match manifest")
    if summary.feedback_artifact_path != manifest.optional_artifacts.get("human_example_feedback"):
        raise KernelValidationError("example_feedback_summary must cite the human feedback artifact path")

    feedback_ids = set(feedback_by_id)
    unknown_summary_ids = set(summary.summarized_from).difference(feedback_ids)
    if unknown_summary_ids:
        raise KernelValidationError(
            f"example_feedback_summary references unknown feedback ids {sorted(unknown_summary_ids)}"
        )

    for adjustment in summary.ranking_adjustments:
        unknown_feedback_ids = set(adjustment.feedback_ids).difference(feedback_ids)
        if unknown_feedback_ids:
            raise KernelValidationError(
                f"ranking adjustment references unknown feedback ids {sorted(unknown_feedback_ids)}"
            )
        expected_artifact, allowed_ids = target_sets[adjustment.target_type.value]
        if adjustment.target_artifact != expected_artifact:
            raise KernelValidationError(
                f"ranking adjustment for {adjustment.target_type.value} must target {expected_artifact}"
            )
        if adjustment.target_id not in allowed_ids:
            raise KernelValidationError(
                f"ranking adjustment references unknown {adjustment.target_type.value} target "
                f"{adjustment.target_id!r}"
            )
