from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ValidationError

from espresso_proof.kernel.io import read_text, read_yaml
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
    StrategyBrief,
    StrategySynthesis,
    TestReport,
    TheoremApplicabilityReport,
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


def validate_yaml_model(path: Path, model: type[BaseModel]) -> BaseModel:
    try:
        return model.model_validate(read_yaml(path))
    except ValidationError as exc:
        raise KernelValidationError(f"{path}: schema validation failed\n{exc}") from exc


def _memory_context_schema_version(raw: object) -> int:
    if not isinstance(raw, dict):
        return 2
    try:
        return int(raw.get("schema_version", 1))
    except (TypeError, ValueError):
        return 1


def _legacy_memory_context_provenance(run_dir: Path, artifact_path: str) -> str:
    return f"projects/{run_dir.parents[1].name}/runs/{run_dir.name}/{artifact_path}"


def _adapt_legacy_memory_context(raw: dict, run_dir: Path, artifact_path: str) -> dict:
    """Keep legacy memory_context files readable without replaying them.

    V1 entries did not carry per-memory claim binding or provenance. For
    validation they are converted to pending notes, so old runs can still render
    memory context while no route ranking, time allocation, or test-priority
    action is implied before revalidation.
    """

    adapted = dict(raw)
    entries = []
    for raw_entry in raw.get("matched_entries") or []:
        if not isinstance(raw_entry, dict):
            entries.append(raw_entry)
            continue
        entry = dict(raw_entry)
        reason = str(entry.get("relevance_reason") or "legacy memory context entry")
        if "revalidation" not in reason.lower():
            reason = f"{reason}; pending revalidation before reuse"
        entry["relevance_reason"] = reason
        entry["recommended_use"] = "note"
        entry["memory_claim_id"] = str(
            entry.get("memory_claim_id") or "__legacy_memory_context_unscoped__"
        )
        provenance = entry.get("provenance")
        entry["provenance"] = (
            provenance
            if provenance and _has_run_artifact_provenance(provenance)
            else [_legacy_memory_context_provenance(run_dir, artifact_path)]
        )
        entry["revalidation_required"] = True
        entry["revalidated_against_locked_claim"] = False
        entries.append(entry)
    adapted["schema_version"] = 2
    adapted["matched_entries"] = entries
    adapted.setdefault("advisory_only", True)
    adapted.setdefault("generated_at", "")
    adapted.setdefault("constraints", [])
    return adapted


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
    memory_context = _load_optional_memory_context(run_dir, manifest)
    if memory_context is not None:
        _validate_memory_context(memory_context, claim, manifest)
    # Phase 03: content-validate the durable front_half_run audit artifact when
    # present (claim/run ids, stage names, no error-as-ok, path confinement). A
    # run without it still validates (old-run compatible).
    validate_front_half_run(run_dir)
    theorem_applicability = _load_optional_theorem_applicability(run_dir, manifest)
    llm_agent_run = _load_optional_llm_agent_run(run_dir, manifest)
    prover_loop = _load_optional_prover_loop(run_dir, manifest)
    prover_round_index = _load_optional_prover_round_index(run_dir, manifest)
    proof_goal_graph = _load_optional_proof_goal_graph(run_dir, manifest)
    theorem_condition_ledger = _load_optional_theorem_condition_ledger(run_dir, manifest)
    agent_run_status = _load_optional_agent_run_status(run_dir, manifest)
    pressure_status = _load_optional_pressure_status(run_dir, manifest)
    time_allocation = _load_optional_time_allocation(run_dir, manifest)
    update_inbox = _load_optional_update_inbox(run_dir, manifest)
    _load_optional_ai_review(run_dir, manifest, claim)
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
        agent_consensus_review, review, idea_grade, manifest, run_dir
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
    _validate_time_allocation(
        time_allocation,
        claim,
        manifest,
        run_dir,
        strategy_synthesis,
        strategy_brief,
    )
    _validate_update_inbox(update_inbox, claim, manifest, run_dir)
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


def _load_optional_agent_run_status(
    run_dir: Path,
    manifest: RunManifest,
) -> AgentRunStatusReport | None:
    optional = manifest.optional_artifacts
    if "agent_run_status" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["agent_run_status"],
        AgentRunStatusReport,
    )


def _load_optional_pressure_status(
    run_dir: Path,
    manifest: RunManifest,
) -> PressureStatusReport | None:
    optional = manifest.optional_artifacts
    if "pressure_status" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["pressure_status"],
        PressureStatusReport,
    )


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


