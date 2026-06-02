from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .io import read_text, read_yaml
from .models import (
    AgentContracts,
    AttemptStatus,
    Claim,
    ComputationStatus,
    ComputationChecks,
    CounterexampleReport,
    ContinueDecision,
    ExampleSelection,
    ExampleFeedbackSummary,
    ExampleStatus,
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
    Review,
    ReviewerVerdict,
    RunManifest,
    SourceGroundingReport,
    SourceStatus,
    StrategyBrief,
    StrategySynthesis,
    TestReport,
    TheoremExtractionReport,
)
from .strategy_rules import validate_strategy_rules


class KernelValidationError(Exception):
    pass


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
    llm_agent_run = _load_optional_llm_agent_run(run_dir, manifest)
    prover_loop = _load_optional_prover_loop(run_dir, manifest)
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
    _validate_example_evidence(computations, counterexamples, idea_grade)
    _validate_source_backing(source_grounding)
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
    _validate_llm_loop_artifacts(manifest, claim, llm_agent_run, prover_loop, strategy_brief)
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


def _validate_manifest_paths(run_dir: Path, manifest: RunManifest) -> None:
    if manifest.run_id != run_dir.name:
        raise KernelValidationError(
            f"run_manifest run_id {manifest.run_id!r} does not match run dir {run_dir.name!r}"
        )

    root = run_dir.resolve()
    manifest_paths = {"input_file": manifest.input_file}
    manifest_paths.update({f"artifacts.{key}": value for key, value in manifest.artifacts.items()})
    manifest_paths.update(
        {f"optional_artifacts.{key}": value for key, value in manifest.optional_artifacts.items()}
    )

    for label, raw_path in manifest_paths.items():
        path = Path(raw_path)
        if path.is_absolute():
            raise KernelValidationError(f"{label}: manifest path must be relative: {raw_path!r}")
        if ".." in path.parts:
            raise KernelValidationError(f"{label}: manifest path cannot contain '..': {raw_path!r}")
        resolved = (run_dir / path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise KernelValidationError(
                f"{label}: manifest path escapes run directory: {raw_path!r}"
            ) from exc
        if not resolved.exists():
            raise KernelValidationError(f"{label}: manifest path is missing: {raw_path!r}")
        if not resolved.is_file():
            raise KernelValidationError(f"{label}: manifest path is not a file: {raw_path!r}")


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


def _validate_counterexample_scope(counterexamples: CounterexampleReport) -> None:
    for test in counterexamples.tests:
        if test.satisfies_all_claim_hypotheses is False and test.refutes_claim:
            raise KernelValidationError(
                f"{test.id}: dropped-hypothesis failure cannot refute the original claim"
            )


def _validate_example_evidence(
    computations: ComputationChecks,
    counterexamples: CounterexampleReport,
    idea_grade: IdeaGrade,
) -> None:
    tests_by_id = {test.id: test for test in counterexamples.tests}
    checks_by_example: dict[str, list[str]] = {}
    for check in computations.checks:
        checks_by_example.setdefault(check.example_id, []).append(check.status.value)
    checked_examples = {
        example_id
        for example_id, statuses in checks_by_example.items()
        if statuses and all(status in {"checked", "source_checked"} for status in statuses)
    }

    evidence_test_ids = set(idea_grade.survived_tests) | set(idea_grade.failed_tests)
    for test_id in evidence_test_ids:
        test = tests_by_id.get(test_id)
        if test is None:
            raise KernelValidationError(f"{test_id}: idea grade references unknown test")
        if test.result not in {ExampleStatus.survived, ExampleStatus.failed}:
            raise KernelValidationError(f"{test_id}: evidence test result is not survived/failed")
        if test.example_id not in checked_examples:
            raise KernelValidationError(
                f"{test_id}: unchecked example {test.example_id} cannot support survived/failed evidence"
            )


def _validate_test_report_evidence(
    computations: ComputationChecks,
    examples: ExampleSelection,
    counterexamples: CounterexampleReport,
    tests: TestReport,
) -> None:
    example_ids = {example.id for example in examples.examples}
    counterexamples_by_id = {test.id: test for test in counterexamples.tests}
    counterexample_ids = set(counterexamples_by_id)
    checks_by_example: dict[str, list[str]] = {}
    for check in computations.checks:
        checks_by_example.setdefault(check.example_id, []).append(check.status.value)
    checked_examples = {
        example_id
        for example_id, statuses in checks_by_example.items()
        if statuses and all(status in {"checked", "source_checked"} for status in statuses)
    }

    for test in tests.tests:
        unknown_examples = set(test.uses_examples).difference(example_ids)
        if unknown_examples:
            raise KernelValidationError(f"{test.id}: unknown example ids {sorted(unknown_examples)}")
        unknown_counterexamples = set(test.uses_counterexamples).difference(counterexample_ids)
        if unknown_counterexamples:
            raise KernelValidationError(
                f"{test.id}: unknown counterexample ids {sorted(unknown_counterexamples)}"
            )
        if test.result in {ExampleStatus.survived, ExampleStatus.failed}:
            if test.evidence_status not in {
                ComputationStatus.checked,
                ComputationStatus.source_checked,
            }:
                raise KernelValidationError(
                    f"{test.id}: survived/failed tests require checked/source_checked evidence"
                )
            for example_id in test.uses_examples:
                if example_id not in checked_examples:
                    raise KernelValidationError(
                        f"{test.id}: unchecked example {example_id} cannot support test result"
                    )
            for counterexample_id in test.uses_counterexamples:
                counterexample = counterexamples_by_id[counterexample_id]
                if counterexample.example_id not in checked_examples:
                    raise KernelValidationError(
                        f"{test.id}: unchecked counterexample example "
                        f"{counterexample.example_id} cannot support test result"
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
    manifest: RunManifest,
    claim: Claim,
    llm_agent_run: LLMAgentRunReport | None,
    prover_loop: ProverLoopReport | None,
    strategy_brief: StrategyBrief,
) -> None:
    if llm_agent_run is None:
        if prover_loop is not None:
            raise KernelValidationError("prover_loop requires llm_agent_run")
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
        return

    if prover_loop.claim_id != claim.id:
        raise KernelValidationError("prover_loop claim_id does not match locked claim")
    if prover_loop.run_id != manifest.run_id:
        raise KernelValidationError("prover_loop run_id does not match manifest")
    if prover_loop.source_llm_agent_run != manifest.optional_artifacts.get("llm_agent_run"):
        raise KernelValidationError("prover_loop must cite the llm_agent_run optional artifact")
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
    forbidden_patterns = {
        r"\bverified\b": "verified",
        r"\bproof\s+complete\b": "proof complete",
        r"\bwe\s+proved\b": "we proved",
        r"\bproved\s+the\s+theorem\b": "proved the theorem",
        r"\bthis\s+proof\b": "this proof",
        r"\bcomplete\s+proof\b": "complete proof",
        r"\bfull\s+proof\b": "full proof",
        r"\bestablishes\s+the\s+theorem\b": "establishes the theorem",
        r"\bestablishes\s+the\s+claim\b": "establishes the claim",
        r"\bproof\s+establishes\b": "proof establishes",
    }
    if idea_grade.proof_language_status in weak_statuses:
        for pattern, phrase in forbidden_patterns.items():
            if re.search(pattern, text):
                raise KernelValidationError(
                    f"final_report.md uses forbidden proof language: {phrase!r}"
                )
