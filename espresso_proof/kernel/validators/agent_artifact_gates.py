"""Theorem extraction/applicability loaders, LLM agent run, prover loop/rounds, proof goal graph, AI review, agent consensus, line-numbered proof."""

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
    artifact_revision_hash,
)
from espresso_proof.kernel.proof_dependencies import (
    trusted_human_allowed_theorem_refs,
    trusted_source_grounded_theorem_refs,
    unresolved_proof_dependency_lines,
)
from espresso_proof.validation.errors import KernelValidationError
from pathlib import Path
import re

from espresso_proof.kernel.validators.consistency_gates import _locator_tuple, _normalize_source_text
from espresso_proof.kernel.validators.prover_language import _prover_status_smuggling_field
from espresso_proof.kernel.validators.runtime_status import validate_yaml_model


def _load_optional_theorem_extraction(
    run_dir: Path,
    manifest: RunManifest,
) -> TheoremExtractionReport | None:
    optional = manifest.optional_artifacts
    if "theorem_extraction" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["theorem_extraction"],
        TheoremExtractionReport,
    )

def _load_optional_theorem_applicability(
    run_dir: Path,
    manifest: RunManifest,
) -> TheoremApplicabilityReport | None:
    optional = manifest.optional_artifacts
    if "theorem_applicability" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["theorem_applicability"],
        TheoremApplicabilityReport,
    )

def _load_optional_theorem_applicability_review(
    run_dir: Path,
    manifest: RunManifest,
    claim: Claim,
) -> TheoremApplicabilityReviewArtifact | None:
    optional = manifest.optional_artifacts
    if "theorem_applicability_review" not in optional:
        return None
    review = validate_yaml_model(
        run_dir / optional["theorem_applicability_review"],
        TheoremApplicabilityReviewArtifact,
    )
    if review.claim_id != claim.id:
        raise KernelValidationError("theorem_applicability_review claim_id does not match locked claim")
    if review.run_id != manifest.run_id:
        raise KernelValidationError("theorem_applicability_review run_id does not match manifest")
    return review

def _load_optional_theorem_condition_ledger(
    run_dir: Path,
    manifest: RunManifest,
) -> TheoremConditionLedger | None:
    optional = manifest.optional_artifacts
    if "theorem_condition_ledger" not in optional:
        return None
    # The ledger model (StrictBaseModel) carries no source/evidence/proof/grade
    # status field; validating here fails closed if one were ever smuggled in.
    return validate_yaml_model(
        run_dir / optional["theorem_condition_ledger"],
        TheoremConditionLedger,
    )

def _load_optional_llm_agent_run(
    run_dir: Path,
    manifest: RunManifest,
) -> LLMAgentRunReport | None:
    optional = manifest.optional_artifacts
    if "llm_agent_run" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["llm_agent_run"],
        LLMAgentRunReport,
    )

def _load_optional_prover_loop(
    run_dir: Path,
    manifest: RunManifest,
) -> ProverLoopReport | None:
    optional = manifest.optional_artifacts
    if "prover_loop" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["prover_loop"],
        ProverLoopReport,
    )

def _load_optional_proof_goal_graph(
    run_dir: Path,
    manifest: RunManifest,
) -> ProofGoalGraph | None:
    optional = manifest.optional_artifacts
    if "proof_goal_graph" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["proof_goal_graph"],
        ProofGoalGraph,
    )

