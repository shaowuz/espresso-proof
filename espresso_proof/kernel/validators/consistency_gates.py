"""Claim-id consistency, contracts, source discipline/backing, applicability, review outcome, loop control, strategy artifacts, human choice."""

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
from espresso_proof.kernel.strategy_rules import validate_strategy_rules
from espresso_proof.validation.errors import KernelValidationError
from pathlib import Path
import re


def _validate_claim_id_consistency(
    claim: Claim,
    human_choice: HumanChoice,
    hypothesis_ledger: HypothesisLedger,
    literature_review: LiteratureReviewReport,
    examples: ExampleSelection,
    computations: ComputationChecks,
    counterexamples: CounterexampleReport,
    tests: TestReport,
    source_grounding: SourceGroundingReport,
    review: Review,
    idea_grade: IdeaGrade,
    loop_control: LoopControl,
    strategy_synthesis: StrategySynthesis,
    strategy_brief: StrategyBrief,
) -> None:
    expected = claim.id
    artifact_ids = {
        "hypothesis_ledger": hypothesis_ledger.claim_id,
        "literature_review": literature_review.claim_id,
        "examples": examples.claim_id,
        "computation_checks": computations.claim_id,
        "counterexamples": counterexamples.claim_id,
        "tests": tests.claim_id,
        "source_grounding": source_grounding.claim_id,
        "review": review.claim_id,
        "idea_grade": idea_grade.claim_id,
        "loop_control": loop_control.claim_id,
        "strategy_synthesis": strategy_synthesis.claim_id,
        "strategy_brief": strategy_brief.claim_id,
        "human_choice": human_choice.claim_id,
    }
    mismatches = {name: claim_id for name, claim_id in artifact_ids.items() if claim_id != expected}
    if mismatches:
        raise KernelValidationError(f"artifact claim_id mismatch: {mismatches}")

def _validate_agent_contracts(agent_contracts: AgentContracts, artifacts: dict[str, str]) -> None:
    output_artifacts = {contract.output_artifact for contract in agent_contracts.contracts}
    required_outputs = {
        "locked_claim.yaml",
        "hypothesis_ledger.yaml",
        "literature_review.yaml",
        "computation_checks.yaml",
        "tests.yaml",
        "source_grounding.yaml",
        "review.yaml",
        "idea_grade.yaml",
        "loop_control.yaml",
        "strategy_brief.yaml",
        "human_choice.yaml",
        "final_report.md",
    }
    # The three bankable artifacts may be a single YAML file OR a per-item bank
    # directory; require whatever path the manifest actually uses (so agent
    # contracts and the manifest stay consistent in either layout).
    for key, default in (
        ("examples", "examples.yaml"),
        ("counterexamples", "counterexamples.yaml"),
        ("strategy_synthesis", "strategy_synthesis.yaml"),
    ):
        required_outputs.add(artifacts.get(key, default))
    missing_outputs = required_outputs.difference(output_artifacts)
    if missing_outputs:
        raise KernelValidationError(f"agent_contracts missing outputs {sorted(missing_outputs)}")
    artifact_values = set(artifacts.values())
    unknown_outputs = {
        output
        for output in output_artifacts.difference(artifact_values)
        if not output.startswith("memory/")
    }
    if unknown_outputs:
        raise KernelValidationError(f"agent_contracts reference non-run artifacts {sorted(unknown_outputs)}")