def _validate_agent_run_status(
    agent_run_status: AgentRunStatusReport | None,
    claim: Claim,
    manifest: RunManifest,
    run_dir: Path,
) -> None:
    """The per-agent run-status board (Goal 27) is advisory process state.

    Only runs when the optional artifact is present. The report model carries no
    source/evidence/proof/grade status field and StrictBaseModel forbids extras,
    so a board that smuggles in ``source_backed`` / checked evidence / a proof or
    grade status is already rejected at load (validate_yaml_model). Here we bind
    the board to THIS run and harden the evidence-gate flag:

    - claim_id must match the locked claim and run_id must match the manifest;
    - every recorded ``artifact_path`` must resolve inside the run directory;
    - ``safe_to_use_as_evidence`` (already gated to status='done' at the model)
      additionally requires the recorded artifact to exist on disk -- a board
      can never mark an absent/rolled-back artifact as usable evidence.

    The board cannot upgrade any status by construction; this binding just keeps
    the dashboard's "this agent is done" claim honest against the real artifacts.
    """
    if agent_run_status is None:
        return
    if agent_run_status.claim_id != claim.id:
        raise KernelValidationError(
            "agent_run_status claim_id does not match locked claim"
        )
    if agent_run_status.run_id != manifest.run_id:
        raise KernelValidationError(
            "agent_run_status run_id does not match run manifest"
        )
    run_root = run_dir.resolve()
    for entry in agent_run_status.agents:
        if entry.artifact_path is not None:
            try:
                raw_path = validate_relative_artifact_path(
                    entry.agent,
                    entry.artifact_path,
                    path_name="agent_run_status artifact_path",
                )
            except ManifestPathValidationError as exc:
                raise KernelValidationError(str(exc)) from exc
            resolved = (run_dir / raw_path).resolve()
            if resolved != run_root and run_root not in resolved.parents:
                raise KernelValidationError(
                    f"agent_run_status artifact_path escapes run directory: "
                    f"{entry.artifact_path!r}"
                )
            if entry.safe_to_use_as_evidence and not resolved.exists():
                raise KernelValidationError(
                    f"agent_run_status marks {entry.agent!r} safe_to_use_as_evidence "
                    "but its artifact is missing"
                )


def _validate_pressure_status(
    pressure_status: PressureStatusReport | None,
    claim: Claim,
    manifest: RunManifest,
    run_dir: Path,
    agent_run_status: AgentRunStatusReport | None,
    examples: ExampleSelection,
    counterexamples: CounterexampleReport,
) -> None:
    """Async pressure sidecar is derived/advisory only.

    It may expose pending/late pressure to scheduling surfaces, but it cannot
    create survived examples, no-counterexample evidence, or refutation status.
    Bind it back to the current run and artifacts so a stale or hand-edited
    pressure file fails closed.
    """

    if pressure_status is None:
        return
    if pressure_status.claim_id != claim.id:
        raise KernelValidationError("pressure_status claim_id does not match locked claim")
    if pressure_status.run_id != manifest.run_id:
        raise KernelValidationError("pressure_status run_id does not match run manifest")
    kinds = {entry.kind for entry in pressure_status.entries}
    if kinds != {"examples", "counterexamples"}:
        raise KernelValidationError("pressure_status requires exactly examples and counterexamples entries")

    board_statuses = (
        {entry.agent: entry.status for entry in agent_run_status.agents}
        if agent_run_status is not None
        else {}
    )
    artifact_counts = {
        "examples": len(examples.examples),
        "counterexamples": len(counterexamples.tests),
    }
    expected_artifacts = {
        "examples": manifest.artifacts["examples"],
        "counterexamples": manifest.artifacts["counterexamples"],
    }
    seen_dedupe_keys: set[str] = set()
    for entry in pressure_status.entries:
        expected_status = board_statuses.get(entry.kind, "no_board")
        if entry.agent_status != expected_status:
            raise KernelValidationError(
                f"pressure_status {entry.kind} agent_status is stale: "
                f"{entry.agent_status!r} != {expected_status!r}"
            )
        if entry.artifact_path != expected_artifacts[entry.kind]:
            raise KernelValidationError(
                f"pressure_status {entry.kind} artifact_path does not match manifest"
            )
        if entry.candidate_count != artifact_counts[entry.kind]:
            raise KernelValidationError(
                f"pressure_status {entry.kind} candidate_count is stale"
            )
        expected_state = _expected_pressure_state(entry.kind, expected_status, examples, counterexamples)
        if entry.pressure_state != expected_state:
            raise KernelValidationError(
                f"pressure_status {entry.kind} pressure_state is stale: "
                f"{entry.pressure_state!r} != {expected_state!r}"
            )
        _validate_pressure_counts(entry, examples, counterexamples)
        artifact_hash = artifact_revision_hash(run_dir, entry.artifact_path)
        for marker in entry.late_markers:
            if marker.effect != "scheduling_only" or not marker.advisory_only:
                raise KernelValidationError("late pressure marker must be scheduling-only/advisory")
            if marker.update_inbox_entry is not False:
                raise KernelValidationError("Task 03 pressure marker must not be an Update Inbox entry")
            if marker.artifact_revision_or_hash != artifact_hash:
                raise KernelValidationError("late pressure marker artifact_revision_or_hash is stale")
            if marker.scope != "whole_claim" or marker.route_id_or_step_id != "whole_claim":
                raise KernelValidationError("Task 03 late pressure marker must be scoped to whole_claim")
            if marker.late_after_strategy is not True:
                raise KernelValidationError("late pressure marker must record late_after_strategy=true")
            if marker.origin_stage != entry.kind:
                raise KernelValidationError("late pressure marker origin_stage does not match entry kind")
            if marker.origin_artifact != entry.artifact_path:
                raise KernelValidationError("late pressure marker origin_artifact does not match entry")
            if marker.locked_claim_id != claim.id:
                raise KernelValidationError("late pressure marker locked_claim_id does not match claim")
            if marker.pressure_state != entry.pressure_state:
                raise KernelValidationError("late pressure marker pressure_state does not match entry")
            if marker.dedupe_key in seen_dedupe_keys:
                raise KernelValidationError("duplicate late pressure marker dedupe_key")
            seen_dedupe_keys.add(marker.dedupe_key)
            valid_ids = (
                {example.id for example in examples.examples}
                if entry.kind == "examples"
                else {test.id for test in counterexamples.tests}
            )
            if marker.origin_artifact_id not in valid_ids:
                raise KernelValidationError("late pressure marker origin_artifact_id is stale")