def _load_optional_ai_review(
    run_dir: Path,
    manifest: RunManifest,
    claim: Claim,
) -> AIReviewAssessment | None:
    """Advisory "AI review passed" signal. Fails closed.

    ``AIReviewAssessment`` (StrictBaseModel) carries NO proof/source/evidence/
    grade/checked/trust/status field of any kind, so a malformed assessment — or
    one trying to smuggle such a field — is rejected here at load. The signal is
    advisory only: it never earns, sets, or upgrades any status. Light binding:
    claim_id / run_id must match the run.
    """

    optional = manifest.optional_artifacts
    if "ai_review" not in optional:
        return None
    assessment = validate_yaml_model(
        run_dir / optional["ai_review"],
        AIReviewAssessment,
    )
    if assessment.claim_id != claim.id:
        raise KernelValidationError(
            f"ai_review claim_id {assessment.claim_id!r} does not match the run "
            f"claim {claim.id!r}"
        )
    if assessment.run_id != manifest.run_id:
        raise KernelValidationError(
            f"ai_review run_id {assessment.run_id!r} does not match the run "
            f"{manifest.run_id!r}"
        )
    try:
        from espresso_proof.kernel.ai_review import assess_ai_review

        expected = assess_ai_review(run_dir)
    except Exception as exc:  # noqa: BLE001 - malformed dependencies must fail closed
        raise KernelValidationError(
            f"ai_review cannot be validated against current run artifacts: {exc}"
        ) from exc
    actual = assessment.model_dump(mode="json")
    actual["generated_at"] = ""
    if actual != expected:
        raise KernelValidationError(
            "ai_review does not match current reviewer/refutation/grade artifacts; "
            "rebuild ai_review.yaml before rendering the advisory signal"
        )
    return assessment

def _load_optional_agent_consensus_review(
    run_dir: Path,
    manifest: RunManifest,
    claim: Claim,
) -> AgentConsensusReview | None:
    """Agent-consensus mock-check record. Fails closed.

    ``AgentConsensusReview`` (StrictBaseModel) carries NO proof/source/evidence/
    grade/checked status FIELD, so a smuggled field name is rejected at load. But
    StrictBaseModel does not police forbidden TOKENS inside the free-text fields, so
    we additionally scan every reviewer-authored text field (which the UI renders)
    for a status smuggle. The model validator already enforces the three distinct
    reviewers + ``consensus`` == unanimous no-fatal-flaw. Light binding: claim_id /
    run_id must match the run; the heavy provenance gate (hash binding, reviewer
    cross-check, refuted/failed incompatibility) is ``_validate_agent_consensus_review``.
    """

    optional = manifest.optional_artifacts
    if "agent_consensus_review" not in optional:
        return None
    consensus = validate_yaml_model(
        run_dir / optional["agent_consensus_review"],
        AgentConsensusReview,
    )
    if consensus.claim_id != claim.id:
        raise KernelValidationError(
            f"agent_consensus_review claim_id {consensus.claim_id!r} does not match "
            f"the run claim {claim.id!r}"
        )
    if consensus.run_id != manifest.run_id:
        raise KernelValidationError(
            f"agent_consensus_review run_id {consensus.run_id!r} does not match the "
            f"run {manifest.run_id!r}"
        )
    # Free-text fields cannot smuggle a status (no reviewer prose may set
    # proof/source/evidence/grade status). The detector exempts honest
    # "not source_backed" disclaimers, so the record's own disclaimer is safe.
    for entry in consensus.entries:
        for text in (entry.looked_for, entry.finding, *entry.residual_objections):
            smuggle = _prover_status_smuggling_field(str(text))
            if smuggle:
                raise KernelValidationError(
                    f"agent_consensus_review entry ({entry.reviewer_id}) text smuggles a "
                    f"status field {smuggle!r}; reviewer prose cannot set proof status"
                )
    for text in (consensus.disclaimer, *consensus.notes):
        smuggle = _prover_status_smuggling_field(str(text))
        if smuggle:
            raise KernelValidationError(
                f"agent_consensus_review text smuggles a status field {smuggle!r}"
            )
    return consensus

def _load_optional_line_numbered_proof(
    run_dir: Path,
    manifest: RunManifest,
    claim: Claim,
) -> LineNumberedProof | None:
    """The structured line-numbered proof. Fails closed. Malformed / route-
    shaped YAML is rejected by ``LineNumberedProof`` (StrictBaseModel + the
    per-line validator). Light binding: claim_id must match the run."""
    optional = manifest.optional_artifacts
    if "line_numbered_proof" not in optional:
        return None
    proof = validate_yaml_model(
        run_dir / optional["line_numbered_proof"], LineNumberedProof
    )
    if proof.claim_id != claim.id:
        raise KernelValidationError(
            f"line_numbered_proof claim_id {proof.claim_id!r} does not match the run "
            f"claim {claim.id!r}"
        )
    return proof