def _validate_source_discipline_bindings(
    run_dir: Path,
    manifest: RunManifest,
    source_grounding: SourceGroundingReport,
    theorem_extraction: TheoremExtractionReport | None,
    theorem_applicability: TheoremApplicabilityReport | None,
) -> None:
    """Provenance bindings (optional/additive — both fields default "").

    1. A source ref carrying ``statement_sha256`` must quote EXACTLY the bytes it
       hashed (self-hash), and when ``theorem_extraction.yaml`` is present the
       hash must name a really-extracted theorem — editing the cited statement
       after grounding fails closed.
    2. A theorem_applicability report carrying ``source_grounding_sha256`` must
       match the CURRENT grounding file — editing the grounding after the audit
       (without re-running it) fails closed instead of leaving a stale
       "applicable" verdict standing.
    """

    import hashlib

    extracted_hashes: set[str] | None = (
        {
            hashlib.sha256(theorem.statement.encode("utf-8")).hexdigest()
            for theorem in theorem_extraction.theorems
        }
        if theorem_extraction is not None
        else None
    )
    for ref in source_grounding.source_refs:
        if not ref.statement_sha256:
            # Optional-and-additive is over for the status that matters: a
            # source_backed ref with no excerpt hash was exempt from both
            # binding checks below, so a fabricated citation could resolve
            # theorem_ref dependencies on the amber-seal path by simply not
            # recording a hash. The grounding pipeline always writes one
            # (source_verification.py), so a source_backed ref without it did
            # not come from the sanctioned path.
            if ref.status == SourceStatus.source_backed:
                raise KernelValidationError(
                    f"{ref.id}: a source_backed reference must record its excerpt "
                    "hash (statement_sha256); a citation the kernel cannot bind to "
                    "quoted bytes cannot support a step"
                )
            continue
        actual = hashlib.sha256(ref.statement_or_summary.encode("utf-8")).hexdigest()
        if actual != ref.statement_sha256:
            raise KernelValidationError(
                f"{ref.id}: cited statement does not match its recorded excerpt "
                "hash (the statement was edited after grounding; re-run grounding)"
            )
        if extracted_hashes is not None and ref.statement_sha256 not in extracted_hashes:
            raise KernelValidationError(
                f"{ref.id}: excerpt hash does not name any extracted theorem in "
                "theorem_extraction.yaml (fabricated or stale citation; re-run "
                "theorem extraction and grounding)"
            )

    if theorem_applicability is not None and theorem_applicability.source_grounding_sha256:
        grounding_rel = manifest.artifacts.get("source_grounding")
        if grounding_rel:
            current = hashlib.sha256((run_dir / grounding_rel).read_bytes()).hexdigest()
            if current != theorem_applicability.source_grounding_sha256:
                raise KernelValidationError(
                    "theorem_applicability.yaml is stale: source_grounding.yaml "
                    "changed after the applicability audit; re-run the audit"
                )

def _validate_source_backing(source_grounding: SourceGroundingReport) -> None:
    refs = {ref.id: ref for ref in source_grounding.source_refs}
    for ref in refs.values():
        if ref.status == SourceStatus.misapplied:
            if ref.hypothesis_match.matches is not False:
                raise KernelValidationError(
                    f"{ref.id}: misapplied source requires a concrete hypothesis mismatch"
                )
            if not ref.hypothesis_match.mismatches:
                raise KernelValidationError(
                    f"{ref.id}: misapplied source must list mismatched hypotheses"
                )
    for step in source_grounding.steps:
        if step.status == SourceStatus.misapplied:
            if step.hypothesis_match.matches is not False:
                raise KernelValidationError(
                    f"{step.proof_step_id}: misapplied step requires a concrete hypothesis mismatch"
                )
            if not step.hypothesis_match.mismatches:
                raise KernelValidationError(
                    f"{step.proof_step_id}: misapplied step must list mismatched hypotheses"
                )
        if step.status == SourceStatus.source_backed:
            if step.hypothesis_match.matches is not True:
                raise KernelValidationError(
                    f"{step.proof_step_id}: source_backed step has non-matching hypotheses"
                )
            if not step.source_refs:
                raise KernelValidationError(f"{step.proof_step_id}: source_backed step has no source refs")
            for ref_id in step.source_refs:
                ref = refs.get(ref_id)
                if ref is None:
                    raise KernelValidationError(f"{step.proof_step_id}: unknown source ref {ref_id}")
                if ref.status != SourceStatus.source_backed:
                    raise KernelValidationError(
                        f"{step.proof_step_id}: source ref {ref_id} is not source_backed"
                    )

def _normalize_source_text(text: str) -> str:
    return " ".join(str(text).split())

def _locator_tuple(locator) -> tuple[str, str]:
    kind = locator.kind.value if hasattr(locator.kind, "value") else str(locator.kind)
    return kind, str(locator.value).strip()