def _expected_pressure_state(
    kind: str,
    agent_status: str,
    examples: ExampleSelection,
    counterexamples: CounterexampleReport,
) -> str:
    if kind == "examples":
        return derive_examples_pressure_state(list(examples.examples), agent_status)
    return derive_counterexamples_pressure_state(list(counterexamples.tests), agent_status)


def _validate_pressure_counts(
    entry,
    examples: ExampleSelection,
    counterexamples: CounterexampleReport,
) -> None:
    if entry.kind == "examples":
        needs = sum(
            1
            for example in examples.examples
            if example.satisfies_all_claim_hypotheses == "unclear"
            or example.result_for_current_idea in {ExampleStatus.inconclusive, ExampleStatus.not_tested}
        )
        inadmissible = sum(
            1
            for example in examples.examples
            if example.satisfies_all_claim_hypotheses is False
            or example.result_for_current_idea == ExampleStatus.failed
        )
        admissible = 0
    else:
        needs = sum(
            1 for test in counterexamples.tests if needs_counterexample_admissibility(test)
        )
        inadmissible = sum(
            1 for test in counterexamples.tests if is_inadmissible_counterexample_pressure(test)
        )
        admissible = sum(
            1 for test in counterexamples.tests if is_admissible_refutation(test)
        )
    if entry.needs_admissibility_count != needs:
        raise KernelValidationError(f"pressure_status {entry.kind} needs_admissibility_count is stale")
    if entry.inadmissible_pressure_count != inadmissible:
        raise KernelValidationError(f"pressure_status {entry.kind} inadmissible_pressure_count is stale")
    if entry.admissible_refutation_count != admissible:
        raise KernelValidationError(f"pressure_status {entry.kind} admissible_refutation_count is stale")


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
    if not artifact_path.exists():
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


def _validate_update_inbox(
    update_inbox: UpdateInboxReport | None,
    claim: Claim,
    manifest: RunManifest,
    run_dir: Path,
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


def _load_optional_failure_classification(
    run_dir: Path,
    manifest: RunManifest,
) -> FailureClassificationReport | None:
    optional = manifest.optional_artifacts
    if "failure_classification" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["failure_classification"],
        FailureClassificationReport,
    )


def _has_admissible_counterexample(counterexamples: CounterexampleReport) -> bool:
    """Single source of truth for "what counts as a real refutation".

    True iff some counterexample is an admissible CHECKED refutation: it satisfies
    every claim hypothesis (``satisfies_all_claim_hypotheses is True``) AND refutes
    the claim. ``refutes_claim`` already requires a checked ``failed`` result and
    forbids dropped-hypothesis / proof-step-only cases at the model level
    (``CounterexampleTest.validate_refutation_scope``). Both the failure-
    classification gate and the idea-grade ``refuted`` gate use this, so the
    refutation definition cannot drift between them.
    """
    return any(
        is_admissible_refutation(test)
        for test in counterexamples.tests
    )