def _validate_agent_consensus_review(
    consensus: AgentConsensusReview | None,
    review: Review,
    idea_grade: IdeaGrade,
    manifest: RunManifest,
    run_dir: Path,
    line_numbered_proof: LineNumberedProof | None = None,
) -> None:
    """Provenance gate for the agent-consensus mock-check (fail-closed).

    The mock check may ONLY seal a structured line-numbered proof. A route /
    proposed_steps list / advisory_proof_sketch is NOT sealable. The gate enforces:

    - a ``line_numbered_proof.yaml`` exists when the status is sealed (missing /
      malformed / route-only -> fail closed);
    - the consensus hash-binds claim + line_numbered_proof (+ source/review/prover_loop),
      so editing the proof after review revokes the seal (fail closed);
    - every PASS (no_fatal_flaw_found) reviewer cites real proof line_id(s) -- a PASS
      that references no line is not a review of the proof;
    - the three distinct proof reviewers reached a unanimous no-fatal-flaw consensus.

    The kernel never computes the agents' judgement (it is an explicitly agent-
    consensus, amber, NOT-machine-verified tier) -- it enforces that a structurally
    valid, current, unanimous review OF THE LINE-NUMBERED PROOF exists, and that no
    stronger/contradicting signal coexists.
    """
    status_uses_it = (
        idea_grade.proof_language_status
        == ProofLanguageStatus.agent_consensus_mock_checked
    )
    if consensus is None:
        if status_uses_it:
            raise KernelValidationError(
                "idea_grade.proof_language_status=agent_consensus_mock_checked requires "
                "an agent_consensus_review artifact"
            )
        return

    # 1. A line-numbered proof MUST exist for a consensus to mean anything.
    #    A route / proposed_steps / advisory sketch is not a proof and is not sealable.
    #    The seal helper also checks this upfront (fail-fast); THIS validator gate is
    #    the authoritative BACKSTOP that blocks a hand-forged consensus bypassing the
    #    seal helper (e.g. a manually written agent_consensus_review.yaml).
    if line_numbered_proof is None:
        raise KernelValidationError(
            "agent_consensus_review requires a line_numbered_proof.yaml; a route / "
            "proposed_steps / advisory_proof_sketch is not a sealable proof"
        )

    # 1b. No line may still carry an unresolved dependency. This rule used to live
    #     only in orchestration/agent_consensus_seal.py, so the sanctioned path
    #     enforced it and a hand-written agent_consensus_review.yaml did not —
    #     reproduced by a hostile review, which sealed a proof whose lines still
    #     carried gaps. A rule that decides a status has to be enforced where the
    #     kernel can see it, not in the component that writes the artifact.
    manifest_dict = manifest.model_dump() if hasattr(manifest, "model_dump") else dict(manifest)
    proof_artifact = (manifest_dict.get("optional_artifacts") or {}).get(
        "line_numbered_proof", "line_numbered_proof.yaml"
    )
    proof_dict = (
        line_numbered_proof.model_dump()
        if hasattr(line_numbered_proof, "model_dump")
        else dict(line_numbered_proof)
    )
    unresolved = unresolved_proof_dependency_lines(
        proof_dict,
        trusted_human_allowed_theorem_refs=trusted_human_allowed_theorem_refs(
            run_dir, manifest_dict, proof_artifact
        ),
        trusted_source_grounded_theorem_refs=trusted_source_grounded_theorem_refs(
            run_dir, manifest_dict
        ),
    )
    if unresolved:
        raise KernelValidationError(
            "agent_consensus_review cannot seal a line-numbered proof with "
            f"unresolved dependencies: {', '.join(unresolved)}"
        )

    # 2. Every PASS must cite real proof line_id(s) (rule 4): a no-fatal-flaw
    #    verdict that references no line is not a review of the line-numbered proof.
    proof_line_ids = {line.line_id for line in line_numbered_proof.lines}
    for entry in consensus.entries:
        if entry.verdict == AgentConsensusVerdict.no_fatal_flaw_found:
            cited = [lid for lid in entry.cited_line_ids if lid]
            unknown = [lid for lid in cited if lid not in proof_line_ids]
            if not cited:
                raise KernelValidationError(
                    f"reviewer {entry.reviewer_id} returned no_fatal_flaw_found but did "
                    "not cite any line_id of the line-numbered proof"
                )
            if unknown:
                raise KernelValidationError(
                    f"reviewer {entry.reviewer_id} cited unknown line_id(s) in the "
                    f"line-numbered proof: {', '.join(sorted(set(unknown)))}"
                )

    # 3. A line_proof reviewer that FAILED the standard kernel review cannot claim
    #    no_fatal_flaw here (the one role that overlaps the front-half review set).
    failed = {
        reviewer.reviewer_id
        for reviewer in review.reviewers
        if reviewer.verdict == ReviewerVerdict.failed
    }
    for entry in consensus.entries:
        if (
            entry.reviewer_id in failed
            and entry.verdict == AgentConsensusVerdict.no_fatal_flaw_found
        ):
            raise KernelValidationError(
                f"reviewer {entry.reviewer_id} failed the review but claims "
                "no_fatal_flaw_found in the agent consensus"
            )

    # 4. Hash binding (revoke on change): the bound_* hashes must equal the CURRENT
    #    artifact hashes -- crucially the line-numbered proof, so editing the proof
    #    after review fails the seal closed.
    artifacts = manifest.artifacts
    optional = manifest.optional_artifacts
    prover_loop_rel = optional.get("prover_loop")
    proof_rel = optional.get("line_numbered_proof")
    expected = {
        "bound_claim_hash": artifact_revision_hash(run_dir, artifacts["locked_claim"]),
        "bound_line_numbered_proof_hash": (
            artifact_revision_hash(run_dir, proof_rel) if proof_rel else ""
        ),
        "bound_source_grounding_hash": artifact_revision_hash(
            run_dir, artifacts["source_grounding"]
        ),
        "bound_review_hash": artifact_revision_hash(run_dir, artifacts["review"]),
        "bound_prover_loop_hash": (
            artifact_revision_hash(run_dir, prover_loop_rel) if prover_loop_rel else ""
        ),
    }
    for field_name, expected_hash in expected.items():
        if getattr(consensus, field_name) != expected_hash:
            raise KernelValidationError(
                f"agent_consensus_review.{field_name} does not match the current run "
                "artifacts; the consensus is stale and must be re-sealed"
            )

    if not status_uses_it:
        return

    # 5. The status path requires a unanimous consensus (belt; model enforces the
    #    consensus<->entries consistency).
    if not consensus.consensus:
        raise KernelValidationError(
            "agent_consensus_mock_checked requires a unanimous no_fatal_flaw_found "
            "consensus from all three proof reviewers"
        )

    # 6. A refuted claim can never carry a mock-checked proof.
    if idea_grade.status == IdeaStatus.refuted:
        raise KernelValidationError(
            "a refuted claim cannot carry "
            "proof_language_status=agent_consensus_mock_checked"
        )