def _validate_review_outcome(review: Review, idea_grade: IdeaGrade) -> None:
    failed_reviewers = [
        reviewer.reviewer_id
        for reviewer in review.reviewers
        if reviewer.verdict == ReviewerVerdict.failed
    ]
    if failed_reviewers:
        if review.attempt_status != AttemptStatus.failed:
            raise KernelValidationError(
                f"failed reviewers require attempt_status=failed: {failed_reviewers}"
            )
        if idea_grade.status == IdeaStatus.promising:
            raise KernelValidationError(
                f"failed reviewers cannot produce promising grade: {failed_reviewers}"
            )
        strong_statuses = {
            # A failed reviewer also blocks the agent-consensus mock-check: a
            # unanimous no-fatal-flaw is impossible if a reviewer failed the run.
            ProofLanguageStatus.agent_consensus_mock_checked,
            ProofLanguageStatus.source_checked_argument,
            ProofLanguageStatus.computation_checked,
            ProofLanguageStatus.lean_checked,
            ProofLanguageStatus.human_confirmed,
        }
        if idea_grade.proof_language_status in strong_statuses:
            raise KernelValidationError(
                f"failed reviewers cannot support proof_language_status={idea_grade.proof_language_status.value}"
            )

def _validate_loop_control(loop_control: LoopControl) -> None:
    no_progress = (
        loop_control.no_new_counterexamples_cycles >= 2
        and loop_control.no_new_references_cycles >= 2
        and loop_control.grade_delta_last_two <= 0
    )
    if no_progress and not loop_control.should_stop:
        raise KernelValidationError("two no-progress cycles require Strategy Brief or human stop")
    if loop_control.should_stop and loop_control.next_action == "continue_loop":
        raise KernelValidationError("loop_control cannot stop and continue at the same time")
    if loop_control.next_action == "stop" and not loop_control.should_stop:
        raise KernelValidationError("loop_control next_action=stop requires should_stop=true")
    if loop_control.next_action == "stop" and not loop_control.stop_reasons:
        raise KernelValidationError("loop_control next_action=stop requires stop_reasons")

def _validate_strategy_routes(
    routes: list,
    idea_grade: IdeaGrade,
    counterexamples: CounterexampleReport,
    review: Review | None = None,
    computations: ComputationChecks | None = None,
    tests: TestReport | None = None,
) -> None:
    known_tests = {test.id for test in counterexamples.tests}
    for strategy in routes:
        unknown_tests = set(strategy.survived_tests + strategy.failed_tests).difference(known_tests)
        if unknown_tests:
            raise KernelValidationError(
                f"{strategy.id}: strategy references unknown tests {sorted(unknown_tests)}"
            )
    validate_strategy_rules(
        routes,
        idea_grade,
        review=review,
        computations=computations,
        counterexamples=counterexamples,
        tests=tests,
        error_type=KernelValidationError,
    )

def _validate_strategy_synthesis(
    strategy_synthesis: StrategySynthesis,
    idea_grade: IdeaGrade,
    counterexamples: CounterexampleReport,
    review: Review,
    computations: ComputationChecks,
    tests: TestReport,
) -> None:
    if not strategy_synthesis.routes:
        raise KernelValidationError("strategy_synthesis requires at least one route")
    required_inputs = {
        "literature_review.yaml",
        "tests.yaml",
        "counterexamples.yaml",
        "source_grounding.yaml",
        "review.yaml",
        "idea_grade.yaml",
        "loop_control.yaml",
    }
    missing_inputs = required_inputs.difference(strategy_synthesis.synthesized_from)
    if missing_inputs:
        raise KernelValidationError(
            f"strategy_synthesis missing required inputs {sorted(missing_inputs)}"
        )
    _validate_strategy_routes(
        strategy_synthesis.routes,
        idea_grade,
        counterexamples,
        review,
        computations,
        tests,
    )

def _strategy_brief_identity(strategy) -> dict:
    data = strategy.model_dump(mode="json")
    data.pop("score", None)
    return data