def _validate_failure_classification(
    failure_classification: FailureClassificationReport | None,
    counterexamples: CounterexampleReport,
    claim: Claim,
) -> None:
    """Failure classification is advisory routing; it must not fabricate a refutation.

    Only runs when the optional artifact is present. Rules:

    - claim_id must match the locked claim;
    - a ``claim_refuted`` classification requires an admissible all-hypotheses
      counterexample (satisfies_all_claim_hypotheses is True and refutes_claim),
      so a dropped-hypothesis or proof-step failure can never be promoted to a
      claim refutation through routing.

    The report model carries no source/evidence/proof status field, so a
    classification cannot upgrade any status by construction.
    """
    if failure_classification is None:
        return
    if failure_classification.claim_id != claim.id:
        raise KernelValidationError(
            "failure_classification claim_id does not match locked claim"
        )
    claims_refuted = any(
        item.category.value == "claim_refuted"
        for item in failure_classification.classifications
    )
    if claims_refuted and not _has_admissible_counterexample(counterexamples):
        raise KernelValidationError(
            "failure_classification marks claim_refuted without an admissible "
            "all-hypotheses counterexample"
        )


def _validate_refuted_grade_admissible(
    idea_grade: IdeaGrade,
    counterexamples: CounterexampleReport,
) -> None:
    """A claim may be graded ``refuted`` ONLY when an admissible checked
    counterexample exists: a counterexample that satisfies every claim hypothesis
    AND refutes the claim (``refutes_claim`` already requires a checked ``failed``
    result and forbids dropped-hypothesis / proof-step-only cases at the model
    level). This makes ``refuted`` a kernel-earned status, not something model
    prose, a reviewer objection, memory, or a forged grade can assert -- and it
    retroactively hardens the subgoal-graph ``contradicted`` gate, which trusts
    ``idea_grade.status == refuted``.
    """
    if idea_grade.status != IdeaStatus.refuted:
        return
    if not _has_admissible_counterexample(counterexamples):
        raise KernelValidationError(
            "idea_grade.status=refuted requires an admissible checked counterexample "
            "(satisfies every claim hypothesis and refutes the claim); a route attack, "
            "reviewer objection, dropped-hypothesis example, or unchecked counterexample "
            "cannot refute the claim"
        )


def _load_optional_mathematical_judgment(
    run_dir: Path,
    manifest: RunManifest,
) -> MathematicalJudgmentReport | None:
    optional = manifest.optional_artifacts
    if "mathematical_judgment" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["mathematical_judgment"],
        MathematicalJudgmentReport,
    )


def _validate_mathematical_judgment(
    mathematical_judgment: MathematicalJudgmentReport | None,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    """The mathematical judgment layer is advisory; it must never upgrade status.

    Only runs when the optional artifact is present. The report model carries no
    source/evidence/proof status field and StrictBaseModel forbids extras, so a
    YAML that smuggles in ``source_backed`` / checked evidence / a proof status is
    already rejected at load (validate_yaml_model). Here we additionally bind the
    report to this run: claim_id must match the locked claim and run_id must match
    the manifest, so a judgment for another claim/run cannot be attached here.
    """
    if mathematical_judgment is None:
        return
    if mathematical_judgment.claim_id != claim.id:
        raise KernelValidationError(
            "mathematical_judgment claim_id does not match locked claim"
        )
    if mathematical_judgment.run_id != manifest.run_id:
        raise KernelValidationError(
            "mathematical_judgment run_id does not match the run manifest"
        )


def _load_optional_failure_pattern_audit(
    run_dir: Path,
    manifest: RunManifest,
) -> FailurePatternAudit | None:
    optional = manifest.optional_artifacts
    if "failure_pattern_audit" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["failure_pattern_audit"],
        FailurePatternAudit,
    )