def _validate_subgoal_graph(
    proof_goal_graph: ProofGoalGraph | None,
    claim: Claim,
    manifest: RunManifest,
    source_grounding: SourceGroundingReport,
    proof_verification: ProofVerificationReport | None,
    computation_verification: ComputationVerificationReport | None,
    idea_grade: IdeaGrade,
    strategy_brief: StrategyBrief,
    theorem_condition_ledger: TheoremConditionLedger | None = None,
) -> None:
    """Subgoal-graph proof-search state (advisory). Fails closed.

    The graph is a SEARCH-MANAGEMENT artifact, never proof evidence. This
    validator enforces that it cannot smuggle trusted status:

    - claim_id / run_id bind to this run;
    - a ``source_supported`` node (or any ``supported_by_source_steps`` ref) must
      point at a source-grounding step whose ``source_backed`` status was earned
      by the existing grounding gate -- prose / reviewer / human / memory can
      never earn it here;
    - a ``scoped_checked`` node (or any ``supported_by_computations`` /
      ``supported_by_scoped_checks`` ref) must point at a deterministic, trusted,
      confirming proof-verification or computation-verification entry;
    - a ``contradicted`` node is allowed only when the kernel's own refutation
      logic already refuted the claim (``idea_grade.status == refuted``), so a
      reviewer objection / dropped-hypothesis counterexample cannot close a
      subgoal as refuted here;
    - the model already forbids proof-complete language and a missing main goal.

    The graph has no status field that feeds back into the run, so its presence
    can never change idea_grade / proof_language / source / evidence / claim
    status (the StrictBaseModel forbids such fields by construction).
    """
    if proof_goal_graph is None:
        return
    if proof_goal_graph.claim_id != claim.id:
        raise KernelValidationError("proof_goal_graph claim_id does not match locked claim")
    if proof_goal_graph.run_id != manifest.run_id:
        raise KernelValidationError("proof_goal_graph run_id does not match the run manifest")

    known_strategy_ids = {strategy.id for strategy in strategy_brief.top_strategies}
    known_strategy_ids.add("none")
    selected = proof_goal_graph.selected_strategy
    if selected is not None and selected not in known_strategy_ids:
        raise KernelValidationError(
            f"proof_goal_graph selected_strategy {selected!r} is not a known strategy"
        )

    earned_source_steps = {
        step.proof_step_id
        for step in source_grounding.steps
        if step.status == SourceStatus.source_backed
    }
    earned_computations: set[str] = set()
    if proof_verification is not None:
        for entry in proof_verification.entries:
            if (
                entry.trusted
                and entry.confirmed
                and entry.earned_status
                in {
                    ProofVerificationStatus.instance_checked,
                    ProofVerificationStatus.computation_checked,
                }
            ):
                earned_computations.add(entry.id)
    confirmed_scoped_checks: set[str] = set()
    if computation_verification is not None:
        for result in computation_verification.results:
            if result.executed and result.verdict.value == "confirmed":
                confirmed_scoped_checks.add(result.check_id)

    claim_refuted = idea_grade.status == IdeaStatus.refuted

    for node in proof_goal_graph.nodes:
        for step_id in node.supported_by_source_steps:
            if step_id not in earned_source_steps:
                raise KernelValidationError(
                    f"{node.id}: supported_by_source_steps references {step_id!r} which is not an "
                    "earned source_backed grounding step"
                )
        for comp_id in node.supported_by_computations:
            if comp_id not in earned_computations:
                raise KernelValidationError(
                    f"{node.id}: supported_by_computations references {comp_id!r} which is not a "
                    "trusted confirming proof-verification entry"
                )
        for check_id in node.supported_by_scoped_checks:
            if check_id not in confirmed_scoped_checks:
                raise KernelValidationError(
                    f"{node.id}: supported_by_scoped_checks references {check_id!r} which is not a "
                    "confirmed deterministic computation-verification result"
                )
        if node.state == SubgoalState.source_supported and not node.supported_by_source_steps:
            raise KernelValidationError(
                f"{node.id}: source_supported requires an earned source-grounding step reference"
            )
        if node.state == SubgoalState.scoped_checked and not (
            node.supported_by_computations or node.supported_by_scoped_checks
        ):
            raise KernelValidationError(
                f"{node.id}: scoped_checked requires a deterministic verification reference"
            )
        if node.state == SubgoalState.contradicted and not claim_refuted:
            raise KernelValidationError(
                f"{node.id}: contradicted requires the claim to be refuted by the kernel "
                "(idea_grade.status=refuted); a reviewer objection or dropped-hypothesis "
                "counterexample cannot close a subgoal as refuted"
            )

    # Cap-aware ledger/graph id consistency. The theorem-condition ledger
    # is the SINGLE WRITER of ``subgoal_tcl_*`` repair nodes (their ids come from a
    # ledger entry's ``repair_subgoal_id``). When BOTH artifacts exist, every such
    # node in the graph MUST correspond to a real ledger entry — this catches a
    # hallucinated / stale ledger repair node smuggled into the graph.
    #
    # CAP-SAFE (does NOT break under _MAX_LEDGER_SUBGOALS): the builder caps how
    # many ledger entries become graph nodes, so the graph only ever holds a SUBSET
    # of the ledger's repair ids — never an extra one. We therefore check
    # "every graph tcl node ⊆ ledger ids", NOT "every ledger entry has a node"
    # (the latter would falsely fail a capped graph). The advisory
    # ``target_subgoal_id`` is a different, unverified field and is untouched here.
    if theorem_condition_ledger is not None:
        ledger_repair_ids = {
            entry.repair_subgoal_id for entry in theorem_condition_ledger.entries
        }
        for node in proof_goal_graph.nodes:
            if node.id.startswith("subgoal_tcl_") and node.id not in ledger_repair_ids:
                raise KernelValidationError(
                    f"{node.id}: a subgoal_tcl_* repair node has no matching "
                    "theorem_condition_ledger entry (hallucinated or stale ledger "
                    "repair node)"
                )

