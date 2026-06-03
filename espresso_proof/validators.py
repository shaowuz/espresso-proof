from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .io import read_text, read_yaml
from .models import (
    AgentContracts,
    AttemptStatus,
    Claim,
    ComputationChecks,
    ComputationVerificationReport,
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
    LLMAgentRunReport,
    LiteratureReviewAgentReport,
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
    StrategySynthesis,
    TestReport,
    TheoremApplicabilityReport,
    TheoremExtractionReport,
)
from .strategy_rules import validate_strategy_rules
from .validation.errors import KernelValidationError
from .validation.evidence import (
    _validate_computation_verification,
    _validate_counterexample_scope,
    _validate_example_evidence,
    _validate_test_report_evidence,
)
from .validation.manifest import ManifestPathValidationError
from .validation.manifest import validate_manifest_paths


def validate_yaml_model(path: Path, model: type[BaseModel]) -> BaseModel:
    try:
        return model.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise KernelValidationError(f"{path}: schema validation failed\n{exc}") from exc


def validate_run(run_dir: Path) -> None:
    manifest = validate_yaml_model(run_dir / "run_manifest.yaml", RunManifest)
    _validate_manifest_paths(run_dir, manifest)
    artifacts = manifest.artifacts

    claim = validate_yaml_model(run_dir / artifacts["locked_claim"], Claim)
    agent_contracts = validate_yaml_model(run_dir / artifacts["agent_contracts"], AgentContracts)
    hypothesis_ledger = validate_yaml_model(run_dir / artifacts["hypothesis_ledger"], HypothesisLedger)
    literature_review = validate_yaml_model(
        run_dir / artifacts["literature_review"], LiteratureReviewReport
    )
    examples = validate_yaml_model(run_dir / artifacts["examples"], ExampleSelection)
    computations = validate_yaml_model(run_dir / artifacts["computation_checks"], ComputationChecks)
    computation_verification = _load_optional_computation_verification(run_dir, manifest)
    counterexamples = validate_yaml_model(run_dir / artifacts["counterexamples"], CounterexampleReport)
    tests = validate_yaml_model(run_dir / artifacts["tests"], TestReport)
    source_grounding = validate_yaml_model(run_dir / artifacts["source_grounding"], SourceGroundingReport)
    review = validate_yaml_model(run_dir / artifacts["review"], Review)
    idea_grade = validate_yaml_model(run_dir / artifacts["idea_grade"], IdeaGrade)
    loop_control = validate_yaml_model(run_dir / artifacts["loop_control"], LoopControl)
    strategy_synthesis = validate_yaml_model(
        run_dir / artifacts["strategy_synthesis"], StrategySynthesis
    )
    strategy_brief = validate_yaml_model(run_dir / artifacts["strategy_brief"], StrategyBrief)
    human_choice = validate_yaml_model(run_dir / artifacts["human_choice"], HumanChoice)
    _load_optional_theorem_extraction(run_dir, manifest)
    _load_optional_literature_review_agent(run_dir, manifest)
    theorem_applicability = _load_optional_theorem_applicability(run_dir, manifest)
    llm_agent_run = _load_optional_llm_agent_run(run_dir, manifest)
    prover_loop = _load_optional_prover_loop(run_dir, manifest)
    prover_round_index = _load_optional_prover_round_index(run_dir, manifest)
    human_feedback, feedback_summary = _load_optional_human_feedback(run_dir, manifest)

    _validate_claim_id_consistency(
        claim,
        human_choice,
        hypothesis_ledger,
        literature_review,
        examples,
        computations,
        counterexamples,
        tests,
        source_grounding,
        review,
        idea_grade,
        loop_control,
        strategy_synthesis,
        strategy_brief,
    )
    _validate_agent_contracts(agent_contracts, artifacts)
    _validate_literature_review_separation(literature_review)
    _validate_counterexample_scope(counterexamples)
    _validate_test_report_evidence(computations, examples, counterexamples, tests)
    _validate_computation_verification(computations, computation_verification)
    _validate_example_evidence(computations, counterexamples, idea_grade)
    _validate_source_backing(source_grounding)
    _validate_theorem_applicability(manifest, claim, source_grounding, theorem_applicability)
    _validate_review_outcome(review, idea_grade)
    _validate_loop_control(loop_control)
    _validate_strategy_synthesis(
        strategy_synthesis,
        idea_grade,
        counterexamples,
        review,
        computations,
        tests,
    )
    _validate_strategy_brief(
        strategy_brief,
        strategy_synthesis,
        idea_grade,
        counterexamples,
        review,
        computations,
        tests,
    )
    _validate_human_choice(human_choice, strategy_brief)
    _validate_human_feedback(
        manifest,
        claim,
        examples,
        counterexamples,
        tests,
        source_grounding,
        strategy_brief,
        human_feedback,
        feedback_summary,
    )
    _validate_llm_loop_artifacts(
        run_dir,
        manifest,
        claim,
        llm_agent_run,
        prover_loop,
        prover_round_index,
        strategy_brief,
    )
    _validate_final_report_language(run_dir / artifacts["final_report"], idea_grade)


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


