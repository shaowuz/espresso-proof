"""Time allocation and update-inbox discipline."""

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
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validation.manifest import validate_relative_artifact_path
from pathlib import Path
import re

from espresso_proof.kernel.validators.language_gate import _first_forbidden_proof_language
from espresso_proof.kernel.validators.runtime_status import validate_yaml_model


def _load_optional_time_allocation(
    run_dir: Path,
    manifest: RunManifest,
) -> TimeAllocationPlan | None:
    optional = manifest.optional_artifacts
    if "time_allocation" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["time_allocation"],
        TimeAllocationPlan,
    )

def _load_optional_update_inbox(
    run_dir: Path,
    manifest: RunManifest,
) -> UpdateInboxReport | None:
    optional = manifest.optional_artifacts
    if "update_inbox" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["update_inbox"],
        UpdateInboxReport,
    )

def _contains_forbidden_allocation_token(value, forbidden: set[str]) -> bool:
    if isinstance(value, str):
        normalized = value.lower()
        if _first_forbidden_proof_language(normalized):
            return True
        allocation_only_patterns = [
            r"\bproof-close\b",
            r"\bsettled\b",
            r"\bverified\s+by\s+examples\b",
        ]
        return any(token in normalized for token in forbidden) or any(
            re.search(pattern, normalized) for pattern in allocation_only_patterns
        )
    if isinstance(value, dict):
        return any(
            str(key) in forbidden or _contains_forbidden_allocation_token(item, forbidden)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_forbidden_allocation_token(item, forbidden) for item in value)
    return False

def _validate_time_allocation(
    time_allocation: TimeAllocationPlan | None,
    claim: Claim,
    manifest: RunManifest,
    run_dir: Path,
    strategy_synthesis: StrategySynthesis,
    strategy_brief: StrategyBrief,
) -> None:
    if "time_allocation" in manifest.artifacts:
        raise KernelValidationError("time_allocation must be an optional scheduling artifact")
    if time_allocation is None:
        return
    if manifest.optional_artifacts.get("time_allocation") != "time_allocation.yaml":
        raise KernelValidationError("time_allocation optional artifact must be time_allocation.yaml")
    if time_allocation.claim_id != claim.id:
        raise KernelValidationError("time_allocation claim_id does not match locked claim")
    if time_allocation.run_id != manifest.run_id:
        raise KernelValidationError("time_allocation run_id does not match run manifest")
    if not time_allocation.advisory_only or time_allocation.effect != "scheduling_only":
        raise KernelValidationError("time_allocation must be scheduling-only/advisory")
    if time_allocation.allocation_state != "TIME_ALLOCATION_STARTED":
        raise KernelValidationError("time_allocation must record TIME_ALLOCATION_STARTED")

    artifact_path = run_dir / manifest.optional_artifacts["time_allocation"]
    if not path_exists(artifact_path):
        raise KernelValidationError("time_allocation optional artifact is missing")

    if time_allocation.strategy_artifact == "strategy_brief":
        route_ids = {route.id for route in strategy_brief.top_strategies}
    else:
        route_ids = {route.id for route in strategy_synthesis.routes}
    for entry in time_allocation.entries:
        if entry.route_id is not None and entry.route_id not in route_ids:
            raise KernelValidationError(
                f"time_allocation entry references unknown route_id: {entry.route_id}"
            )
        if entry.action == "bounded_proof_exploration" and not entry.unchecked:
            raise KernelValidationError("bounded proof exploration must be marked unchecked")

    forbidden = {
        "proof_status",
        "source_status",
        "evidence_status",
        "theorem_applicability_status",
        "refutation_status",
        "grade_status",
        "source_backed",
        "checked_evidence",
        "proved",
        "verified",
    }
    raw = read_yaml(artifact_path)
    expected_forbidden_effect = {
        "proof",
        "source",
        "evidence",
        "theorem_applicability",
        "refutation",
        "grade",
    }
    raw_forbidden_effect = raw.get("forbidden_status_effect")
    if (
        not isinstance(raw_forbidden_effect, list)
        or set(raw_forbidden_effect) != expected_forbidden_effect
        or len(raw_forbidden_effect) != len(expected_forbidden_effect)
    ):
        raise KernelValidationError(
            "time_allocation forbidden_status_effect cannot be removed or weakened"
        )
    if _contains_forbidden_allocation_token(raw, forbidden):
        raise KernelValidationError(
            "time_allocation cannot write proof/source/evidence/theorem/refutation/grade status"
        )

