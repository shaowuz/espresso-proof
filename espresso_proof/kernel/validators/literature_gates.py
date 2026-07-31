"""Literature retrieval/candidates/resolution, proof-gap resolution, memory context."""

from __future__ import annotations

from espresso_proof.kernel.io import path_exists, path_is_file, read_text, read_yaml
from espresso_proof.kernel.io import path_exists, read_yaml
from espresso_proof.kernel.io import read_yaml
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
    artifact_revision_hash,
)
from espresso_proof.validation.errors import KernelValidationError
from pathlib import Path
from pydantic import BaseModel, ValidationError
from pydantic import ValidationError

from espresso_proof.kernel.validators.runtime_status import _adapt_legacy_memory_context, _memory_context_schema_version, validate_yaml_model


def _load_optional_literature_retrieval(
    run_dir: Path,
    manifest: RunManifest,
) -> LiteratureRetrievalReport | None:
    optional = manifest.optional_artifacts
    if "literature_retrieval" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["literature_retrieval"],
        LiteratureRetrievalReport,
    )

def _load_optional_literature_search_plan(
    run_dir: Path,
    manifest: RunManifest,
) -> LiteratureSearchPlan | None:
    optional = manifest.optional_artifacts
    if "literature_search_plan" not in optional:
        return None
    # The model has no status field, so loading it is the candidate-only guard:
    # a plan can never carry source_backed / proof status.
    return validate_yaml_model(
        run_dir / optional["literature_search_plan"],
        LiteratureSearchPlan,
    )

def _load_optional_autonomous_literature_retrieval(
    run_dir: Path,
    manifest: RunManifest,
) -> AutonomousLiteratureRetrievalReport | None:
    optional = manifest.optional_artifacts
    if "autonomous_literature_retrieval" not in optional:
        return None
    # AutonomousRetrievalCandidate.status is Literal["candidate"]; validating here
    # fails closed if any candidate were ever written with another status.
    return validate_yaml_model(
        run_dir / optional["autonomous_literature_retrieval"],
        AutonomousLiteratureRetrievalReport,
    )

def _validate_autonomous_literature_retrieval(
    report: AutonomousLiteratureRetrievalReport | None,
    source_grounding: SourceGroundingReport,
    theorem_extraction: TheoremExtractionReport | None,
) -> None:
    """Bind autonomous retrieval's earned flag to source-grounding truth.

    The retrieval sidecar is provenance/scheduling context. If it records that
    grounding earned source support, the current validated source-grounding
    artifact must actually contain earned source-backed refs/steps for the
    candidate/source this retrieval handed to grounding.
    """

    if report is None or not report.earned_source_backed:
        return
    if not report.grounded:
        raise KernelValidationError(
            "autonomous_literature_retrieval earned_source_backed requires grounded=true"
        )
    if not report.grounded_candidate_id or not report.grounded_retrieval_source_id:
        raise KernelValidationError(
            "autonomous_literature_retrieval earned_source_backed requires grounded candidate identity"
        )
    candidates_by_id = {candidate.id: candidate for candidate in report.candidates}
    grounded_candidate = candidates_by_id.get(report.grounded_candidate_id)
    if grounded_candidate is None:
        raise KernelValidationError(
            "autonomous_literature_retrieval grounded_candidate_id is not one of its candidates"
        )
    if grounded_candidate.provider != "local_corpus" or not grounded_candidate.local_path:
        raise KernelValidationError(
            "autonomous_literature_retrieval earned_source_backed requires a grounded local candidate"
        )
    expected_source_id = f"ret_{Path(grounded_candidate.local_path).stem}"
    if report.grounded_retrieval_source_id != expected_source_id:
        raise KernelValidationError(
            "autonomous_literature_retrieval grounded source does not match grounded candidate"
        )
    if theorem_extraction is None:
        raise KernelValidationError(
            "autonomous_literature_retrieval earned_source_backed requires theorem_extraction binding"
        )
    allowed_ref_ids = {
        f"verified_{theorem.id}"
        for theorem in theorem_extraction.theorems
        if theorem.source_ref_id == report.grounded_retrieval_source_id
    }
    reported_ref_ids = set(report.earned_source_ref_ids)
    if not reported_ref_ids or not reported_ref_ids <= allowed_ref_ids:
        raise KernelValidationError(
            "autonomous_literature_retrieval earned source refs do not match grounded retrieval source"
        )
    refs_by_id = {ref.id: ref for ref in source_grounding.source_refs}
    if any(
        refs_by_id.get(ref_id) is None
        or refs_by_id[ref_id].status != SourceStatus.source_backed
        for ref_id in reported_ref_ids
    ):
        raise KernelValidationError(
            "autonomous_literature_retrieval earned source refs are not source-grounding backed"
        )
    reported_step_ids = set(report.earned_proof_step_ids)
    if not reported_step_ids:
        raise KernelValidationError(
            "autonomous_literature_retrieval earned_source_backed requires an "
            "earned source-grounding step"
        )
    steps_by_id = {step.proof_step_id: step for step in source_grounding.steps}
    for step_id in reported_step_ids:
        step = steps_by_id.get(step_id)
        if step is None or step.status != SourceStatus.source_backed:
            raise KernelValidationError(
                "autonomous_literature_retrieval earned proof step is not source-backed"
            )
        if not set(step.source_refs) & reported_ref_ids:
            raise KernelValidationError(
                "autonomous_literature_retrieval earned proof step is not bound to earned source ref"
            )
    if not any(
        step.status == SourceStatus.source_backed
        and set(step.source_refs) & reported_ref_ids
        for step in source_grounding.steps
    ):
        raise KernelValidationError(
            "autonomous_literature_retrieval earned_source_backed requires an "
            "earned source-grounding step bound to the grounded retrieval source"
        )