def _validate_strategy_brief(
    strategy_brief: StrategyBrief,
    strategy_synthesis: StrategySynthesis,
    idea_grade: IdeaGrade,
    counterexamples: CounterexampleReport,
    review: Review,
    computations: ComputationChecks,
    tests: TestReport,
) -> None:
    if not strategy_brief.top_strategies:
        raise KernelValidationError("strategy_brief requires at least one strategy")
    if len(strategy_brief.top_strategies) > 3:
        raise KernelValidationError("strategy_brief may expose at most three top strategies")
    synthesis_ids = {route.id for route in strategy_synthesis.routes}
    synthesis_by_id = {route.id: route for route in strategy_synthesis.routes}
    brief_ids = {strategy.id for strategy in strategy_brief.top_strategies}
    unknown = brief_ids.difference(synthesis_ids)
    if unknown:
        raise KernelValidationError(
            f"strategy_brief contains routes not produced by Strategy Synthesizer: {sorted(unknown)}"
        )
    for strategy in strategy_brief.top_strategies:
        synthesis_route = synthesis_by_id[strategy.id]
        if _strategy_brief_identity(strategy) != _strategy_brief_identity(synthesis_route):
            raise KernelValidationError(
                f"{strategy.id}: strategy_brief route differs from Strategy Synthesis"
            )
    _validate_strategy_routes(
        strategy_brief.top_strategies,
        idea_grade,
        counterexamples,
        review,
        computations,
        tests,
    )

def _validate_human_choice(
    human_choice: HumanChoice,
    strategy_brief: StrategyBrief,
    manifest: RunManifest,
) -> None:
    # Fail-closed provenance check (belt-and-suspenders over the HumanChoice
    # model validator): only selected_by == 'human' may claim human
    # confirmation. Written exhaustively (!= 'human', not == 'auto') so a future
    # provenance value can never claim confirmation by falling between checks.
    if human_choice.human_confirmed and human_choice.selected_by != "human":
        raise KernelValidationError(
            f"human_choice selected_by={human_choice.selected_by!r} can never claim "
            "human_confirmed=true (only a real human selection may)"
        )
    # State-name / provenance cross-check: the two CONTINUE_SELECTED state
    # spellings advertise WHO selected, so a RECORDED (status=selected) choice
    # must agree — otherwise a state-name reader is misled about human
    # involvement. A still-pending choice is exempt: the auto path honestly
    # writes AUTO_CHOICE_CONTINUE_SELECTED (and validates) BEFORE recording the
    # choice, so that transient window must stay valid (crash-truthful state).
    current_state = getattr(manifest.current_state, "value", manifest.current_state)
    if human_choice.status == HumanChoiceStatus.selected:
        if (
            current_state == "HUMAN_CHOICE_CONTINUE_SELECTED"
            and human_choice.selected_by != "human"
        ):
            raise KernelValidationError(
                "run_manifest current_state=HUMAN_CHOICE_CONTINUE_SELECTED requires "
                f"human_choice selected_by='human' (got {human_choice.selected_by!r})"
            )
        if (
            current_state == "AUTO_CHOICE_CONTINUE_SELECTED"
            and human_choice.selected_by != "auto"
        ):
            raise KernelValidationError(
                "run_manifest current_state=AUTO_CHOICE_CONTINUE_SELECTED requires "
                f"human_choice selected_by='auto' (got {human_choice.selected_by!r})"
            )
    if human_choice.status == HumanChoiceStatus.selected:
        strategy_ids = {strategy.id for strategy in strategy_brief.top_strategies}
        if human_choice.selected_strategy not in strategy_ids and human_choice.selected_strategy != "none":
            raise KernelValidationError(
                f"human_choice selected unknown strategy {human_choice.selected_strategy!r}"
            )
        choices = {
            choice.strategy_id: {effort.value for effort in choice.effort_options}
            for choice in strategy_brief.allowed_human_choices
        }
        selected_strategy = human_choice.selected_strategy
        if selected_strategy not in choices:
            raise KernelValidationError(
                f"human_choice selected strategy {selected_strategy!r} has no allowed choice option"
            )
        selected_effort = human_choice.effort.value if human_choice.effort is not None else None
        if selected_effort not in choices[selected_strategy]:
            raise KernelValidationError(
                f"human_choice effort {selected_effort!r} is not allowed for {selected_strategy!r}"
            )
        if (
            selected_strategy == "none"
            and human_choice.continue_decision == ContinueDecision.continue_run
        ):
            raise KernelValidationError("human_choice cannot continue with selected_strategy='none'")
