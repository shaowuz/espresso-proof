"""Validators — internal topological slice 6/6 (callees precede callers). Imported via espresso_proof.kernel.validators."""


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
from espresso_proof.orchestration.agent_flow_spec import (
    get_stage,
    ordered_stage_ids,
    stages_blocked_by_unclear_claim,
)
from espresso_proof.orchestration.agent_flow_legacy_map import get_legacy_mapping
from espresso_proof.orchestration.pressure_lane_barrier import (
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


from espresso_proof.kernel.validators._part1 import *  # noqa: F401,F403


from espresso_proof.kernel.validators._part2 import *  # noqa: F401,F403


from espresso_proof.kernel.validators._part3 import *  # noqa: F401,F403


from espresso_proof.kernel.validators._part4 import *  # noqa: F401,F403


from espresso_proof.kernel.validators._part5 import *  # noqa: F401,F403


def _prover_clause_is_support_disclaimer(clause: str) -> bool:
    return any(
        phrase in clause
        for phrase in (
            "cannot support",
            "can't support",
            "does not support",
            "doesn't support",
            "not support",
            "never support",
            "cannot contribute",
            "can't contribute",
            "do not contribute",
            "does not contribute",
            "doesn't contribute",
            "not contribute",
            "cannot count",
            "can't count",
            "not evidence",
            "is not proof",
            "not a proof",
        )
    )


def _prover_unchecked_support_misuse(text: str) -> str | None:
    for clause in _prover_assertion_clauses(text):
        if _prover_clause_is_support_disclaimer(clause):
            continue
        supportish = re.search(r"\b(?:support|supports|establish|establishes|prove|proves)\b", clause)
        if not supportish:
            continue
        if "unchecked" in clause and any(
            noun in clause
            for noun in ("citation", "citations", "source", "sources", "reference", "references")
        ):
            return "unchecked citation/source"
        if "unchecked" in clause and "example" in clause:
            return "unchecked example"
        if "pending theorem applicability" in clause:
            return "pending theorem applicability"
        if "line-level gap" in clause:
            return "line-level gap"
    return None


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
            ]
        )
    # NOTE: review.unsafe_language is deliberately excluded. It is the audit
    # ledger of forbidden phrases the kernel DETECTED in raw agent output (via
    # collect_unsafe_language) and recorded verbatim on purpose; scanning it would
    # make the guard flag its own detection record. The actual content fields
    # above are already redacted (safe_text/safe_list), so proof-language in real
    # content is still caught.
    phrase = _first_forbidden_proof_language(" ".join(text_parts).lower())
    if phrase:
        raise KernelValidationError(
            f"{loop_attempt_id}: prover round artifact uses forbidden proof language {phrase!r}"
        )
    for text in text_parts:
        status_field = _prover_status_smuggling_field(text)
        if status_field:
            raise KernelValidationError(
                f"{loop_attempt_id}: prover round text cannot set {status_field}"
            )
        misuse = _prover_unchecked_support_misuse(text)
        if misuse:
            raise KernelValidationError(
                f"{loop_attempt_id}: prover round treats unchecked context as support ({misuse})"
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
        try:
            raw_path = validate_relative_artifact_path(
                entry.loop_attempt_id,
                entry.artifact_path,
                path_name="prover round artifact path",
            )
        except ManifestPathValidationError as exc:
            raise KernelValidationError(str(exc)) from exc
        path = (run_dir / raw_path).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise KernelValidationError(
                f"{entry.loop_attempt_id}: prover round artifact path escapes run dir"
            ) from exc
        if not path_exists(path) or not path_is_file(path):
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
            try:
                validate_relative_artifact_path(
                    entry.loop_attempt_id,
                    artifact,
                    path_name="prover round input_artifacts path",
                )
            except ManifestPathValidationError as exc:
                raise KernelValidationError(str(exc)) from exc
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


def _optional_final_report_context_artifact(
    run_dir: Path,
    manifest: RunManifest,
    key: str,
) -> dict:
    rel = manifest.optional_artifacts.get(key)
    if not rel:
        return {}
    path = run_dir / rel
    return read_yaml(path) if path_exists(path) else {}


def _first_matching_pattern(text: str, patterns: dict[str, str]) -> str | None:
    for pattern, phrase in patterns.items():
        if re.search(pattern, text):
            return phrase
    return None


def _validate_final_report_does_not_hide_pending_checks(
    text: str,
    examples: ExampleSelection,
    counterexamples: CounterexampleReport,
    tests: TestReport,
) -> None:
    pending_examples = [
        example.id
        for example in examples.examples
        if example.result_for_current_idea
        in {ExampleStatus.inconclusive, ExampleStatus.not_tested}
    ]
    example_pressure_gaps = pending_examples or (
        ["no example checks recorded"] if not examples.examples else []
    )
    if example_pressure_gaps:
        phrase = _first_matching_pattern(
            text,
            {
                r"\bno\s+pending\s+examples?\s+(?:remain|left|exist)\b": "no pending examples",
                r"\bno\s+examples?\s+(?:are|remain|left|exist)\s+pending\b": "no examples pending",
                r"\bno\s+examples?\s+checks?\s+pending\b": "no example checks pending",
                r"\bno\s+examples?\s+checks?\s+(?:are|remain|left|exist)\s+pending\b": "no example checks pending",
                r"\bexamples?\s+pending\s*:\s*(?:none|zero|0)\b": "examples pending none",
                r"\bexamples?\s+checks?\s*:\s*none\s+pending\b": "example checks none pending",
                r"\bexamples?\s+(?:are\s+)?not\s+pending\b": "examples not pending",
                r"\bexamples?\s+ha(?:s|ve)\s+no\s+pending\s+checks?\b": "examples no pending checks",
                r"\ball\s+examples?\s+(?:survived|passed|validated|checked)\b": "all examples survived/passed",
                r"\bexamples?\s+(?:all\s+)?(?:survived|passed)\b": "examples survived/passed",
                r"\bsurvived\s+examples?\b": "survived examples",
                r"\bno\s+(?:failing|failed)\s+examples?\b": "no failed examples",
            },
        )
        if phrase:
            raise KernelValidationError(
                "final_report.md cannot claim examples are cleared while examples "
                f"are pending/unavailable ({', '.join(example_pressure_gaps)}): {phrase!r}"
            )

    pending_counterexamples = [
        test.id
        for test in counterexamples.tests
        if test.result in {ExampleStatus.inconclusive, ExampleStatus.not_tested}
    ]
    pending_counterexamples.extend(
        report_item.id
        for report_item in tests.tests
        if report_item.uses_counterexamples
        and report_item.result in {ExampleStatus.inconclusive, ExampleStatus.not_tested}
    )
    pending_counterexamples = list(dict.fromkeys(pending_counterexamples))
    has_counterexample_pressure = bool(counterexamples.tests) or any(
        report_item.uses_counterexamples for report_item in tests.tests
    )
    counterexample_pressure_gaps = pending_counterexamples or (
        ["no counterexample checks recorded"] if not has_counterexample_pressure else []
    )
    if counterexample_pressure_gaps:
        phrase = _first_matching_pattern(
            text,
            {
                r"\bno\s+pending\s+counterexamples?\s+(?:remain|left|exist)\b": "no pending counterexamples",
                r"\bno\s+counterexamples?\s+(?:are|remain|left|exist)\s+pending\b": "no counterexamples pending",
                r"\bno\s+counterexamples?\s+checks?\s+pending\b": "no counterexample checks pending",
                r"\bno\s+counterexamples?\s+checks?\s+(?:are|remain|left|exist)\s+pending\b": "no counterexample checks pending",
                r"\bcounterexamples?\s+pending\s*:\s*(?:none|zero|0)\b": "counterexamples pending none",
                r"\bcounterexample\s+pressure\s*:\s*(?:none|zero|0)\b": "counterexample pressure none",
                r"\bcounterexamples?\s+checks?\s*:\s*none\s+pending\b": "counterexample checks none pending",
                r"\bcounterexamples?\s+(?:are\s+)?not\s+pending\b": "counterexamples not pending",
                r"\bcounterexamples?\s+ha(?:s|ve)\s+no\s+pending\s+checks?\b": "counterexamples no pending checks",
                r"\bno\s+counterexample\s+pressure\b": "no counterexample pressure",
                r"\bno\s+counterexamples?\s+(?:remain|exist|found|were\s+found|detected)\b": "no counterexamples found",
                r"\bcounterexample\s+search\s+(?:found\s+)?(?:none|nothing)\b": "counterexample search found none",
                r"\bcounterexamples?\s+(?:are\s+)?(?:cleared|settled)\b": "counterexamples cleared",
            },
        )
        if phrase:
            raise KernelValidationError(
                "final_report.md cannot claim counterexample pressure is cleared "
                "while counterexample checks are pending/unavailable "
                f"({', '.join(counterexample_pressure_gaps)}): {phrase!r}"
            )


def _has_theorem_applicability_blocker(
    theorem_applicability: TheoremApplicabilityReport | None,
) -> bool:
    if theorem_applicability is None:
        return False
    if theorem_applicability.overall_status != "all_applicable":
        return True
    return any(
        usage.blocks_source_backed or usage.applicability_status != "applicable"
        for usage in [*theorem_applicability.usages, *theorem_applicability.ledger_usages]
    )


def _has_reviewer_blocker(review: Review) -> bool:
    return (
        any(reviewer.verdict != ReviewerVerdict.passed for reviewer in review.reviewers)
        or bool(review.theorem_drift)
        or bool(review.missing_hypotheses)
        or bool(review.source_risks)
        or bool(review.example_risks)
        or bool(review.downgrade_reasons)
    )


def _first_stale_clearing_phrase(text: str) -> str | None:
    for clause in _prover_assertion_clauses(text):
        phrase = _first_matching_pattern(
            clause,
            {
                r"\b(?:not|no\s+longer)\s+stale\b": "not stale",
                r"\bnot\s+pending\b": "not pending",
                r"\bno\s+(?:pending|stale|late|unapplied)\s+(?:updates?|signals?|events?|entries?)\b": "no pending updates",
                r"\bupdate inbox\s*:\s*(?:none|empty|clear|cleared|resolved)\b": "update inbox none",
                r"\bupdate inbox\s+(?:has|contains|shows)\s+no\s+(?:pending|stale|late|unapplied)?\s*(?:entries?|updates?|events?|signals?)\b": "update inbox no pending entries",
                r"\bno\s+(?:pending|stale|late|unapplied)\s+update inbox\s+(?:entries?|updates?|events?|signals?)\b": "no pending update inbox entries",
                r"\ball\s+update inbox\s+(?:entries?|updates?|events?|signals?)\s+(?:are\s+)?(?:resolved|cleared|applied)\b": "update inbox entries resolved",
                r"\bupdate inbox\s+(?:is\s+)?(?:current|up[- ]to[- ]date|clear|cleared|resolved|empty)\b": "update inbox current",
                r"\bno\s+(?:late|pending|unapplied)\s+advisory\s+signals?\s+remain\b": "no late advisory signals remain",
                r"\b(?:late|pending|unapplied)\s+advisory\s+signals?\s+(?:are\s+)?(?:resolved|cleared|applied)\b": "late advisory signals resolved",
                r"\b(?:updates?|signals?|events?)\s+(?:are\s+)?(?:cleared|resolved)\b": "updates cleared",
            },
        )
        if phrase:
            return phrase
        if any(term in clause for term in ("stale", "pending", "unapplied", "update inbox", "late")):
            continue
        phrase = _first_matching_pattern(
            clause,
            {
                r"\b(?:complete|up[- ]to[- ]date|current)\s+(?:final\s+)?(?:summary|report|answer|state)\b": "current report",
                r"\b(?:the\s+|this\s+)?(?:final\s+)?(?:summary|report|answer|state)\s+(?:is|was|remains)\s+(?:complete|up[- ]to[- ]date|current|final)\b": "current report",
                r"\bfinal\s+(?:summary|answer|state)\b": "final summary",
            },
        )
        if phrase:
            return phrase
    return None


def _validate_final_report_does_not_hide_blockers(
    text: str,
    idea_grade: IdeaGrade,
    source_grounding: SourceGroundingReport,
    review: Review,
    theorem_applicability: TheoremApplicabilityReport | None,
    update_inbox: UpdateInboxReport | None,
) -> None:
    has_source_gap = bool(idea_grade.source_gaps) or any(
        ref.status != SourceStatus.source_backed for ref in source_grounding.source_refs
    ) or any(step.status != SourceStatus.source_backed for step in source_grounding.steps)
    if has_source_gap:
        phrase = _first_matching_pattern(
            text,
            {
                r"\bno\s+source\s+gaps?\b": "no source gaps",
                r"\bno\s+open\s+source\s+gaps?\b": "no open source gaps",
                r"\b(?:open\s+)?source\s+gaps?\s*:\s*(?:none|zero|0)\b": "source gaps none",
                r"\bthere\s+(?:are|is)\s+no\s+open\s+source\s+gaps?\b": "no open source gaps",
                r"\bthere\s+(?:are|is)\s+(?:zero|0)\s+(?:open\s+)?source\s+gaps?\b": "zero source gaps",
                r"\bsource\s+gaps?\s+(?:are\s+)?(?:resolved|cleared|closed)\b": "source gaps resolved",
                r"\ball\s+source\s+gaps?\s+ha(?:s|ve)\s+been\s+(?:resolved|cleared|closed)\b": "all source gaps resolved",
                r"\ball\s+sources?\s+(?:checked|validated|verified|backed)\b": "all sources checked/backed",
                r"\bsource[- ]backed\s+(?:proof|argument|route)\b": "source-backed proof/argument",
                r"\broute\s+(?:is\s+)?source[- ]backed\b": "route is source-backed",
            },
        )
        if phrase:
            raise KernelValidationError(
                f"final_report.md cannot hide open source gaps: {phrase!r}"
            )

    if _has_theorem_applicability_blocker(theorem_applicability):
        phrase = _first_matching_pattern(
            text,
            {
                r"\bno\s+theorem\s+(?:blockers?|applicability\s+blockers?)\b": "no theorem blockers",
                r"\ball\s+theorem\s+(?:conditions|hypotheses)\s+(?:are\s+)?(?:satisfied|checked|met)\b": "all theorem conditions satisfied",
                r"\btheorem\s+applicability\s+(?:is\s+)?(?:clear|settled|all\s+applicable|applicable)\b": "theorem applicability clear/applicable",
                r"\btheorem\s+(?:applies|is\s+applicable)\b": "theorem applies",
            },
        )
        if phrase:
            raise KernelValidationError(
                f"final_report.md cannot hide theorem applicability blockers: {phrase!r}"
            )

    if _has_reviewer_blocker(review):
        phrase = _first_matching_pattern(
            text,
            {
                r"\bno\s+(?:reviewer\s+)?(?:blockers?|objections?|risks?)\b": "no reviewer blockers/objections",
                r"\breviewers?\s+(?:approved|passed|found\s+no)\b": "reviewers approved/passed",
                r"\ball\s+reviewers?\s+(?:passed|approved)\b": "all reviewers passed",
            },
        )
        if phrase:
            raise KernelValidationError(
                f"final_report.md cannot hide reviewer blockers: {phrase!r}"
            )

    pending_update_ids = [
        entry.id for entry in (update_inbox.entries if update_inbox else []) if entry.applied_at is None
    ]
    if pending_update_ids:
        phrase = _first_stale_clearing_phrase(text)
        if phrase:
            raise KernelValidationError(
                "final_report.md cannot present stale/pending scheduling signals "
                f"as cleared ({', '.join(pending_update_ids)}): {phrase!r}"
            )


def _first_final_report_unchecked_support_misuse(text: str) -> str | None:
    context_terms = (
        "memory",
        "prior run",
        "prior-run",
        "prior runs",
        "human preference",
        "human choice",
        "human confirmed",
        "mathematician preference",
        "strategy brief",
        "update inbox",
        "literature",
        "literature review",
        "literature candidate",
        "literature candidates",
        "candidate literature",
        "candidate source",
        "candidate sources",
        "candidate reference",
        "candidate references",
        "candidate theorem",
        "prover",
        "prover text",
        "prover output",
        "prover answer",
        "prover response",
        "prover draft",
        "prover round",
        "prover comment",
        "reviewer",
        "reviewer text",
        "reviewer output",
        "reviewer answer",
        "reviewer response",
        "reviewer assessment",
        "reviewer comment",
        "reviewer approval",
        "reviewer objection",
        "reviewer report",
        "reviewer verdict",
        "reviewers passed",
        "route",
        "strategy",
        "autonomous retrieval",
        "retrieval hit",
        "proposed source",
        "proposed sources",
        "unchecked source",
        "unchecked citation",
    )
    support_terms = (
        r"\b(?:evidence|support(?:s|ed|ing)?|establish(?:es|ed|ing)?|"
        r"prove(?:s|d|n|ing)?|confirm(?:s|ed|ing)?|validat(?:es|ed|ing)?|"
        r"certif(?:y|ies|ied|ying)|verif(?:y|ies|ied|ying)|"
        r"show(?:s|ed|ing)?|demonstrat(?:e|es|ed|ing)|"
        r"approv(?:e|es|ed|ing))\b"
    )
    for clause in _prover_assertion_clauses(text):
        if _prover_clause_is_support_disclaimer(clause):
            continue
        if re.search(
            (
                r"\b(?:prover|reviewer)\s+"
                r"(?:(?:text|output|answer|response|draft|round|comment|assessment|approval|objection|report|verdict)\s+)?"
                r"(?:is|are|was|were|becomes?|became)\s+"
                r"(?:a\s+)?(?:proof|proof\s+status|source\s+support|evidence|"
                r"checked\s+evidence|refutation|refutation\s+status)\b"
            ),
            clause,
        ):
            return clause
        if any(term in clause for term in context_terms) and re.search(support_terms, clause):
            return clause
    return None


def _first_final_report_global_source_backed_projection(text: str) -> str | None:
    patterns = {
        r"\bsource[-_ ]backed\s+(?:proof|argument|route|strategy)\b": "source-backed proof/argument/route",
        r"\b(?:proof|argument|route|strategy)\s+(?:is|are|was|were|becomes?|became)\s+(?:a\s+)?source[-_ ]backed\b": "proof/argument/route is source-backed",
    }
    for clause in _prover_assertion_clauses(text):
        if any(
            phrase in clause
            for phrase in (
                "not source-backed",
                "not source backed",
                "not source_backed",
                "no source-backed",
                "no source backed",
                "no source_backed",
                "without source-backed",
                "without source backed",
                "without source_backed",
            )
        ):
            continue
        phrase = _first_matching_pattern(clause, patterns)
        if phrase:
            return phrase
    return None


def _validate_final_report_language(
    final_report: Path,
    manifest: RunManifest,
    idea_grade: IdeaGrade,
    examples: ExampleSelection,
    counterexamples: CounterexampleReport,
    tests: TestReport,
    source_grounding: SourceGroundingReport,
    review: Review,
    theorem_applicability: TheoremApplicabilityReport | None,
    update_inbox: UpdateInboxReport | None,
) -> None:
    text = read_text(final_report).lower()
    warnings = collect_surface_warnings(
        grade=idea_grade.model_dump(mode="json"),
        review=review.model_dump(mode="json"),
        examples=examples.model_dump(mode="json"),
        counterexamples=counterexamples.model_dump(mode="json"),
        tests=tests.model_dump(mode="json"),
        source_grounding=source_grounding.model_dump(mode="json"),
        theorem_applicability=(
            theorem_applicability.model_dump(mode="json")
            if theorem_applicability is not None
            else {}
        ),
        update_inbox=(
            update_inbox.model_dump(mode="json") if update_inbox is not None else {}
        ),
        line_numbered_proof=_optional_final_report_context_artifact(
            final_report.parent, manifest, "line_numbered_proof"
        ),
        agent_consensus_review=_optional_final_report_context_artifact(
            final_report.parent, manifest, "agent_consensus_review"
        ),
    )
    weak_statuses = {
        ProofLanguageStatus.idea_sketch,
        ProofLanguageStatus.proof_candidate,
        # Agent-consensus mock-check is amber/NOT machine-verified, so its final
        # report is held to the same no-forbidden-proof-language bar as the other
        # weak statuses (it may never print "proof-close"/"proved"/"verified").
        ProofLanguageStatus.agent_consensus_mock_checked,
        ProofLanguageStatus.source_checked_argument,
        ProofLanguageStatus.computation_checked,
    }
    if idea_grade.proof_language_status in weak_statuses:
        phrase = _first_forbidden_proof_language(text)
        if phrase:
            raise KernelValidationError(
                f"final_report.md uses forbidden proof language: {phrase!r}"
            )
    _validate_final_report_does_not_hide_pending_checks(
        text, examples, counterexamples, tests
    )
    source_projection = _first_final_report_global_source_backed_projection(text)
    if source_projection:
        raise KernelValidationError(
            "final_report.md projects step-local source support onto a route/proof/argument: "
            f"{source_projection!r}"
        )
    _validate_final_report_does_not_hide_blockers(
        text, idea_grade, source_grounding, review, theorem_applicability, update_inbox
    )
    support_misuse = _first_final_report_unchecked_support_misuse(text)
    if support_misuse:
        raise KernelValidationError(
            "final_report.md treats advisory/scheduling context as evidence: "
            f"{support_misuse!r}"
        )
    missing = final_report_missing_disclosures(
        text,
        warnings,
        allow_missing_header=False,
    )
    if missing:
        raise KernelValidationError(
            "final_report.md must disclose active blocker/pending/stale categories: "
            + ", ".join(missing)
        )


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
    examples = load_examples_artifact(run_dir / artifacts["examples"])
    computations = validate_yaml_model(run_dir / artifacts["computation_checks"], ComputationChecks)
    computation_verification = _load_optional_computation_verification(run_dir, manifest)
    failure_classification = _load_optional_failure_classification(run_dir, manifest)
    mathematical_judgment = _load_optional_mathematical_judgment(run_dir, manifest)
    failure_pattern_audit = _load_optional_failure_pattern_audit(run_dir, manifest)
    literature_retrieval = _load_optional_literature_retrieval(run_dir, manifest)
    proof_verification = _load_optional_proof_verification(run_dir, manifest)
    _load_optional_finite_object_witness(run_dir, manifest, claim)
    external_tool_candidates = _load_optional_external_tool_candidates(run_dir, manifest)
    external_tool_runs = _load_optional_external_tool_runs(run_dir, manifest)
    counterexamples = load_counterexamples_artifact(run_dir / artifacts["counterexamples"])
    tests = validate_yaml_model(run_dir / artifacts["tests"], TestReport)
    source_grounding = validate_yaml_model(run_dir / artifacts["source_grounding"], SourceGroundingReport)
    review = validate_yaml_model(run_dir / artifacts["review"], Review)
    idea_grade = validate_yaml_model(run_dir / artifacts["idea_grade"], IdeaGrade)
    loop_control = validate_yaml_model(run_dir / artifacts["loop_control"], LoopControl)
    strategy_synthesis = load_strategy_synthesis_artifact(
        run_dir / artifacts["strategy_synthesis"]
    )
    strategy_brief = validate_yaml_model(run_dir / artifacts["strategy_brief"], StrategyBrief)
    human_choice = validate_yaml_model(run_dir / artifacts["human_choice"], HumanChoice)
    theorem_extraction = _load_optional_theorem_extraction(run_dir, manifest)
    _load_optional_literature_review_agent(run_dir, manifest)
    _load_optional_literature_search_plan(run_dir, manifest)
    autonomous_literature_retrieval = _load_optional_autonomous_literature_retrieval(run_dir, manifest)
    _load_optional_literature_candidates(run_dir, manifest)
    _load_optional_retrieval_resolution(run_dir, manifest)
    proof_gap_resolution = _load_optional_proof_gap_resolution(run_dir, manifest)
    memory_context = _load_optional_memory_context(run_dir, manifest)
    if memory_context is not None:
        _validate_memory_context(memory_context, claim, manifest)
    # Phase 03: content-validate the durable front_half_run audit artifact when
    # present (claim/run ids, stage names, no error-as-ok, path confinement). A
    # run without it still validates (old-run compatible).
    validate_front_half_run(run_dir)
    theorem_applicability = _load_optional_theorem_applicability(run_dir, manifest)
    _load_optional_theorem_applicability_review(run_dir, manifest, claim)
    llm_agent_run = _load_optional_llm_agent_run(run_dir, manifest)
    prover_loop = _load_optional_prover_loop(run_dir, manifest)
    prover_round_index = _load_optional_prover_round_index(run_dir, manifest)
    proof_goal_graph = _load_optional_proof_goal_graph(run_dir, manifest)
    theorem_condition_ledger = _load_optional_theorem_condition_ledger(run_dir, manifest)
    agent_run_status = _load_optional_agent_run_status(run_dir, manifest)
    pressure_status = _load_optional_pressure_status(run_dir, manifest)
    stage_status = _load_optional_stage_status(run_dir, manifest)
    pressure_lane_barrier = _load_optional_pressure_lane_barrier(run_dir, manifest)
    time_allocation = _load_optional_time_allocation(run_dir, manifest)
    update_inbox = _load_optional_update_inbox(run_dir, manifest)
    _load_optional_ai_review(run_dir, manifest, claim)
    line_numbered_proof = _load_optional_line_numbered_proof(run_dir, manifest, claim)
    agent_consensus_review = _load_optional_agent_consensus_review(run_dir, manifest, claim)
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
    _validate_test_report_evidence(computations, examples, counterexamples, tests)
    _validate_computation_verification(computations, computation_verification)
    _validate_failure_classification(failure_classification, counterexamples, claim)
    _validate_refuted_grade_admissible(idea_grade, counterexamples)
    _validate_mathematical_judgment(mathematical_judgment, claim, manifest)
    _validate_failure_pattern_audit(failure_pattern_audit, claim, manifest)
    _validate_literature_retrieval(literature_retrieval, claim, manifest)
    _validate_proof_gap_resolution(proof_gap_resolution, claim, manifest, run_dir)
    _validate_proof_verification(proof_verification, claim, manifest, idea_grade)
    _validate_grade_proof_language_backed(
        idea_grade,
        source_grounding,
        computation_verification,
        proof_verification,
        agent_consensus_review,
    )
    _validate_grade_10_requires_global_proof_status(idea_grade)
    _validate_external_tool_artifacts(
        external_tool_candidates, external_tool_runs, claim, manifest
    )
    _validate_subgoal_graph(
        proof_goal_graph,
        claim,
        manifest,
        source_grounding,
        proof_verification,
        computation_verification,
        idea_grade,
        strategy_brief,
        theorem_condition_ledger,
    )
    _validate_example_evidence(computations, counterexamples, idea_grade)
    _validate_source_backing(source_grounding)
    _validate_autonomous_literature_retrieval(
        autonomous_literature_retrieval,
        source_grounding,
        theorem_extraction,
    )
    _validate_source_discipline_bindings(
        run_dir, manifest, source_grounding, theorem_extraction, theorem_applicability
    )
    _validate_theorem_applicability(
        manifest, claim, source_grounding, theorem_applicability, theorem_extraction
    )
    _validate_review_outcome(review, idea_grade)
    _validate_agent_consensus_review(
        agent_consensus_review, review, idea_grade, manifest, run_dir, line_numbered_proof
    )
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
    _validate_agent_run_status(agent_run_status, claim, manifest, run_dir)
    _validate_pressure_status(
        pressure_status,
        claim,
        manifest,
        run_dir,
        agent_run_status,
        examples,
        counterexamples,
    )
    _validate_stage_status(stage_status, claim, manifest, run_dir)
    _validate_pressure_lane_barrier(
        pressure_lane_barrier,
        claim,
        manifest,
        run_dir,
        agent_run_status,
    )
    _validate_time_allocation(
        time_allocation,
        claim,
        manifest,
        run_dir,
        strategy_synthesis,
        strategy_brief,
    )
    _validate_update_inbox(update_inbox, claim, manifest, run_dir, time_allocation)
    _validate_final_report_language(
        run_dir / artifacts["final_report"],
        manifest,
        idea_grade,
        examples,
        counterexamples,
        tests,
        source_grounding,
        review,
        theorem_applicability,
        update_inbox,
    )


__all__ = [
    '_first_final_report_global_source_backed_projection',
    '_first_final_report_unchecked_support_misuse',
    '_first_matching_pattern',
    '_first_stale_clearing_phrase',
    '_has_reviewer_blocker',
    '_has_theorem_applicability_blocker',
    '_optional_final_report_context_artifact',
    '_prover_clause_is_support_disclaimer',
    '_prover_unchecked_support_misuse',
    '_validate_final_report_does_not_hide_blockers',
    '_validate_final_report_does_not_hide_pending_checks',
    '_validate_final_report_language',
    '_validate_llm_loop_artifacts',
    '_validate_prover_round_index',
    '_validate_prover_round_language',
    'validate_run',
]