def _load_optional_literature_candidates(
    run_dir: Path,
    manifest: RunManifest,
) -> LiteratureCandidates | None:
    optional = manifest.optional_artifacts
    if "literature_candidates" not in optional:
        return None
    # LiteratureCandidate.status is Literal["candidate"]; validating here fails
    # closed if any candidate were ever written (or hand-edited) with another
    # status (e.g. source_backed). The scout artifact has no checked/proof/grade
    # field, so it can never upgrade trusted status.
    return validate_yaml_model(
        run_dir / optional["literature_candidates"],
        LiteratureCandidates,
    )

def _load_optional_retrieval_resolution(
    run_dir: Path,
    manifest: RunManifest,
) -> RetrievalResolutionReport | None:
    optional = manifest.optional_artifacts
    if "retrieval_resolution" not in optional:
        return None
    # RetrievalResolutionReport (StrictBaseModel, extra=forbid) has no
    # source_backed / checked / proof / grade field; validating here fails closed
    # if a retrieval report were ever written/hand-edited with a trusted status.
    return validate_yaml_model(
        run_dir / optional["retrieval_resolution"],
        RetrievalResolutionReport,
    )

def _load_optional_proof_gap_resolution(
    run_dir: Path,
    manifest: RunManifest,
) -> ProofGapResolutionReport | None:
    optional = manifest.optional_artifacts
    if "proof_gap_resolution" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["proof_gap_resolution"],
        ProofGapResolutionReport,
    )

def _validate_proof_gap_resolution(
    report: ProofGapResolutionReport | None,
    claim: Claim,
    manifest: RunManifest,
    run_dir: Path,
) -> None:
    if report is None:
        return
    if report.claim_id != claim.id:
        raise KernelValidationError("proof_gap_resolution claim_id does not match locked claim")
    if report.run_id != manifest.run_id:
        raise KernelValidationError("proof_gap_resolution run_id does not match manifest")
    proof_artifact = manifest.optional_artifacts.get("line_numbered_proof")
    if not proof_artifact:
        raise KernelValidationError("proof_gap_resolution requires a line_numbered_proof artifact")
    if report.proof_artifact != proof_artifact:
        raise KernelValidationError("proof_gap_resolution proof_artifact does not match line_numbered_proof")
    if report.line_numbered_proof_sha256 != artifact_revision_hash(run_dir, proof_artifact):
        raise KernelValidationError("proof_gap_resolution is stale for the current line_numbered_proof")
    proof = validate_yaml_model(run_dir / proof_artifact, LineNumberedProof)
    proof_dependencies = {
        (line.line_id, dep.ref): dep
        for line in proof.lines
        for dep in line.dependencies
    }
    seen: set[tuple[str, str]] = set()
    for candidate in report.candidates:
        key = (candidate.line_id, candidate.dependency_ref)
        if key in seen:
            raise KernelValidationError("duplicate proof gap resolution candidate")
        seen.add(key)
        if candidate.action == "attach_human_allowed_theorem_ref":
            if candidate.status != "accepted_by_human":
                raise KernelValidationError("human-allowed proof gap candidate must be accepted_by_human")
            if candidate.source_grounded or candidate.machine_verified:
                raise KernelValidationError("human-allowed theorem refs are advisory, not source-grounded or machine-verified")
            dep = proof_dependencies.get((candidate.line_id, candidate.proposed_ref))
            if dep is None:
                raise KernelValidationError("human-allowed proof gap candidate is not attached to the proof graph")
            if dep.authority != "human_allowed_theorem_ref" or dep.status != "accepted_by_human":
                raise KernelValidationError("human-allowed proof dependency lacks trusted advisory provenance")
            if dep.source_grounded or dep.machine_verified:
                raise KernelValidationError("human-allowed proof dependency must remain advisory")
        if candidate.action == "attach_candidate_theorem_ref":
            if candidate.status != "candidate":
                raise KernelValidationError("agent source-locator theorem refs must remain candidate until reviewed")
            dep = proof_dependencies.get((candidate.line_id, candidate.proposed_ref))
            if dep is None:
                raise KernelValidationError("agent proof gap candidate is not attached to the proof graph")

