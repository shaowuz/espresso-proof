"""Proof verification (the checked-status recompute gate), finite witnesses, external tools."""

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
from espresso_proof.kernel.recompute import checked_entry_rejection_reason
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validation.manifest import ManifestPathValidationError
from espresso_proof.validation.manifest import validate_relative_artifact_path
from pathlib import Path
import re

from espresso_proof.kernel.validators.runtime_status import validate_yaml_model


# verifier_type -> the only checked status it may earn (with a confirming result).
_TRUSTED_EARNED = {
    "finite_group_cayley": "instance_checked",
    "finite_identity_table": "instance_checked",
    "finite_ring_identity": "instance_checked",
    "matrix_rank": "instance_checked",
    "exact_computation": "computation_checked",
}

def _load_optional_proof_verification(
    run_dir: Path,
    manifest: RunManifest,
) -> ProofVerificationReport | None:
    optional = manifest.optional_artifacts
    if "proof_verification" not in optional:
        # An unregistered file must not be silently exempt from validation. A
        # run that carries proof_verification.yaml on disk without the manifest
        # entry used to skip every gate below — including the recompute gate —
        # while any consumer reading the file by name still saw its entries.
        # Fail closed: present means validated, or the run is invalid.
        stray = run_dir / "proof_verification.yaml"
        if stray.exists():
            raise KernelValidationError(
                "proof_verification.yaml is present but not registered in "
                "run_manifest.optional_artifacts; an unregistered verification "
                "artifact would bypass the checked-status gates"
            )
        return None
    return validate_yaml_model(
        run_dir / optional["proof_verification"],
        ProofVerificationReport,
    )

def _load_optional_finite_object_witness(
    run_dir: Path,
    manifest: RunManifest,
    claim: Claim,
) -> FiniteObjectWitnessReport | None:
    optional = manifest.optional_artifacts
    if "finite_object_witness" not in optional:
        return None
    witness = validate_yaml_model(
        run_dir / optional["finite_object_witness"],
        FiniteObjectWitnessReport,
    )
    if witness.claim_id != claim.id:
        raise KernelValidationError("finite_object_witness claim_id does not match locked claim")
    if witness.run_id != manifest.run_id:
        raise KernelValidationError("finite_object_witness run_id does not match manifest")
    return witness

def _load_optional_external_tool_candidates(
    run_dir: Path,
    manifest: RunManifest,
) -> ExternalToolCandidatesReport | None:
    optional = manifest.optional_artifacts
    if "external_tool_candidates" not in optional:
        return None
    # The model has no source/evidence/proof status field, so loading it is the
    # candidate-only guard: a discovered tool can never earn a trusted status.
    return validate_yaml_model(
        run_dir / optional["external_tool_candidates"],
        ExternalToolCandidatesReport,
    )

def _load_optional_external_tool_runs(
    run_dir: Path,
    manifest: RunManifest,
) -> ExternalToolRunsReport | None:
    optional = manifest.optional_artifacts
    if "external_tool_runs" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["external_tool_runs"],
        ExternalToolRunsReport,
    )