def _validate_failure_pattern_audit(
    failure_pattern_audit: FailurePatternAudit | None,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    """The failure-pattern audit is advisory; it must not relabel or upgrade.

    Only runs when the optional artifact is present. Rules (fail closed):

    - claim_id / run_id must bind to this run;
    - every matched pattern_id must exist in the canonical SEED bank, and its
      tier / action / family / enforced_by must equal the bank verbatim -- so a
      run (or memory, or a human edit) can NEVER promote a candidate pattern to
      ``hard`` or change what it is allowed to do;
    - a ``hard`` match must point at an EXISTING kernel validator
      (``enforced_by`` in ENFORCING_VALIDATORS); it adds no new blocking.

    The audit model carries no source/evidence/proof status field (and extras are
    forbidden), so a match can never upgrade any status or refute the claim.
    """
    if failure_pattern_audit is None:
        return
    # Local import: failure_pattern_bank imports models+io only (no cycle).
    from espresso_proof.kernel.failure_pattern_bank import ENFORCING_VALIDATORS, seed_failure_pattern_bank

    if failure_pattern_audit.claim_id != claim.id:
        raise KernelValidationError(
            "failure_pattern_audit claim_id does not match locked claim"
        )
    if failure_pattern_audit.run_id != manifest.run_id:
        raise KernelValidationError(
            "failure_pattern_audit run_id does not match the run manifest"
        )
    canonical = {p.id: p for p in seed_failure_pattern_bank().patterns}
    for match in failure_pattern_audit.matches:
        pattern = canonical.get(match.pattern_id)
        if pattern is None:
            raise KernelValidationError(
                f"failure_pattern_audit references unknown pattern {match.pattern_id!r}"
            )
        if (
            match.tier != pattern.tier
            or match.action != pattern.action
            or match.family != pattern.family
            or match.enforced_by != pattern.enforced_by
        ):
            raise KernelValidationError(
                f"failure_pattern_audit relabels pattern {match.pattern_id!r} "
                "(tier/action/family/enforced_by must equal the canonical bank)"
            )
        if match.tier.value == "hard" and match.enforced_by not in ENFORCING_VALIDATORS:
            raise KernelValidationError(
                f"hard pattern {match.pattern_id!r} must be enforced by an existing "
                "kernel validator"
            )


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


def _load_optional_proof_verification(
    run_dir: Path,
    manifest: RunManifest,
) -> ProofVerificationReport | None:
    optional = manifest.optional_artifacts
    if "proof_verification" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["proof_verification"],
        ProofVerificationReport,
    )


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


# verifier_type -> the only checked status it may earn (with a confirming result).
_TRUSTED_EARNED = {
    "finite_group_cayley": "instance_checked",
    "finite_identity_table": "instance_checked",
    "finite_ring_identity": "instance_checked",
    "matrix_rank": "instance_checked",
    "exact_computation": "computation_checked",
}


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
    - ``human_attested`` requires ``verifier_type == human_checked`` and is never
      a machine-checked status;
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
            if not (entry.trusted and entry.confirmed):
                raise KernelValidationError(
                    f"proof_verification entry {entry.id!r} claims {earned} without a "
                    "trusted + confirming verifier result"
                )
            if _TRUSTED_EARNED.get(vt) != earned:
                raise KernelValidationError(
                    f"proof_verification entry {entry.id!r}: verifier_type {vt!r} cannot "
                    f"earn {earned!r}"
                )
        elif earned == "human_attested":
            if vt != "human_checked":
                raise KernelValidationError(
                    f"proof_verification entry {entry.id!r}: human_attested requires "
                    "verifier_type=human_checked"
                )
        # earned == unverified is always acceptable (fail-closed default)
        if vt == "lean_bridge" and earned != "unverified":
            raise KernelValidationError(
                f"proof_verification entry {entry.id!r}: lean_bridge cannot earn {earned!r} in V0"
            )