def _validate_applicability_usage_source_binding(
    usage,
    source_ref,
    theorem_extraction: TheoremExtractionReport | None,
) -> None:
    """Bind step-local theorem applicability to the cited checked source.

    A source-backed applicability usage may only discuss the exact statement and
    locator that the source-grounding artifact already checked. When extraction
    is present, that pair must also name a real extracted theorem and source.
    """

    if _normalize_source_text(usage.theorem_statement) != _normalize_source_text(
        source_ref.statement_or_summary
    ):
        raise KernelValidationError(
            f"{usage.proof_step_id}: theorem_applicability theorem_statement does not "
            "match the cited source_ref statement"
        )
    if _locator_tuple(usage.locator) != _locator_tuple(source_ref.locator):
        raise KernelValidationError(
            f"{usage.proof_step_id}: theorem_applicability locator does not match "
            "the cited source_ref locator"
        )
    if theorem_extraction is None:
        return

    extraction_sources = {source.id: source for source in theorem_extraction.sources}
    matching_theorems = [
        theorem
        for theorem in theorem_extraction.theorems
        if _normalize_source_text(theorem.statement) == _normalize_source_text(usage.theorem_statement)
        and _locator_tuple(theorem.locator) == _locator_tuple(usage.locator)
    ]
    if not matching_theorems:
        raise KernelValidationError(
            f"{usage.proof_step_id}: theorem_applicability statement/locator does not "
            "match any extracted theorem"
        )
    if usage.theorem_id is not None and usage.theorem_id not in {
        theorem.id for theorem in matching_theorems
    }:
        raise KernelValidationError(
            f"{usage.proof_step_id}: theorem_applicability theorem_id does not match "
            "the extracted theorem"
        )
    source_matches = False
    for theorem in matching_theorems:
        extracted_source = extraction_sources.get(theorem.source_ref_id)
        if extracted_source is None:
            continue
        if (
            extracted_source.title == source_ref.title
            and extracted_source.authors == source_ref.authors
        ):
            source_matches = True
            break
    if not source_matches:
        raise KernelValidationError(
            f"{usage.proof_step_id}: theorem_applicability extracted theorem source "
            "does not match the cited source_ref"
        )