def _load_optional_prover_round_index(
    run_dir: Path,
    manifest: RunManifest,
) -> ProverRoundIndex | None:
    optional = manifest.optional_artifacts
    if "prover_round_index" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["prover_round_index"],
        ProverRoundIndex,
    )


def _validate_manifest_paths(run_dir: Path, manifest: RunManifest) -> None:
    try:
        validate_manifest_paths(run_dir, manifest)
    except ManifestPathValidationError as exc:
        raise KernelValidationError(str(exc)) from exc


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
        "examples.yaml",
        "computation_checks.yaml",
        "counterexamples.yaml",
        "tests.yaml",
        "source_grounding.yaml",
        "review.yaml",
        "idea_grade.yaml",
        "loop_control.yaml",
        "strategy_synthesis.yaml",
        "strategy_brief.yaml",
        "human_choice.yaml",
        "final_report.md",
    }
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


def _validate_theorem_applicability(
    manifest: RunManifest,
    claim: Claim,
    source_grounding: SourceGroundingReport,
    theorem_applicability: TheoremApplicabilityReport | None,
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


def _strategy_brief_identity(strategy) -> dict:
    data = strategy.model_dump(mode="json")
    data.pop("score", None)
    return data


def _validate_human_choice(human_choice: HumanChoice, strategy_brief: StrategyBrief) -> None:
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


def _validate_llm_loop_artifacts(
    run_dir: Path,
    manifest: RunManifest,
    claim: Claim,
    llm_agent_run: LLMAgentRunReport | None,
    prover_loop: ProverLoopReport | None,
    prover_round_index: ProverRoundIndex | None,
    strategy_brief: StrategyBrief,
) -> None:
    if llm_agent_run is None:
        if prover_loop is not None:
            raise KernelValidationError("prover_loop requires llm_agent_run")
        if prover_round_index is not None:
            raise KernelValidationError("prover_round_index requires llm_agent_run")
        return

    if llm_agent_run.claim_id != claim.id:
        raise KernelValidationError("llm_agent_run claim_id does not match locked claim")
    if llm_agent_run.run_id != manifest.run_id:
        raise KernelValidationError("llm_agent_run run_id does not match manifest")
    known_artifacts = set(manifest.artifacts.values()) | set(manifest.optional_artifacts.values())
    unknown_inputs = set(llm_agent_run.input_artifacts).difference(known_artifacts)
    if unknown_inputs:
        raise KernelValidationError(
            f"llm_agent_run references unknown input artifacts {sorted(unknown_inputs)}"
        )

    if prover_loop is None:
        if prover_round_index is not None:
            raise KernelValidationError("prover_round_index requires prover_loop")
        return

    if prover_loop.claim_id != claim.id:
        raise KernelValidationError("prover_loop claim_id does not match locked claim")
    if prover_loop.run_id != manifest.run_id:
        raise KernelValidationError("prover_loop run_id does not match manifest")
    if prover_loop.source_llm_agent_run != manifest.optional_artifacts.get("llm_agent_run"):
        raise KernelValidationError("prover_loop must cite the llm_agent_run optional artifact")
    if prover_round_index is None:
        raise KernelValidationError("prover_loop requires prover_round_index")
    if prover_round_index.claim_id != claim.id:
        raise KernelValidationError("prover_round_index claim_id does not match locked claim")
    if prover_round_index.run_id != manifest.run_id:
        raise KernelValidationError("prover_round_index run_id does not match manifest")
    if prover_round_index.source_prover_loop != manifest.optional_artifacts.get("prover_loop"):
        raise KernelValidationError("prover_round_index must cite the prover_loop optional artifact")
    if prover_loop.human_gate_status != llm_agent_run.human_gate.status:
        raise KernelValidationError("prover_loop human_gate_status does not match llm_agent_run")
    if (
        llm_agent_run.human_gate.status == "pending"
        and prover_loop.loop_decision not in {"ask_human", "stop"}
    ):
        raise KernelValidationError("pending LLM human gate cannot continue prover loop")
    if llm_agent_run.human_gate.status == "pending":
        for attempt in prover_loop.attempts:
            if attempt.time_allocation not in {"ask_human", "stop"} or attempt.status not in {
                "waiting_human",
                "stopped",
            }:
                raise KernelValidationError(
                    f"{attempt.attempt_id}: pending human gate cannot allocate prover work"
                )

    known_strategy_ids = {strategy.id for strategy in strategy_brief.top_strategies}
    known_strategy_ids.add("none")
    if prover_loop.selected_strategy_id is not None and prover_loop.selected_strategy_id not in known_strategy_ids:
        raise KernelValidationError(
            f"prover_loop selected unknown strategy {prover_loop.selected_strategy_id!r}"
        )
    reviews_by_id = {review.verifier_id: review for review in llm_agent_run.verifier_reviews}
    known_reviewer_ids = set(reviews_by_id)
    if len(known_reviewer_ids) < 3:
        raise KernelValidationError("proof-close-style prover loop requires at least three reviewer roles")
    known_attempt_ids = {attempt.attempt_id for attempt in prover_loop.attempts}
    for attempt in prover_loop.attempts:
        if attempt.parent_attempt_id is not None:
            if attempt.parent_attempt_id == attempt.attempt_id:
                raise KernelValidationError(f"{attempt.attempt_id}: parent attempt cannot be self")
            if attempt.parent_attempt_id not in known_attempt_ids:
                raise KernelValidationError(
                    f"{attempt.attempt_id}: parent attempt id does not exist"
                )
        if attempt.strategy_id not in known_strategy_ids:
            raise KernelValidationError(
                f"{attempt.attempt_id}: unknown strategy id {attempt.strategy_id!r}"
            )
        if attempt.round_index == 1:
            unknown_reviewers = set(attempt.reviewer_ids).difference(known_reviewer_ids)
            if unknown_reviewers:
                raise KernelValidationError(
                    f"{attempt.attempt_id}: unknown reviewer ids {sorted(unknown_reviewers)}"
                )
        elif len(set(attempt.reviewer_ids)) < 3:
            raise KernelValidationError(
                f"{attempt.attempt_id}: appended loop round requires at least three reviewers"
            )
        if attempt.prover_attempt_id != llm_agent_run.prover_attempt.agent_id:
            if attempt.round_index == 1:
                raise KernelValidationError(
                    f"{attempt.attempt_id}: prover attempt id does not match llm_agent_run"
                )
            if not attempt.prover_attempt_id.startswith("llm_prover_"):
                raise KernelValidationError(
                    f"{attempt.attempt_id}: appended prover attempt id is malformed"
                )
        for pointer in attempt.reviewer_objections:
            if pointer.verifier_id not in attempt.reviewer_ids:
                raise KernelValidationError(
                    f"{attempt.attempt_id}: reviewer objection is not listed in reviewer_ids"
                )
            if attempt.round_index == 1:
                source_review = reviews_by_id.get(pointer.verifier_id)
                if source_review is None:
                    raise KernelValidationError(
                        f"{attempt.attempt_id}: reviewer objection references unknown reviewer "
                        f"{pointer.verifier_id!r}"
                    )
                if pointer.verdict != source_review.verdict:
                    raise KernelValidationError(
                        f"{attempt.attempt_id}: reviewer objection verdict does not match "
                        f"{pointer.verifier_id}"
                    )
                if pointer.recommended_human_action != source_review.recommended_human_action:
                    raise KernelValidationError(
                        f"{attempt.attempt_id}: reviewer objection action does not match "
                        f"{pointer.verifier_id}"
                    )
                if pointer.objection_count != len(source_review.objections):
                    raise KernelValidationError(
                        f"{attempt.attempt_id}: reviewer objection count does not match "
                        f"{pointer.verifier_id}"
                    )
    _validate_prover_round_index(
        run_dir,
        manifest,
        llm_agent_run,
        prover_loop,
        prover_round_index,
    )


def _validate_prover_round_index(
    run_dir: Path,
    manifest: RunManifest,
    llm_agent_run: LLMAgentRunReport,
    prover_loop: ProverLoopReport,
    prover_round_index: ProverRoundIndex,
) -> None:
    attempts_by_id = {attempt.attempt_id: attempt for attempt in prover_loop.attempts}
    index_by_attempt = {entry.loop_attempt_id: entry for entry in prover_round_index.rounds}
    missing_attempts = set(attempts_by_id).difference(index_by_attempt)
    extra_attempts = set(index_by_attempt).difference(attempts_by_id)
    if missing_attempts:
        raise KernelValidationError(
            f"prover_round_index missing loop attempts {sorted(missing_attempts)}"
        )
    if extra_attempts:
        raise KernelValidationError(
            f"prover_round_index references unknown loop attempts {sorted(extra_attempts)}"
        )

    root = run_dir.resolve()
    for entry in prover_round_index.rounds:
        raw_path = Path(entry.artifact_path)
        if raw_path.is_absolute() or ".." in raw_path.parts:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round artifact path must stay inside run dir"
            )
        path = (run_dir / raw_path).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round artifact path escapes run dir"
            ) from exc
        if not path.exists() or not path.is_file():
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round artifact is missing"
            )
        report = validate_yaml_model(path, ProverRoundReport)
        attempt = attempts_by_id[entry.loop_attempt_id]
        if report.claim_id != llm_agent_run.claim_id:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round claim_id does not match"
            )
        if report.run_id != manifest.run_id:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round run_id does not match manifest"
            )
        if entry.round_index != attempt.round_index or report.round_index != attempt.round_index:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round_index does not match loop attempt"
            )
        if entry.strategy_id != attempt.strategy_id or report.strategy_id != attempt.strategy_id:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round strategy_id does not match loop attempt"
            )
        if report.loop_attempt_id != attempt.attempt_id:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round loop_attempt_id does not match"
            )
        if report.parent_attempt_id != attempt.parent_attempt_id:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round parent_attempt_id does not match"
            )
        if entry.status != attempt.status or report.status != attempt.status:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round status does not match loop attempt"
            )
        if entry.time_allocation != attempt.time_allocation or report.time_allocation != attempt.time_allocation:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round time allocation does not match loop attempt"
            )
        if report.prover_attempt.agent_id != attempt.prover_attempt_id:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round prover attempt id does not match"
            )
        if {review.verifier_id for review in report.verifier_reviews} != set(attempt.reviewer_ids):
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round verifier ids do not match loop attempt"
            )
        known_artifacts = set(manifest.artifacts.values()) | set(manifest.optional_artifacts.values())
        for artifact in report.input_artifacts:
            artifact_path = Path(artifact)
            if artifact_path.is_absolute() or ".." in artifact_path.parts:
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: prover round input_artifacts must stay inside manifest"
                )
            if artifact not in known_artifacts:
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: prover round references unknown input artifact {artifact!r}"
                )
        _validate_prover_round_language(entry.loop_attempt_id, report)
        pointer_by_id = {pointer.verifier_id: pointer for pointer in attempt.reviewer_objections}
        for review in report.verifier_reviews:
            pointer = pointer_by_id.get(review.verifier_id)
            if pointer is None:
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: prover round reviewer is missing loop pointer"
                )
            if pointer.verdict != review.verdict:
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: prover round reviewer verdict does not match loop attempt"
                )
            if pointer.recommended_human_action != review.recommended_human_action:
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: prover round reviewer action does not match loop attempt"
                )
            if pointer.objection_count != len(review.objections):
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: prover round reviewer objection count does not match loop attempt"
                )
        if list(report.reviewer_blockers) != list(attempt.blockers):
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round reviewer_blockers do not match loop attempt"
            )
        if attempt.round_index == 1:
            if report.source != "initial_llm_agent_run":
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: initial prover round source is wrong"
                )
            if report.prover_attempt.model_dump(mode="json") != llm_agent_run.prover_attempt.model_dump(mode="json"):
                raise KernelValidationError(
                    f"{entry.loop_attempt_id}: initial round prover does not match llm_agent_run"
                )
            source_reviews = {
                review.verifier_id: review.model_dump(mode="json")
                for review in llm_agent_run.verifier_reviews
            }
            for review in report.verifier_reviews:
                if review.model_dump(mode="json") != source_reviews.get(review.verifier_id):
                    raise KernelValidationError(
                        f"{entry.loop_attempt_id}: initial round verifier body does not match "
                        f"llm_agent_run for {review.verifier_id}"
                    )
        elif report.source != "appended_prover_loop_round":
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: appended prover round source is wrong"
            )