def _validate_update_inbox_applied_at_consumption(
    update_inbox: UpdateInboxReport,
    time_allocation: TimeAllocationPlan | None,
    manifest: RunManifest,
) -> None:
    applied_entries = [entry for entry in update_inbox.entries if entry.applied_at]
    if not applied_entries:
        return
    if time_allocation is None:
        raise KernelValidationError(
            "update_inbox applied_at requires time_allocation scheduling consumption"
        )
    time_allocation_dependencies = manifest.dependencies.get("time_allocation", {})
    if "update_inbox" not in time_allocation_dependencies.get("depends_on", []):
        raise KernelValidationError(
            "update_inbox applied_at requires time_allocation dependency on update_inbox"
        )

    allocation_by_route = {
        entry.route_id: entry
        for entry in time_allocation.entries
        if entry.route_id is not None
    }
    # Routes whose scheduler question a human has answered. The answer is what
    # releases the route from ask_human, so after it the route legitimately
    # carries a normal action while its objection entries remain applied — the
    # stamp still has an explanation, and that explanation is the person.
    answered_routes = {
        str(entry.route_id_or_step_id)
        for entry in update_inbox.entries
        if entry.type == "human_scheduling_decision"
        and entry.route_id_or_step_id
        and entry.effect == "scheduling_only"
        and entry.advisory_only is True
        and entry.validator_ok is True
    }
    for entry in applied_entries:
        if entry.type == "human_scheduling_decision":
            # A recorded human answer is itself consumable by the rebuilt plan.
            continue
        if entry.type != "reviewer_objection":
            raise KernelValidationError(
                "update_inbox applied_at is currently valid only for reviewer_objection "
                "scheduling consumption"
            )
        if entry.route_id_or_step_id is None:
            raise KernelValidationError(
                "update_inbox applied_at reviewer_objection requires a route binding"
            )
        allocation = allocation_by_route.get(entry.route_id_or_step_id)
        if allocation is None:
            raise KernelValidationError(
                "update_inbox applied_at requires matching time_allocation route"
            )
        if allocation.action not in {"repair_attempt", "ask_human"} and (
            str(entry.route_id_or_step_id) not in answered_routes
        ):
            raise KernelValidationError(
                "update_inbox applied_at requires a reviewer-objection time_allocation response"
            )

def _canonical_update_inbox_dedupe_key(entry) -> str:
    route = entry.route_id_or_step_id
    route_key = "whole_claim" if route is None else str(route)
    return "|".join(
        [
            str(entry.type.value if hasattr(entry.type, "value") else entry.type),
            str(entry.locked_claim_id),
            str(entry.origin_artifact),
            str(entry.origin_artifact_id_or_path),
            str(entry.artifact_revision_or_hash),
            route_key,
        ]
    )

def _contains_forbidden_update_inbox_token(value, forbidden_fields: set[str]) -> bool:
    if isinstance(value, str):
        normalized = value.lower()
        if _first_forbidden_proof_language(normalized):
            return True
        if any(
            re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", normalized)
            for token in forbidden_fields
        ):
            return True
        return any(
            re.search(pattern, normalized)
            for pattern in [
                r"\bproof-close\b",
                r"\bsettled\b",
                r"\bverified\s+by\s+examples\b",
            ]
        )
    if isinstance(value, dict):
        return any(
            str(key) in forbidden_fields
            or _contains_forbidden_update_inbox_token(item, forbidden_fields)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_forbidden_update_inbox_token(item, forbidden_fields) for item in value)
    return False

def _validate_update_inbox(
    update_inbox: UpdateInboxReport | None,
    claim: Claim,
    manifest: RunManifest,
    run_dir: Path,
    time_allocation: TimeAllocationPlan | None,
) -> None:
    if "update_inbox" in manifest.artifacts:
        raise KernelValidationError("update_inbox must be an optional scheduling artifact")
    if update_inbox is None:
        return
    if manifest.optional_artifacts.get("update_inbox") != "update_inbox.yaml":
        raise KernelValidationError("update_inbox optional artifact must be update_inbox.yaml")
    if update_inbox.claim_id != claim.id:
        raise KernelValidationError("update_inbox claim_id does not match locked claim")
    if update_inbox.run_id != manifest.run_id:
        raise KernelValidationError("update_inbox run_id does not match run manifest")

    artifact_path = run_dir / manifest.optional_artifacts["update_inbox"]
    raw = read_yaml(artifact_path)
    forbidden_fields = {
        "proof_status",
        "source_backed",
        "source_status",
        "checked_evidence",
        "example_checked",
        "example_survived",
        "example_failed",
        "counterexample_refutes_claim",
        "theorem_applicability_status",
        "grade_status",
        "verified",
        "proved",
        "complete_proof",
        "final_status",
        "memory_evidence",
    }
    if _contains_forbidden_update_inbox_token(raw, forbidden_fields):
        raise KernelValidationError("update_inbox cannot write mathematical status fields")

    for entry in update_inbox.entries:
        if entry.locked_claim_id != claim.id:
            raise KernelValidationError("update_inbox entry locked_claim_id does not match claim")
        if entry.effect != "scheduling_only" or entry.advisory_only is not True:
            raise KernelValidationError("update_inbox entries must be scheduling-only/advisory")
        if entry.dedupe_key != _canonical_update_inbox_dedupe_key(entry):
            raise KernelValidationError("update_inbox entry dedupe_key is not canonical")
        validate_relative_artifact_path(
            f"update_inbox.{entry.id}.origin_artifact",
            entry.origin_artifact,
            path_name="origin artifact",
        )
    _validate_update_inbox_applied_at_consumption(
        update_inbox,
        time_allocation,
        manifest,
    )
