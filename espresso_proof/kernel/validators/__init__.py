"""Run validators (kernel trust gate), grouped by artifact family.

Public API unchanged: validate_run, validate_yaml_model (+ every internal
_validate_*/_load_optional_* helper, AND the names validators.py imported
and re-exported such as KernelValidationError) are available here, so
`from espresso_proof.kernel.validators import <name>` keeps working."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ValidationError

from espresso_proof.kernel.io import path_exists, path_is_file, read_text, read_yaml
from espresso_proof.kernel.research_bank import (
    load_counterexamples_artifact,
    load_examples_artifact,
    load_strategy_synthesis_artifact,
)
from espresso_proof.kernel.models import (
    AgentConsensusReview,
    AgentConsensusVerdict,
    AgentContracts,
    AgentRunStatusReport,
    AIReviewAssessment,
    AttemptStatus,
    AutonomousLiteratureRetrievalReport,
    Claim,
    ComputationChecks,
    ComputationVerificationReport,
    CounterexampleReport,
    ExternalToolCandidatesReport,
    ExternalToolRunsReport,
    FailureClassificationReport,
    FailurePatternAudit,
    FiniteObjectWitnessReport,
    LiteratureCandidates,
    MemoryContext,
    RetrievalResolutionReport,
    LiteratureRetrievalReport,
    LiteratureSearchPlan,
    MathematicalJudgmentReport,
    ProofGoalGraph,
    _has_run_artifact_provenance,
    ProofVerificationReport,
    ProofVerificationStatus,
    PressureLaneBarrierReport,
    ProofGapResolutionReport,
    SubgoalState,
    ContinueDecision,
    ExampleSelection,
    ExampleFeedbackSummary,
    HypothesisLedger,
    HumanChoice,
    HumanExampleFeedback,
    HumanChoiceStatus,
    ExampleStatus,
    IdeaGrade,
    IdeaStatus,
    LineNumberedProof,
    LLMAgentRunReport,
    LiteratureReviewAgentReport,
    LiteratureReviewReport,
    LoopControl,
    ProofLanguageStatus,
    ProverLoopReport,
    ProverRoundIndex,
    ProverRoundReport,
    PressureStatusReport,
    Review,
    VerificationVerdict,
    ReviewerVerdict,
    RunManifest,
    SourceGroundingReport,
    SourceStatus,
    StageStatusReport,
    StrategyBrief,
    StrategySynthesis,
    TestReport,
    TheoremApplicabilityReport,
    TheoremApplicabilityReviewArtifact,
    TheoremConditionLedger,
    TheoremExtractionReport,
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
from espresso_proof.kernel.proof_language import contains_proof_language
from espresso_proof.kernel.strategy_rules import validate_strategy_rules
from espresso_proof.kernel.surface_warnings import (
    collect_surface_warnings,
    final_report_missing_disclosures,
)
from espresso_proof.kernel.agent_flow_spec import (
    get_stage,
    ordered_stage_ids,
    stages_blocked_by_unclear_claim,
)
from espresso_proof.kernel.agent_flow_legacy_map import get_legacy_mapping
from espresso_proof.kernel.pressure_lane_barrier import (
    PRESSURE_LANE_BARRIER_AGENT_ROLES,
    PRESSURE_LANE_BARRIER_AGENT_STAGE_IDS,
    PRESSURE_LANE_BARRIER_FILENAME,
    PRESSURE_LANE_BARRIER_MODE,
    order_for_pressure_lane_barrier,
)
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validation.front_half import validate_front_half_run
from espresso_proof.validation.evidence import (
    _validate_computation_verification,
    _validate_example_evidence,
    _validate_test_report_evidence,
)
from espresso_proof.validation.manifest import ManifestPathValidationError
from espresso_proof.validation.manifest import validate_relative_artifact_path
from espresso_proof.validation.manifest import validate_manifest_paths

from espresso_proof.kernel.validators import language_gate as _m_language_gate
globals().update({k: v for k, v in vars(_m_language_gate).items() if not k.startswith('__')})
from espresso_proof.kernel.validators import runtime_status as _m_runtime_status
globals().update({k: v for k, v in vars(_m_runtime_status).items() if not k.startswith('__')})
from espresso_proof.kernel.validators import scheduling as _m_scheduling
globals().update({k: v for k, v in vars(_m_scheduling).items() if not k.startswith('__')})
from espresso_proof.kernel.validators import evidence_gates as _m_evidence_gates
globals().update({k: v for k, v in vars(_m_evidence_gates).items() if not k.startswith('__')})
from espresso_proof.kernel.validators import literature_gates as _m_literature_gates
globals().update({k: v for k, v in vars(_m_literature_gates).items() if not k.startswith('__')})
from espresso_proof.kernel.validators import machine_evidence_gates as _m_machine_evidence_gates
globals().update({k: v for k, v in vars(_m_machine_evidence_gates).items() if not k.startswith('__')})
from espresso_proof.kernel.validators import prover_language as _m_prover_language
globals().update({k: v for k, v in vars(_m_prover_language).items() if not k.startswith('__')})
from espresso_proof.kernel.validators import consistency_gates as _m_consistency_gates
globals().update({k: v for k, v in vars(_m_consistency_gates).items() if not k.startswith('__')})
from espresso_proof.kernel.validators import agent_artifact_gates as _m_agent_artifact_gates
globals().update({k: v for k, v in vars(_m_agent_artifact_gates).items() if not k.startswith('__')})
from espresso_proof.kernel.validators import final_gates as _m_final_gates
globals().update({k: v for k, v in vars(_m_final_gates).items() if not k.startswith('__')})