def _validate_prover_round_language(loop_attempt_id: str, report: ProverRoundReport) -> None:
    text_parts = [
        report.prover_attempt.output_summary,
        *report.prover_attempt.proposed_steps,
        *report.prover_attempt.source_requests,
        *report.prover_attempt.risks,
        report.next_action,
        report.safety_note,
    ]
    for review in report.verifier_reviews:
        text_parts.extend(
            [
                *review.objections,
                *review.missing_hypotheses,
                *review.source_requests,
                *review.unsafe_language,
            ]
        )
    phrase = _first_forbidden_proof_language(" ".join(text_parts).lower())
    if phrase:
        raise KernelValidationError(
            f"{loop_attempt_id}: prover round artifact uses forbidden proof language {phrase!r}"
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


def _validate_final_report_language(final_report: Path, idea_grade: IdeaGrade) -> None:
    text = read_text(final_report).lower()
    weak_statuses = {
        ProofLanguageStatus.idea_sketch,
        ProofLanguageStatus.proof_candidate,
        ProofLanguageStatus.source_checked_argument,
        ProofLanguageStatus.computation_checked,
    }
    if idea_grade.proof_language_status in weak_statuses:
        phrase = _first_forbidden_proof_language(text)
        if phrase:
            raise KernelValidationError(
                f"final_report.md uses forbidden proof language: {phrase!r}"
            )


def _first_forbidden_proof_language(text: str) -> str | None:
    forbidden_patterns = {
        r"\bverified\b": "verified",
        r"\bproof\s+complete\b": "proof complete",
        r"\bwe\s+proved\b": "we proved",
        r"\bproved\s+the\s+theorem\b": "proved the theorem",
        r"\bproven\b": "proven",
        r"\bproves\s+the\s+claim\b": "proves the claim",
        r"\bproves\s+the\s+theorem\b": "proves the theorem",
        r"\bhas\s+been\s+proven\b": "has been proven",
        r"\bthis\s+proof\b": "this proof",
        r"\bcomplete\s+proof\b": "complete proof",
        r"\bfull\s+proof\b": "full proof",
        r"\bestablishes\s+the\s+theorem\b": "establishes the theorem",
        r"\bestablishes\s+the\s+claim\b": "establishes the claim",
        r"\bproof\s+establishes\b": "proof establishes",
    }
    for pattern, phrase in forbidden_patterns.items():
        if re.search(pattern, text):
            return phrase
    return None