def _validate_external_tool_artifacts(
    candidates: ExternalToolCandidatesReport | None,
    runs: ExternalToolRunsReport | None,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    """External-tool discovery + run records are RAW evidence, never a status.

    Both report models carry no source/evidence/proof/grade field (StrictBaseModel
    forbids extras), so an external tool cannot write a trusted status by
    construction -- only the kernel-gated proof-verification bridge (a trusted
    deterministic backend with a confirming result) may earn a SCOPED status.
    Here we bind claim_id / run_id and confine every recorded path to the run
    directory (no absolute path, no ``..``); a tool run is always scoped
    (``not_universal_proof`` is pinned True at the model level). Fail closed.
    """

    def _confine(paths: list[str], where: str) -> None:
        for raw in paths:
            try:
                validate_relative_artifact_path(
                    where,
                    raw,
                    path_name="external_tool_runs path",
                )
            except ManifestPathValidationError as exc:
                raise KernelValidationError(
                    f"external_tool_runs {str(exc)}"
                ) from exc

    if candidates is not None:
        if candidates.claim_id != claim.id:
            raise KernelValidationError(
                "external_tool_candidates claim_id does not match locked claim"
            )
        if candidates.run_id != manifest.run_id:
            raise KernelValidationError(
                "external_tool_candidates run_id does not match the run manifest"
            )
    if runs is not None:
        if runs.claim_id != claim.id:
            raise KernelValidationError(
                "external_tool_runs claim_id does not match locked claim"
            )
        if runs.run_id != manifest.run_id:
            raise KernelValidationError(
                "external_tool_runs run_id does not match the run manifest"
            )
        for run in runs.runs:
            _confine([run.working_dir], "working_dir")
            _confine(run.input_files, "input_files")
            _confine(run.output_files, "output_files")

def _validate_proof_verification(
    proof_verification: ProofVerificationReport | None,
    claim: Claim,
    manifest: RunManifest,
    idea_grade: IdeaGrade,
) -> None:
    """Proof verification bridge: only a trusted deterministic backend with a
    confirming result may earn a checked status. Fails closed.

    Rules (the report model already excludes a ``proved`` status by construction):

    - claim_id / run_id bind to this run, and every entry's claim_id must match
      the locked claim (a verifier result for a different claim cannot be
      attached here);
    - a checked status (``instance_checked`` / ``computation_checked``) requires
      ``trusted`` AND ``confirmed`` AND the matching trusted ``verifier_type``;
    - ``lean_checked`` is NOT earnable in V0 (reserved) -> always rejected;
    - ``human_attested`` is NOT earnable in V0 either -> always rejected: there
      is no human-attestation artifact for the kernel to re-derive it from, so
      an entry claiming it is unverifiable say-so (fail closed);
    - a non-trusted or non-confirming entry, and any ``lean_bridge`` entry, must be
      ``unverified``.

    Natural-language prover/reviewer approval never reaches this report; it cannot
    set any checked status.
    """
    if proof_verification is None:
        return
    if proof_verification.claim_id != claim.id:
        raise KernelValidationError("proof_verification claim_id does not match locked claim")
    if proof_verification.run_id != manifest.run_id:
        raise KernelValidationError("proof_verification run_id does not match the run manifest")
    for entry in proof_verification.entries:
        vt = entry.verifier_type.value
        earned = entry.earned_status.value
        if entry.claim_id != claim.id:
            raise KernelValidationError(
                f"proof_verification entry {entry.id!r} targets a different claim"
            )
        if earned == "lean_checked":
            raise KernelValidationError(
                f"proof_verification entry {entry.id!r}: lean_checked is not earnable in V0"
            )
        if earned in {"instance_checked", "computation_checked"}:
            # trusted / confirmed / target are written by whoever wrote the file,
            # so they are a declaration, not evidence. The kernel re-derives the
            # result itself, binds the status to WHAT the adapter says it checked,
            # and compares its own transcript hash. The rule lives in
            # kernel/recompute.py because every consumer that acts on checked
            # evidence has to ask the same question — a consumer with its own copy
            # of "trusted and confirmed" is how the gate got bypassed before.
            reason = checked_entry_rejection_reason(entry.model_dump(mode="json"))
            if reason is not None:
                raise KernelValidationError(
                    f"proof_verification entry {entry.id!r} claims {earned} but {reason}"
                )
            if _TRUSTED_EARNED.get(vt) != earned:
                raise KernelValidationError(
                    f"proof_verification entry {entry.id!r}: verifier_type {vt!r} cannot "
                    f"earn {earned!r}"
                )
        elif earned == "human_attested":
            raise KernelValidationError(
                f"proof_verification entry {entry.id!r}: human_attested is not earnable "
                "in V0: there is no human-attestation artifact for the kernel to check, "
                "so a recorded attestation would be unverifiable say-so (fail closed)"
            )
        # earned == unverified is always acceptable (fail-closed default)
        if vt == "lean_bridge" and earned != "unverified":
            raise KernelValidationError(
                f"proof_verification entry {entry.id!r}: lean_bridge cannot earn {earned!r} in V0"
            )

def _validate_grade_10_requires_global_proof_status(idea_grade: IdeaGrade) -> None:
    """A perfect score is a global proof-strength claim, not local support.

    Source-backed theorem applicability is intentionally step-local. It can
    support an individual proof step, but it cannot by itself make the whole
    route proved or justify a Grade-10/perfect grade while the proof-language
    status is still weak or scoped.
    """
    weak_or_scoped_statuses = {
        ProofLanguageStatus.idea_sketch,
        ProofLanguageStatus.proof_candidate,
        # Agent consensus is amber/advisory, never a global machine-checked proof,
        # so it can never imply a perfect score.
        ProofLanguageStatus.agent_consensus_mock_checked,
        ProofLanguageStatus.source_checked_argument,
        ProofLanguageStatus.computation_checked,
    }
    if idea_grade.score == 100 and idea_grade.proof_language_status in weak_or_scoped_statuses:
        raise KernelValidationError(
            "idea_grade.score=100 requires a global checked proof status; local "
            "source-backed/applicable steps, proof candidates, and scoped checks cannot "
            "upgrade the grade to Grade 10"
        )
