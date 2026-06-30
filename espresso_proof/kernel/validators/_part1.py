"""Validators — internal topological slice 1/6 (callees precede callers). Imported via espresso_proof.kernel.validators."""


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


# verifier_type -> the only checked status it may earn (with a confirming result).
_TRUSTED_EARNED = {
    "finite_group_cayley": "instance_checked",
    "finite_identity_table": "instance_checked",
    "finite_ring_identity": "instance_checked",
    "matrix_rank": "instance_checked",
    "exact_computation": "computation_checked",
}


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


def _load_optional_stage_status(
    run_dir: Path,
    manifest: RunManifest,
) -> StageStatusReport | None:
    optional = manifest.optional_artifacts
    if "stage_status" not in optional:
        return None
    if not path_exists(run_dir / optional["stage_status"]):
        raise KernelValidationError("stage_status optional artifact is missing")
    return validate_yaml_model(
        run_dir / optional["stage_status"],
        StageStatusReport,
    )


def _load_optional_pressure_lane_barrier(
    run_dir: Path,
    manifest: RunManifest,
) -> PressureLaneBarrierReport | None:
    optional = manifest.optional_artifacts
    if "pressure_lane_barrier" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["pressure_lane_barrier"],
        PressureLaneBarrierReport,
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


def _contains_forbidden_stage_status_token(value) -> bool:
    forbidden_keys = {
        "proof_status",
        "source_backed",
        "source_status",
        "checked_evidence",
        "example_checked",
        "example_survived",
        "example_failed",
        "counterexample_refutes_claim",
        "refutation_status",
        "theorem_applicability_status",
        "grade_status",
        "human_confirmed",
        "lean_checked",
        "machine_checked",
        "verified",
        "proved",
        "complete_proof",
        "final_status",
        "memory_evidence",
    }
    if isinstance(value, str):
        lowered = value.lower()
        forbidden_phrases = [
            r"\bproved\b",
            r"\bverified\b",
            r"\bproof-close\b",
            r"\bproof\s+complete\b",
            r"\bsource[-_ ]backed\b",
            r"\bchecked\s+evidence\b",
            r"\brefutes\s+claim\b",
            r"\blean[-_ ]checked\b",
            r"\bmachine[-_ ]checked\b",
        ]
        return any(re.search(pattern, lowered) for pattern in forbidden_phrases)
    if isinstance(value, dict):
        return any(
            str(key).lower() in forbidden_keys
            or _contains_forbidden_stage_status_token(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_forbidden_stage_status_token(item) for item in value)
    return False


def _validate_stage_status(
    stage_status: StageStatusReport | None,
    claim: Claim,
    manifest: RunManifest,
    run_dir: Path,
) -> None:
    """Bind AGENT_FLOW process status to the current run and spec.

    The report is optional and advisory. It may make stage progress visible, but
    cannot own or smuggle mathematical trust. This validator therefore checks
    schema shape, spec parity, selected process invariants, and artifact hashes;
    it never promotes or demotes proof/source/evidence/refutation/grade status.
    """

    if "stage_status" in manifest.artifacts:
        raise KernelValidationError("stage_status must be an optional process artifact")
    if stage_status is None:
        return
    if manifest.optional_artifacts.get("stage_status") != "stage_status.yaml":
        raise KernelValidationError("stage_status optional artifact must be stage_status.yaml")
    if stage_status.claim_id != claim.id:
        raise KernelValidationError("stage_status claim_id does not match locked claim")
    if stage_status.run_id != manifest.run_id:
        raise KernelValidationError("stage_status run_id does not match run manifest")
    if not stage_status.advisory_only or stage_status.effect != "process_status_only":
        raise KernelValidationError("stage_status must be process-status-only/advisory")

    artifact_path = run_dir / manifest.optional_artifacts["stage_status"]
    raw = read_yaml(artifact_path)
    if _contains_forbidden_stage_status_token(raw):
        raise KernelValidationError("stage_status cannot write mathematical status fields")

    expected_ids = ordered_stage_ids()
    actual_ids = tuple(entry.stage_id for entry in stage_status.stages)
    if actual_ids != expected_ids:
        raise KernelValidationError("stage_status must cover AGENT_FLOW stages in canonical order")

    expected_id_set = set(expected_ids)
    status_by_id = {entry.stage_id: entry.status for entry in stage_status.stages}
    claim_clear_blocked = any(entry.claim_clear_blocked for entry in stage_status.stages)
    blocked_ids = set(stages_blocked_by_unclear_claim())
    dependency_ready_statuses = {"queued", "running", "done", "failed"}

    for entry in stage_status.stages:
        spec = get_stage(entry.stage_id)
        legacy_mapping = get_legacy_mapping(entry.stage_id)
        mapped_artifacts = set(legacy_mapping.artifacts).union(
            legacy_mapping.optional_artifacts
        )
        for field_name, stage_refs in (
            ("depends_on", entry.depends_on),
            ("reads_from", entry.reads_from),
        ):
            for stage_ref in stage_refs:
                if stage_ref not in expected_id_set:
                    raise KernelValidationError(
                        f"stage_status {entry.stage_id} {field_name} "
                        f"references unknown AGENT_FLOW stage: {stage_ref!r}"
                    )
        if entry.display_name != spec.display_name:
            raise KernelValidationError(f"stage_status {entry.stage_id} display_name is stale")
        if tuple(entry.depends_on) != spec.depends_on:
            raise KernelValidationError(f"stage_status {entry.stage_id} depends_on is stale")
        if tuple(entry.reads_from) != spec.reads_from:
            raise KernelValidationError(f"stage_status {entry.stage_id} reads_from is stale")
        if tuple(entry.outputs) != spec.outputs:
            raise KernelValidationError(f"stage_status {entry.stage_id} outputs are stale")
        if entry.parallel_group != spec.parallel_group:
            raise KernelValidationError(f"stage_status {entry.stage_id} parallel_group is stale")
        if entry.evidence_role != spec.evidence_role:
            raise KernelValidationError(f"stage_status {entry.stage_id} evidence_role is stale")
        if entry.status in dependency_ready_statuses:
            for dependency in entry.depends_on:
                if status_by_id.get(dependency) != "done":
                    raise KernelValidationError(
                        f"stage_status {entry.stage_id} cannot be {entry.status} "
                        f"before dependency {dependency} is done"
                    )
        if entry.claim_clear_blocked and entry.stage_id not in blocked_ids:
            raise KernelValidationError(
                f"stage_status {entry.stage_id} cannot be claim-clear-blocked"
            )
        for rel, recorded_hash in entry.artifact_hashes.items():
            validate_relative_artifact_path(
                f"stage_status.{entry.stage_id}.artifact_hashes",
                rel,
                path_name="stage_status artifact hash path",
            )
            if rel not in mapped_artifacts:
                raise KernelValidationError(
                    f"stage_status {entry.stage_id} artifact hash path is not "
                    f"mapped to the stage: {rel!r}"
                )
            current_hash = artifact_revision_hash(run_dir, rel)
            if recorded_hash == "missing":
                if entry.status == "done":
                    raise KernelValidationError(
                        f"stage_status {entry.stage_id} is done but artifact {rel!r} is missing"
                    )
                if current_hash != "missing":
                    raise KernelValidationError(
                        f"stage_status {entry.stage_id} artifact hash is stale"
                    )
            elif current_hash != recorded_hash:
                raise KernelValidationError(
                    f"stage_status {entry.stage_id} artifact hash is stale"
                )

    if claim_clear_blocked:
        for stage_id in blocked_ids:
            if status_by_id.get(stage_id) in {"queued", "running", "done"}:
                raise KernelValidationError(
                    f"stage_status {stage_id} cannot run while claim-clear is blocked"
                )

    if status_by_id.get("time_allocation") in {"queued", "running", "done"}:
        if status_by_id.get("strategy_grader") != "done":
            raise KernelValidationError(
                "stage_status time_allocation cannot start before strategy_grader is done"
            )


def _validate_pressure_lane_barrier(
    pressure_lane_barrier: PressureLaneBarrierReport | None,
    claim: Claim,
    manifest: RunManifest,
    run_dir: Path,
    agent_run_status: AgentRunStatusReport | None,
) -> None:
    """Bind the explicit pressure-lane barrier to this run.

    The barrier is allowed to be mid-SEED/RUN/JOIN so a crashed batch can be
    resumed. Only terminal claims are cross-checked against the agent board:
    ``committed`` must mean the validated single-agent path already marked the
    lane done, and ``failed`` must not coexist with a done board entry.
    """

    if "pressure_lane_barrier" in manifest.artifacts:
        raise KernelValidationError(
            "pressure_lane_barrier must be an optional process artifact"
        )
    if pressure_lane_barrier is None:
        return
    if (
        manifest.optional_artifacts.get("pressure_lane_barrier")
        != PRESSURE_LANE_BARRIER_FILENAME
    ):
        raise KernelValidationError(
            "pressure_lane_barrier optional artifact must be pressure_lane_barrier.yaml"
        )
    if pressure_lane_barrier.claim_id != claim.id:
        raise KernelValidationError(
            "pressure_lane_barrier claim_id does not match locked claim"
        )
    if pressure_lane_barrier.run_id != manifest.run_id:
        raise KernelValidationError(
            "pressure_lane_barrier run_id does not match run manifest"
        )
    if (
        not pressure_lane_barrier.advisory_only
        or pressure_lane_barrier.effect != "scheduling_only"
        or pressure_lane_barrier.mode != PRESSURE_LANE_BARRIER_MODE
    ):
        raise KernelValidationError(
            "pressure_lane_barrier must be scheduling-only/advisory"
        )

    artifact_path = run_dir / manifest.optional_artifacts["pressure_lane_barrier"]
    raw = read_yaml(artifact_path)
    if _contains_forbidden_stage_status_token(raw):
        raise KernelValidationError(
            "pressure_lane_barrier cannot write mathematical status fields"
        )

    requested = list(pressure_lane_barrier.requested_agents)
    expected_order = order_for_pressure_lane_barrier(set(requested))
    if list(pressure_lane_barrier.commit_order) != expected_order:
        raise KernelValidationError(
            "pressure_lane_barrier commit_order is not deterministic"
        )

    board_statuses = (
        {entry.agent: entry.status for entry in agent_run_status.agents}
        if agent_run_status is not None
        else {}
    )
    entries_by_agent = {entry.agent: entry for entry in pressure_lane_barrier.entries}
    for agent in requested:
        entry = entries_by_agent[agent]
        if entry.stage_id != PRESSURE_LANE_BARRIER_AGENT_STAGE_IDS[agent]:
            raise KernelValidationError(
                f"pressure_lane_barrier {agent} stage_id is stale"
            )
        if entry.role != PRESSURE_LANE_BARRIER_AGENT_ROLES[agent]:
            raise KernelValidationError(
                f"pressure_lane_barrier {agent} role is stale"
            )
        if entry.worker_shared_write or entry.candidate_persisted:
            raise KernelValidationError(
                "pressure_lane_barrier workers cannot claim shared writes or persisted candidates"
            )
        board_status = board_statuses.get(agent)
        if entry.status == "committed" and board_status not in {None, "done"}:
            raise KernelValidationError(
                f"pressure_lane_barrier {agent} committed but agent board is {board_status}"
            )
        if entry.status == "failed" and board_status == "done":
            raise KernelValidationError(
                f"pressure_lane_barrier {agent} failed but agent board is done"
            )


__all__ = [
    '_TRUSTED_EARNED',
    '_adapt_legacy_memory_context',
    '_contains_forbidden_stage_status_token',
    '_expected_pressure_state',
    '_legacy_memory_context_provenance',
    '_load_optional_agent_run_status',
    '_load_optional_pressure_lane_barrier',
    '_load_optional_pressure_status',
    '_load_optional_stage_status',
    '_load_optional_time_allocation',
    '_load_optional_update_inbox',
    '_memory_context_schema_version',
    '_validate_agent_run_status',
    '_validate_pressure_counts',
    '_validate_pressure_lane_barrier',
    '_validate_pressure_status',
    '_validate_stage_status',
    'validate_yaml_model',
]