def _load_optional_memory_context(
    run_dir: Path,
    manifest: RunManifest,
) -> MemoryContext | None:
    optional = manifest.optional_artifacts
    if "memory_context" not in optional:
        return None
    # MemoryContext / MemoryContextEntry (StrictBaseModel, extra=forbid) have no
    # source/evidence/proof status field; validating here fails closed if a memory
    # context were ever written/hand-edited with a trusted status.
    artifact_path = optional["memory_context"]
    path = run_dir / artifact_path
    raw = read_yaml(path)
    if isinstance(raw, dict) and _memory_context_schema_version(raw) < 2:
        raw = _adapt_legacy_memory_context(raw, run_dir, artifact_path)
    try:
        return MemoryContext.model_validate(raw)
    except ValidationError as exc:
        raise KernelValidationError(f"{path}: schema validation failed\n{exc}") from exc

def _validate_memory_context(
    memory_context: MemoryContext,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    if memory_context.claim_id != claim.id:
        raise KernelValidationError(
            f"memory_context claim_id mismatch: {memory_context.claim_id!r} != {claim.id!r}"
        )
    if memory_context.run_id != manifest.run_id:
        raise KernelValidationError(
            f"memory_context run_id mismatch: {memory_context.run_id!r} != {manifest.run_id!r}"
        )
    if memory_context.schema_version < 2 and memory_context.matched_entries:
        raise KernelValidationError(
            "memory_context with matched_entries requires schema_version >= 2 replay metadata"
        )
    for entry in memory_context.matched_entries:
        if not entry.memory_claim_id.strip():
            raise KernelValidationError("memory_context entry requires memory_claim_id")
        if not entry.provenance:
            raise KernelValidationError("memory_context entry requires provenance")
        cross_claim = entry.memory_claim_id != claim.id
        if cross_claim and not entry.revalidation_required:
            raise KernelValidationError(
                "memory_context replay under a different locked claim requires revalidation"
            )
        if cross_claim and entry.revalidation_required and entry.recommended_use != "note":
            raise KernelValidationError(
                "pending cross-claim memory replay must be non-actionable"
            )
        if cross_claim and entry.revalidated_against_locked_claim:
            raise KernelValidationError(
                "memory_context cannot claim revalidation for a different locked claim"
            )

def _validate_literature_retrieval(
    literature_retrieval: LiteratureRetrievalReport | None,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    """Retrieval surfaces CANDIDATE sources only; it must not shortcut grounding.

    Only runs when the optional artifact is present. The hit status enum has no
    ``source_backed`` value (and StrictBaseModel forbids extras), so a retrieval
    hit can never be source_backed -- only Source Grounding earns that. Here we
    bind claim_id / run_id to this run (fail closed).
    """
    if literature_retrieval is None:
        return
    if literature_retrieval.claim_id != claim.id:
        raise KernelValidationError(
            "literature_retrieval claim_id does not match locked claim"
        )
    if literature_retrieval.run_id != manifest.run_id:
        raise KernelValidationError(
            "literature_retrieval run_id does not match the run manifest"
        )

def _load_optional_literature_review_agent(
    run_dir: Path,
    manifest: RunManifest,
) -> LiteratureReviewAgentReport | None:
    optional = manifest.optional_artifacts
    if "literature_review_agent" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["literature_review_agent"],
        LiteratureReviewAgentReport,
    )

def _validate_literature_review_separation(literature_review: LiteratureReviewReport) -> None:
    for item in literature_review.candidate_references:
        if item.status == SourceStatus.source_backed:
            raise KernelValidationError(
                f"{item.id}: Literature Review cannot mark a reference source_backed"
            )
        if item.status == SourceStatus.misapplied:
            raise KernelValidationError(
                f"{item.id}: Literature Review cannot decide misapplied; Referencing must do that"
            )