def _validate_theorem_applicability(
    manifest: RunManifest,
    claim: Claim,
    source_grounding: SourceGroundingReport,
    theorem_applicability: TheoremApplicabilityReport | None,
    theorem_extraction: TheoremExtractionReport | None,
) -> None:
    has_source_backed_step = any(
        step.status == SourceStatus.source_backed for step in source_grounding.steps
    )
    if theorem_applicability is None:
        if has_source_backed_step:
            raise KernelValidationError(
                "source_backed theorem steps require theorem_applicability audit"
            )
        return
    if theorem_applicability.claim_id != claim.id:
        raise KernelValidationError("theorem_applicability claim_id does not match locked claim")
    if theorem_applicability.run_id != manifest.run_id:
        raise KernelValidationError("theorem_applicability run_id does not match manifest")
    if theorem_applicability.source_grounding_artifact != manifest.artifacts["source_grounding"]:
        raise KernelValidationError("theorem_applicability must cite the source_grounding artifact")
    if theorem_applicability.literature_review_agent_artifact is not None:
        expected = manifest.optional_artifacts.get("literature_review_agent")
        if theorem_applicability.literature_review_agent_artifact != expected:
            raise KernelValidationError(
                "theorem_applicability literature_review_agent_artifact does not match manifest"
            )
    known_steps = {step.proof_step_id: step for step in source_grounding.steps}
    refs_by_id = {ref.id: ref for ref in source_grounding.source_refs}
    known_refs = set(refs_by_id)
    usages_by_step = {usage.proof_step_id: usage for usage in theorem_applicability.usages}
    for usage in theorem_applicability.usages:
        if usage.proof_step_id not in known_steps:
            raise KernelValidationError(
                f"theorem_applicability references unknown proof step {usage.proof_step_id!r}"
            )
        if usage.source_ref_id not in known_refs and usage.source_ref_id != "missing_source_ref":
            raise KernelValidationError(
                f"theorem_applicability references unknown source ref {usage.source_ref_id!r}"
            )
        step = known_steps[usage.proof_step_id]
        if (
            usage.source_ref_id == "missing_source_ref"
            and (usage.source_status == SourceStatus.source_backed or step.status == SourceStatus.source_backed)
        ):
            raise KernelValidationError(
                f"{usage.proof_step_id}: source_backed theorem applicability requires "
                "a concrete cited source_ref_id"
            )
        if usage.source_ref_id != "missing_source_ref" and usage.source_ref_id not in step.source_refs:
            raise KernelValidationError(
                f"{usage.proof_step_id}: theorem_applicability source_ref_id is not used by proof step"
            )
        if usage.source_status != step.status:
            raise KernelValidationError(
                f"{usage.proof_step_id}: theorem_applicability source_status does not match source_grounding step"
            )
        if usage.source_ref_id in refs_by_id and refs_by_id[usage.source_ref_id].status != usage.source_status:
            raise KernelValidationError(
                f"{usage.proof_step_id}: theorem_applicability source_status does not match source ref"
            )
        if usage.source_status == SourceStatus.source_backed and usage.source_ref_id in refs_by_id:
            _validate_applicability_usage_source_binding(
                usage, refs_by_id[usage.source_ref_id], theorem_extraction
            )
    for step in source_grounding.steps:
        if step.status != SourceStatus.source_backed:
            continue
        usage = usages_by_step.get(step.proof_step_id)
        if usage is None:
            raise KernelValidationError(
                f"{step.proof_step_id}: source_backed theorem step lacks applicability audit"
            )
        if usage.applicability_status != "applicable" or usage.blocks_source_backed:
            raise KernelValidationError(
                f"{step.proof_step_id}: source_backed theorem step is blocked by theorem applicability"
            )
        if any(condition.status != "satisfied" for condition in usage.conditions):
            raise KernelValidationError(
                f"{step.proof_step_id}: source_backed theorem step has unsatisfied theorem conditions"
            )
        if not usage.locator.is_exact:
            raise KernelValidationError(
                f"{step.proof_step_id}: source_backed theorem step requires an exact applicability locator"
            )
        if (
            usage.theorem_scope in {"very_general", "generic"}
            and usage.claim_scope == "universal"
            and not usage.scope_justification.strip()
        ):
            raise KernelValidationError(
                f"{step.proof_step_id}: a '{usage.theorem_scope}' theorem cannot back a universal "
                f"('all') claim without an explicit scope_justification"
            )