def _validate_grade_proof_language_backed(
    idea_grade: IdeaGrade,
    source_grounding: SourceGroundingReport,
    computation_verification: ComputationVerificationReport | None,
    proof_verification: ProofVerificationReport | None,
    agent_consensus_review: AgentConsensusReview | None = None,
) -> None:
    """A "verified-proof" grade status must be backed by a real trusted result.

    ``idea_grade.proof_language_status`` renders as "A proof has been checked by a
    trusted verifier", so the VERIFIED statuses cannot be asserted from prose or a
    forged grade:

    - ``source_checked_argument`` requires at least one source-grounding step
      that actually earned ``source_backed``. This remains step-local reference
      support, not a checked proof.
    - ``computation_checked`` requires an actual confirming MACHINE result -- a
      trusted+confirming proof-verification entry (computation/instance checked)
      or a confirmed computation-verification result. A scoped/finite computation
      that genuinely backs the grade therefore stays scoped (it earned its entry);
      a grade that merely *claims* computation_checked with no machine result is
      rejected. This is the "no global proof status from scoped computation" gate.
    - ``lean_checked`` / ``human_confirmed`` are NOT earnable in V0: Espresso has
      no Lean/global-proof lane and no human-proof-attestation artifact (and human
      feedback may never upgrade proof status). Fail closed.
    """
    status = idea_grade.proof_language_status
    if status == ProofLanguageStatus.agent_consensus_mock_checked:
        # Agent-consensus mock-check: amber/advisory, NOT machine-verified. Requires
        # a unanimous adversarial consensus artifact; reviewer prose alone cannot
        # upgrade the proof language. The hash binding + cross-checks live in
        # _validate_agent_consensus_review.
        if agent_consensus_review is None or not agent_consensus_review.consensus:
            raise KernelValidationError(
                "idea_grade.proof_language_status=agent_consensus_mock_checked requires "
                "a unanimous agent_consensus_review (no_fatal_flaw_found from all three "
                "reviewers); reviewer prose cannot upgrade proof language"
            )
    elif status == ProofLanguageStatus.source_checked_argument:
        if not any(
            step.status == SourceStatus.source_backed
            for step in source_grounding.steps
        ):
            raise KernelValidationError(
                "idea_grade.proof_language_status=source_checked_argument requires "
                "at least one earned source-grounding step; reviewer/source prose "
                "cannot upgrade proof language"
            )
    elif status == ProofLanguageStatus.computation_checked:
        machine_confirmed = bool(
            proof_verification
            and any(
                entry.trusted
                and entry.confirmed
                and entry.earned_status
                in {
                    ProofVerificationStatus.computation_checked,
                    ProofVerificationStatus.instance_checked,
                }
                for entry in proof_verification.entries
            )
        ) or bool(
            computation_verification
            and any(
                result.executed and result.verdict == VerificationVerdict.confirmed
                for result in computation_verification.results
            )
        )
        if not machine_confirmed:
            raise KernelValidationError(
                "idea_grade.proof_language_status=computation_checked requires a confirming "
                "trusted machine result (a proof-verification or computation-verification "
                "entry); a scoped/finite computation cannot upgrade the grade to a global "
                "checked proof without one"
            )
    elif status in {
        ProofLanguageStatus.lean_checked,
        ProofLanguageStatus.human_confirmed,
    }:
        raise KernelValidationError(
            f"idea_grade.proof_language_status={status.value} is not earnable in V0: "
            "Espresso has no Lean/global-proof lane and no human-proof-attestation "
            "artifact (human feedback cannot upgrade proof status)"
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
    we additionally scan every reviewer-authored text field (which Slice C renders)
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


def _validate_agent_consensus_review(
    consensus: AgentConsensusReview | None,
    review: Review,
    idea_grade: IdeaGrade,
    manifest: RunManifest,
    run_dir: Path,
) -> None:
    """Provenance gate for the agent-consensus mock-check (fail-closed).

    The status is amber/advisory, but it DOES occupy a ``proof_language_status``
    slot, so it earns the same fail-closed provenance treatment as
    ``source_checked_argument``: the consensus must be a unanimous adversarial pass
    by the ACTUAL three reviewers of THIS run, hash-bound to the current
    claim/source/review/prover_loop, and it is revoked the moment any of those
    change. The kernel never computes the agents' judgement (that is the whole point
    of an explicitly agent-consensus tier) — it enforces that a structurally valid,
    current, unanimous consensus exists and that no stronger/contradicting signal
    coexists.
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

    # 1. Reviewer cross-check: the consensus reviewers must be exactly the reviewers
    #    that actually reviewed this run (no citing reviewers who did not review it).
    consensus_ids = {entry.reviewer_id for entry in consensus.entries}
    review_ids = {reviewer.reviewer_id for reviewer in review.reviewers}
    if consensus_ids != review_ids:
        raise KernelValidationError(
            "agent_consensus_review reviewers must match the run's Review reviewers"
        )

    # 2. A reviewer that FAILED the standard review cannot claim no_fatal_flaw here.
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

    # 3. Hash binding (revoke on change): the bound_* hashes must equal the CURRENT
    #    artifact hashes, so a stale consensus cannot ride a modified proof.
    artifacts = manifest.artifacts
    prover_loop_rel = manifest.optional_artifacts.get("prover_loop")
    expected = {
        "bound_claim_hash": artifact_revision_hash(run_dir, artifacts["locked_claim"]),
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

    # 4. The status path requires a unanimous consensus (belt; model enforces the
    #    consensus<->entries consistency).
    if not consensus.consensus:
        raise KernelValidationError(
            "agent_consensus_mock_checked requires a unanimous no_fatal_flaw_found "
            "consensus from all three reviewers"
        )

    # 5. A refuted claim can never carry a mock-checked proof.
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

    # Goal 57A cap-aware ledger/graph id consistency. The theorem-condition ledger
    # is the SINGLE WRITER of ``subgoal_tcl_*`` repair nodes (their ids come from a
    # ledger entry's ``repair_subgoal_id``). When BOTH artifacts exist, every such
    # node in the graph MUST correspond to a real ledger entry — this catches a
    # hallucinated / stale ledger repair node smuggled into the graph.
    #
    # CAP-SAFE (does NOT break under _MAX_LEDGER_SUBGOALS): the builder caps how
    # many ledger entries become graph nodes, so the graph only ever holds a SUBSET
    # of the ledger's repair ids — never an extra one. We therefore check
    # "every graph tcl node ⊆ ledger ids", NOT "every ledger entry has a node"
    # (the latter would falsely fail a capped graph). Goal 54B's advisory
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


def _validate_source_discipline_bindings(
    run_dir: Path,
    manifest: RunManifest,
    source_grounding: SourceGroundingReport,
    theorem_extraction: TheoremExtractionReport | None,
    theorem_applicability: TheoremApplicabilityReport | None,
) -> None:
    """Goal 41 provenance bindings (optional/additive — both fields default "").

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


def _normalize_source_text(text: str) -> str:
    return " ".join(str(text).split())


def _locator_tuple(locator) -> tuple[str, str]:
    kind = locator.kind.value if hasattr(locator.kind, "value") else str(locator.kind)
    return kind, str(locator.value).strip()


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


def _prover_status_smuggling_field(text: str) -> str | None:
    for clause in _prover_assertion_clauses(text):
        for field in (
            "proof_status",
            "proof_language_status",
            "agent_consensus_mock_checked",
            "agent_consensus",
            "mock_checked",
            "source_status",
            "source_backed_status",
            "source_backed",
            "evidence_status",
            "checked_evidence",
            "theorem_applicability",
            "theorem_applicability_status",
            "applicability_status",
            "refutation_status",
            "counterexample_refutes_claim",
            "refutes_claim",
            "grade_status",
        ):
            if re.search(rf"\b{re.escape(field)}\s*[:=]", clause):
                return field
        owner_status_patterns = [
            (r"\bproof[_ -]?language[_ -]?status\s+(?:is|are|was|were)\s+[\w -]+", "proof_language_status"),
            (r"\bproof\s+status\s+(?:is|are|was|were)\s+[\w -]+", "proof_status"),
            (r"\bsource[_ -]?backed[_ -]?status\s+(?:is|are|was|were)\s+[\w -]+", "source_backed_status"),
            (r"\bsource\s+status\s+(?:is|are|was|were)\s+[\w -]+", "source_status"),
            (r"\bevidence\s+status\s+(?:is|are|was|were)\s+[\w -]+", "evidence_status"),
            (r"\bchecked[_ -]?evidence\s+(?:is|are|was|were)\s+[\w -]+", "checked_evidence"),
            (r"\btheorem[_ -]?applicability[_ -]?status\s+(?:is|are|was|were)\s+[\w -]+", "theorem_applicability_status"),
            (r"\btheorem\s+applicability\s+status\s+(?:is|are|was|were)\s+[\w -]+", "theorem_applicability_status"),
            (r"\brefutation\s+status\s+(?:is|are|was|were)\s+[\w -]+", "refutation_status"),
            (r"\bgrade[_ -]?status\s+(?:is|are|was|were)\s+[\w -]+", "grade_status"),
            (r"\bgrade\s+status\s+(?:is|are|was|were)\s+[\w -]+", "grade_status"),
        ]
        for pattern, name in owner_status_patterns:
            if re.search(pattern, clause):
                return name
        if _prover_clause_is_status_boundary_disclaimer(clause):
            continue
        status_patterns = [
            (r"\b(?:is|are|as|becomes|became|counts?\s+as|treats?\s+as)\s+source[_ -]?backed\b", "source_backed"),
            (r"\bsource\s+(?:status\s+)?(?:is|=|:)\s*(?:backed|checked|supported)\b", "source_status"),
            (r"\bchecked[_ -]?evidence\s+(?:supports?|establishes?|proves?|is|are)\b", "checked_evidence"),
            (r"\b(?:is|are|as|counts?\s+as|treats?\s+as)\s+checked[_ -]?evidence\b", "checked_evidence"),
            (r"\bevidence\s+(?:status\s+)?(?:is|=|:)\s*(?:checked|verified)\b", "evidence_status"),
            (r"\btheorem\s+applicability(?:\s+status)?\s+(?:is|=|:)\s*(?:applicable|all[_ ]applicable)\b", "theorem_applicability"),
            (r"\bapplicability[_ ]status\s*(?:is|=|:)\s*(?:applicable|all[_ ]applicable)\b", "theorem_applicability"),
            (r"\ball[_ ]applicable\b", "theorem_applicability"),
            (r"\bexamples?\s+(?:have\s+)?survived\b", "example_status"),
            (r"\bsurvived\s+examples?\b", "example_status"),
            (r"\bno\s+counterexamples?\s+(?:remain|exist|found|pressure)\b", "counterexample_pressure"),
            (r"\bmissing\s+counterexamples?\s+(?:imply|mean|show|prove)\s+no\s+pressure\b", "counterexample_pressure"),
            (r"\bcounterexamples?\s+refutes?\b", "counterexample_refutes_claim"),
            (r"\bcounterexample[_ -]?refutes[_ -]?claim\b", "counterexample_refutes_claim"),
            (r"\b(?:claim|route|statement)\s+is\s+refuted\b", "refutation_status"),
            (r"\brefuted\b", "refutation_status"),
        ]
        for pattern, name in status_patterns:
            if re.search(pattern, clause):
                return name
    return None


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


def _prover_assertion_clauses(text: str) -> list[str]:
    normalized = " ".join(str(text).lower().split())
    rough = re.split(
        r"(?:[.;!?]\s+|,\s+|\n+|\s+\b(?:but|however|while|although|whereas)\b\s+)",
        normalized,
    )
    return [part.strip() for part in rough if part.strip()]


def _prover_clause_is_status_boundary_disclaimer(clause: str) -> bool:
    return any(
        phrase in clause
        for phrase in (
            "does not upgrade",
            "do not upgrade",
            "cannot upgrade",
            "can never upgrade",
            "never upgrades",
            "never upgrade",
            "does not set",
            "cannot set",
            "cannot write",
            "must not upgrade",
            "not proof status",
            "not evidence",
            "not a proof",
            "is not proof",
            "not applicable",
            "not checked",
            "not refuted",
            "advisory only",
            "advisory-only",
            "scheduling-only",
        )
    )


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
            "cannot count",
            "can't count",
            "not evidence",
            "is not proof",
            "not a proof",
        )
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


