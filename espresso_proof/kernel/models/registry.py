"""Name -> model class registry (schema export surface)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import BaseModel, Field, model_validator

from espresso_proof.kernel.models.foundations import Claim, HypothesisLedger, SourceRef
from espresso_proof.kernel.models.evidence import ComputationChecks, ComputationVerificationReport, ComputationVerificationResult, CounterexampleReport, ExampleBank, ExampleComputationCheck, ExampleSelection, FailureClassificationReport, FailurePatternAudit, FailurePatternBank, MachineCheckSpec, MathematicalJudgmentReport, TestReport
from espresso_proof.kernel.models.literature import AutonomousLiteratureRetrievalReport, ExtractedTheorem, LiteratureCandidate, LiteratureCandidates, LiteratureRetrievalReport, LiteratureReviewAgentReport, LiteratureReviewReport, LiteratureSearchPlan, RetrievalResolutionReport, TheoremExtractionReport
from espresso_proof.kernel.models.machine_verification import ExternalToolCandidatesReport, ExternalToolRunsReport, FiniteObjectWitnessReport, ProofVerificationReport, ScopedVerificationRequest, ScopedVerificationRequests
from espresso_proof.kernel.models.source_grounding import SourceGroundingReport, TheoremApplicabilityReport, TheoremApplicabilityReviewArtifact, TheoremConditionLedger
from espresso_proof.kernel.models.review_strategy import HumanTimeAllocationContext, IdeaGrade, Review, StrategyBrief, StrategySynthesis, TimeAllocationPlan, UpdateInboxReport
from espresso_proof.kernel.models.human_interaction import AdmissionCheck, AgentContracts, ExampleFeedbackSummary, HumanChoice, HumanExampleFeedback, LoopControl
from espresso_proof.kernel.models.agent_reports import AIReviewAssessment, LLMAgentRunReport, ProverLoopReport, ProverRoundIndex, ProverRoundReport
from espresso_proof.kernel.models.subgoals import ProofGoalGraph
from espresso_proof.kernel.models.line_proof import AgentConsensusReview, LineNumberedProof, ProofGapResolutionReport
from espresso_proof.kernel.models.run_lifecycle import AgentRunStatusReport, FrontHalfRunReport, MemoryContext, MemoryEntry, MemoryIndex, PressureLaneBarrierReport, PressureStatusReport, RunManifest, StageStatusReport, WorkflowSpec


MODEL_REGISTRY: dict[str, type[BaseModel]] = {
    "claim": Claim,
    "ai_review": AIReviewAssessment,
    "agent_consensus_review": AgentConsensusReview,
    "line_numbered_proof": LineNumberedProof,
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
    "finite_object_witness": FiniteObjectWitnessReport,
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
    "theorem_applicability_review": TheoremApplicabilityReviewArtifact,
    "review": Review,
    "idea_grade": IdeaGrade,
    "loop_control": LoopControl,
    "strategy_synthesis": StrategySynthesis,
    "strategy_brief": StrategyBrief,
    "time_allocation": TimeAllocationPlan,
    "update_inbox": UpdateInboxReport,
    "human_choice": HumanChoice,
    "human_example_feedback": HumanExampleFeedback,
    "example_feedback_summary": ExampleFeedbackSummary,
    "llm_agent_run": LLMAgentRunReport,
    "prover_loop": ProverLoopReport,
    "prover_round": ProverRoundReport,
    "prover_round_index": ProverRoundIndex,
    "proof_gap_resolution": ProofGapResolutionReport,
    "proof_goal_graph": ProofGoalGraph,
    "theorem_condition_ledger": TheoremConditionLedger,
    "human_time_allocation_context": HumanTimeAllocationContext,
    "front_half_run": FrontHalfRunReport,
    "stage_status": StageStatusReport,
    "pressure_lane_barrier": PressureLaneBarrierReport,
    "agent_run_status": AgentRunStatusReport,
    "pressure_status": PressureStatusReport,
    "memory_entry": MemoryEntry,
    "memory_index": MemoryIndex,
    "memory_context": MemoryContext,
    "run_manifest": RunManifest,
    "workflow_spec": WorkflowSpec,
    "source_ref": SourceRef,
    "extracted_theorem": ExtractedTheorem,
    "example_computation_check": ExampleComputationCheck,
}


__all__ = [
    "MODEL_REGISTRY",
]