def _first_matching_pattern(text: str, patterns: dict[str, str]) -> str | None:
    for pattern, phrase in patterns.items():
        if re.search(pattern, text):
            return phrase
    return None


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


def _first_forbidden_proof_language(text: str) -> str | None:
    canonical_phrase = contains_proof_language(text)
    if canonical_phrase:
        return canonical_phrase
    forbidden_patterns = {
        r"\bq\.?e\.?d\.?\b": "proof-close",
        r"\bverified\b": "verified",
        r"\b(?:the\s+)?(?:theorem|claim|result)\s+(?:is|was|has\s+been)\s+settled\b": "settled result",
        r"\bsettled\s+the\s+(?:theorem|claim|result)\b": "settled result",
        r"\bsettles?\s+the\s+(?:theorem|claim|result)\b": "settles result",
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
        r"\b(?:route|strategy)\s+(?:is|are|was|were)\s+(?:a\s+)?(?:proof|proof\s+status|source\s+support|evidence)\b": "route/strategy proof status",
        r"\broute\s+(?:is\s+)?(?:a\s+)?proof\b": "route is a proof",
        r"\broute\s+proves\s+the\s+(?:result|claim|theorem)\b": "route proves the result",
        r"\broute\s+establishes\s+the\s+(?:result|claim|theorem)\b": "route establishes the result",
        r"\b(?:route|strategy)\s+(?:show(?:s|ed|ing)?|demonstrat(?:e|es|ed|ing)|verif(?:y|ies|ied|ying)|validat(?:e|es|ed|ing)|support(?:s|ed|ing)?|certif(?:y|ies|ied|ying)|prove(?:s|d|n|ing)?)\s+(?:the\s+)?(?:theorem|claim|result|proof|proof\s+status|source\s+support|evidence)\b": "route/strategy status projection",
        r"\btheorem\s+applicability\s+(?:proves|establishes|confirms)\b": "theorem applicability proves",
        r"\bapplicability\s+(?:proves|establishes)\s+the\s+(?:route|claim|theorem|result)\b": "applicability proves",
        r"\bestablishes\s+the\s+theorem\b": "establishes the theorem",
        r"\bestablishes\s+the\s+claim\b": "establishes the claim",
        r"\bproof\s+establishes\b": "proof establishes",
        # Goal 41: Chinese global proof-completion markers (no \b — Python's
        # word boundary treats CJK as word chars, so \b would never fire inside
        # Chinese prose). Same curation as the display lexicon.
        r"证毕": "证毕",
        r"证明完毕": "证明完毕",
        r"证明完成": "证明完成",
        r"定理得证": "定理得证",
        r"命题得证": "命题得证",
        r"猜想得证": "猜想得证",
        r"定理已证明": "定理已证明",
        r"命题已证明": "命题已证明",
        r"我们证明了该定理": "我们证明了该定理",
        r"我们证明了这个定理": "我们证明了这个定理",
    }
    for pattern, phrase in forbidden_patterns.items():
        if re.search(pattern, text):
            return phrase
    return None
