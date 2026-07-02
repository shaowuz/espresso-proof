"""[COMPAT · reference data] Map from AGENT_FLOW concepts to today's runtime surfaces.

Read-only mapping/reference data: it runs no agents, writes no run artifacts, and
infers no mathematical trust. Note: the stage-order constants it defines (e.g.
``LEGACY_FRONT_HALF_FAST_ORDER``) ARE consumed by the orchestrator's fast-core
plan, so this module is reference data, not dead code.
The executable runtime dataflow lives in espresso_proof.orchestration.orchestrator.


This module is deliberately read-only. It records how each conceptual
AGENT_FLOW stage maps onto the current coarse front-half stages, orchestrator
steps, optional artifacts, and advisory sidecars. It must not run agents, write
run artifacts, or infer mathematical trust.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from espresso_proof.kernel.models import RunState
from espresso_proof.orchestration.agent_flow_spec import get_stage, ordered_stage_ids
from espresso_proof.validation.manifest import validate_relative_artifact_path

ImplementationStatus = Literal[
    "implemented",
    "implemented_coarse",
    "partial",
    "docs_only",
    "dev_test_only",
    "implemented_advisory",
]

RuntimeMappingKind = Literal[
    "direct_runtime",
    "coarse_runtime",
    "synthetic_from_existing_artifacts",
    "optional_runtime",
    "pending_future_work",
    "dev_test_only",
]

IMPLEMENTATION_STATUSES: tuple[str, ...] = (
    "implemented",
    "implemented_coarse",
    "partial",
    "docs_only",
    "dev_test_only",
    "implemented_advisory",
)

RUNTIME_MAPPING_KINDS: tuple[str, ...] = (
    "direct_runtime",
    "coarse_runtime",
    "synthetic_from_existing_artifacts",
    "optional_runtime",
    "pending_future_work",
    "dev_test_only",
)

PHYSICAL_STAGE_NAMESPACES: tuple[str, ...] = (
    "agents",
    "claim",
    "cli",
    "formal",
    "front_half",
    "kernel",
    "literature",
    "llm_agents",
    "memory",
    "mock_pipeline",
    "orchestration",
    "orchestrator",
    "run-agent",
    "server",
    "strategy",
    "ui",
    "verification",
)

RUN_MANIFEST_STATES: tuple[str, ...] = tuple(state.value for state in RunState)


@dataclass(frozen=True)
class AgentFlowLegacyMapping:
    """One conceptual stage's current compatibility mapping."""

    stage_id: str
    implementation_status: ImplementationStatus
    runtime_kind: RuntimeMappingKind
    physical_stages: tuple[str, ...] = ()
    front_half_stages: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    optional_artifacts: tuple[str, ...] = ()
    manifest_states: tuple[str, ...] = ()
    pending_work: tuple[str, ...] = ()
    blocked_by_future_work: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


LEGACY_FRONT_HALF_FULL_ORDER: tuple[str, ...] = (
    "understanding_claim",
    "literature_review",
    "examples",
    "counterexamples",
    "source_theorem",
    "check_generator",
    "regrade",
    "strategy",
    "attacker",
    "grade_memory_memo",
)

LEGACY_FRONT_HALF_FAST_ORDER: tuple[str, ...] = (
    "understanding_claim",
    "source_theorem",
    "regrade",
    "strategy",
    "grade_memory_memo",
)

LEGACY_MAPPING: tuple[AgentFlowLegacyMapping, ...] = (
    AgentFlowLegacyMapping(
        stage_id="scaffold",
        implementation_status="implemented_coarse",
        runtime_kind="coarse_runtime",
        physical_stages=(
            "orchestrator:scaffold",
            "orchestration:agent_flow_driver",
            "mock_pipeline:run_mock_critique",
        ),
        artifacts=("run_manifest.yaml", "input.md"),
        manifest_states=("SCAFFOLDED",),
        notes=(
            "Creates a valid coarse run shell.",
            "Compatibility driver derives the runtime plan/status projection; it is process-only.",
            "stage_status.yaml projects this as the entry process row; it is not mathematical progress.",
        ),
    ),
    AgentFlowLegacyMapping(
        stage_id="understanding_claim",
        implementation_status="implemented",
        runtime_kind="direct_runtime",
        physical_stages=("front_half:understanding_claim",),
        front_half_stages=("understanding_claim",),
        artifacts=("locked_claim.yaml", "hypothesis_ledger.yaml"),
        notes=("Preserves quantifiers and keeps human_confirmed false.",),
    ),
    AgentFlowLegacyMapping(
        stage_id="claim_clear_gate",
        implementation_status="implemented_coarse",
        runtime_kind="coarse_runtime",
        physical_stages=("claim:evaluate_claim_clear", "claim:write_claim_clear_gate"),
        artifacts=("claim_clear_gate.yaml",),
        manifest_states=("CLAIM_CLEAR", "WAITING_CLAIM_CLARIFICATION"),
        notes=(
            "Records the scheduling gate and run-level lifecycle state.",
            "Downstream blocked/skipped process rows are projected through stage_status.yaml.",
            "The manifest lifecycle remains coarse.",
        ),
    ),
    AgentFlowLegacyMapping(
        stage_id="literature_review",
        implementation_status="partial",
        runtime_kind="coarse_runtime",
        physical_stages=(
            "front_half:literature_review",
            "orchestrator:literature",
            "orchestration:pressure_lane_barrier",
        ),
        front_half_stages=("literature_review",),
        artifacts=("literature_review.yaml",),
        optional_artifacts=("pressure_lane_barrier.yaml",),
        pending_work=("Orchestrator-default pressure-lane execution remains disabled.",),
        blocked_by_future_work=("Orchestrator-default pressure-lane execution product gate",),
        notes=(
            "Candidate discovery only; never source-backed.",
            "Explicit batch execution is barrier-backed, scheduling-only, and not orchestrator-default.",
        ),
    ),
    AgentFlowLegacyMapping(
        stage_id="examples",
        implementation_status="partial",
        runtime_kind="coarse_runtime",
        physical_stages=(
            "front_half:examples",
            "run-agent:examples",
            "orchestration:pressure_lane_barrier",
        ),
        front_half_stages=("examples",),
        artifacts=("examples.yaml", "computation_checks.yaml", "tests.yaml"),
        optional_artifacts=("pressure_lane_barrier.yaml",),
        pending_work=("Orchestrator-default pressure-lane execution remains disabled.",),
        blocked_by_future_work=("Orchestrator-default pressure-lane execution product gate",),
        notes=(
            "Pending examples are not survived tests.",
            "Explicit batch execution is barrier-backed, scheduling-only, and not orchestrator-default.",
        ),
    ),
    AgentFlowLegacyMapping(
        stage_id="counterexamples",
        implementation_status="partial",
        runtime_kind="coarse_runtime",
        physical_stages=(
            "front_half:counterexamples",
            "run-agent:counterexamples",
            "orchestration:pressure_lane_barrier",
        ),
        front_half_stages=("counterexamples",),
        artifacts=("counterexamples.yaml", "tests.yaml"),
        optional_artifacts=("pressure_lane_barrier.yaml",),
        pending_work=("Orchestrator-default pressure-lane execution remains disabled.",),
        blocked_by_future_work=("Orchestrator-default pressure-lane execution product gate",),
        notes=(
            "Pending counterexamples are not failed tests or no-pressure evidence.",
            "Explicit batch execution is barrier-backed, scheduling-only, and not orchestrator-default.",
        ),
    ),
    AgentFlowLegacyMapping(
        stage_id="counterexample_admissibility",
        implementation_status="partial",
        runtime_kind="synthetic_from_existing_artifacts",
        physical_stages=("front_half:counterexamples", "kernel:counterexample_validators"),
        front_half_stages=("counterexamples",),
        artifacts=("counterexamples.yaml", "tests.yaml"),
        pending_work=("Dedicated admissibility artifact/status row remains future work.",),
        notes=("Named conceptual stage, currently folded into counterexample/test artifacts.",),
    ),
    AgentFlowLegacyMapping(
        stage_id="source_theorem_candidate",
        implementation_status="implemented_coarse",
        runtime_kind="coarse_runtime",
        physical_stages=(
            "front_half:source_theorem",
            "front_half:check_generator",
            "literature:source_grounding_stage",
            "verification:verification_request_probes",
            "verification:source_verification",
            "verification:theorem_applicability",
        ),
        front_half_stages=("source_theorem", "check_generator"),
        artifacts=("source_grounding.yaml", "computation_checks.yaml"),
        optional_artifacts=("theorem_applicability.yaml", "scoped_verification_requests.yaml"),
        pending_work=("Dedicated route-level source/theorem applicability validation is still split across artifacts.",),
        notes=(
            "Candidate-only source/theorem pass; source grounding and theorem applicability stay validator-gated.",
            "Scoped-verification request probes are structured requests only; checked results are earned later by the proof_verification bridge within their finite/computational scope.",
            "Scoped checks remain limited.",
        ),
    ),
    AgentFlowLegacyMapping(
        stage_id="conservative_regrade",
        implementation_status="partial",
        runtime_kind="coarse_runtime",
        physical_stages=("front_half:regrade",),
        front_half_stages=("regrade",),
        artifacts=("idea_grade.yaml", "loop_control.yaml", "review.yaml"),
        pending_work=(
            "Dedicated pre-strategy regrade artifact separated from post-review grade remains future work.",
        ),
        notes=(
            "Pre-strategy conservative refresh remains coarse.",
            "Pending pressure lanes are visible through pressure_lanes/stage_status and must stay pending, not survived/no-pressure evidence.",
        ),
    ),
    AgentFlowLegacyMapping(
        stage_id="strategy_generator",
        implementation_status="implemented_coarse",
        runtime_kind="coarse_runtime",
        physical_stages=("strategy:generation", "front_half:strategy"),
        front_half_stages=("strategy",),
        artifacts=("strategy_synthesis.yaml",),
        pending_work=("Dedicated generated-routes artifact remains future work.",),
        notes=("Service boundary exists; legacy artifact is still shared with grader output.",),
    ),
    AgentFlowLegacyMapping(
        stage_id="examples_counterexamples_attacker",
        implementation_status="partial",
        runtime_kind="coarse_runtime",
        physical_stages=("front_half:attacker",),
        front_half_stages=("attacker",),
        artifacts=("tests.yaml",),
        pending_work=(
            "Dedicated example_counterexample_attacks.yaml artifact remains future work.",
        ),
        notes=("Current attacker pressure is coarse and mostly folded into tests.yaml.",),
    ),
    AgentFlowLegacyMapping(
        stage_id="strategy_grader",
        implementation_status="implemented_coarse",
        runtime_kind="coarse_runtime",
        physical_stages=("strategy:grading", "strategy:time_allocation", "front_half:strategy"),
        front_half_stages=("strategy",),
        artifacts=("strategy_synthesis.yaml", "strategy_brief.yaml"),
        optional_artifacts=("time_allocation.yaml",),
        pending_work=("Dedicated strategy_grades.yaml remains future work.",),
        notes=("First allocation trigger is explicit; route-grade artifact remains legacy-folded.",),
    ),
    AgentFlowLegacyMapping(
        stage_id="strategy_brief",
        implementation_status="implemented",
        runtime_kind="coarse_runtime",
        physical_stages=("strategy:brief", "front_half:strategy"),
        front_half_stages=("strategy",),
        artifacts=("strategy_brief.yaml",),
        notes=("Dedicated service constructs the legacy strategy_brief artifact.",),
    ),
    AgentFlowLegacyMapping(
        stage_id="human_strategy_preference",
        implementation_status="implemented",
        runtime_kind="direct_runtime",
        physical_stages=(
            "orchestrator:resume_run",
            "orchestrator:auto_continue",
            "cli:choose-strategy",
            "server:choice",
        ),
        artifacts=("human_choice.yaml",),
        manifest_states=(
            "HUMAN_CHOICE_CONTINUE_SELECTED",
            "HUMAN_CHOICE_REVISE_SELECTED",
            "HUMAN_CHOICE_STOP_SELECTED",
            # honest auto peer: machinery picked the continue route
            # (selected_by='auto', human_confirmed=false); never HUMAN_*.
            "AUTO_CHOICE_CONTINUE_SELECTED",
        ),
        notes=(
            "Human choice steers route/budget only; it is not evidence.",
            "auto_continue records the same choice with selected_by='auto'; "
            "it can never claim human confirmation.",
        ),
    ),
    AgentFlowLegacyMapping(
        stage_id="time_allocation",
        implementation_status="implemented_coarse",
        runtime_kind="optional_runtime",
        physical_stages=(
            "strategy:time_allocation",
            "kernel:time_allocation",
            "orchestration:prover_loop_driver",
        ),
        artifacts=("prover_loop.yaml", "loop_control.yaml"),
        optional_artifacts=("time_allocation.yaml",),
        pending_work=("Deeper prover-loop extraction remains future work.",),
        notes=("Strategy-facing service boundary exists; kernel model/writer remains shared.",),
    ),
    AgentFlowLegacyMapping(
        stage_id="update_inbox",
        implementation_status="implemented_coarse",
        runtime_kind="optional_runtime",
        physical_stages=(
            "orchestration:update_inbox_service",
            "kernel:update_inbox",
            "llm_agents:reviewer_blocker_routing",
        ),
        optional_artifacts=("update_inbox.yaml",),
        pending_work=("Deeper prover-loop extraction remains future work.",),
        notes=(
            "Orchestration service boundary rejects forged, stale, and self-originating scheduling events.",
            "applied_at means consumed by scheduling, not resolved.",
        ),
    ),
    AgentFlowLegacyMapping(
        stage_id="prover",
        implementation_status="implemented_coarse",
        runtime_kind="optional_runtime",
        physical_stages=(
            "orchestrator:llm_prover_reviewers",
            "agents:llm_agents",
            "verification:proof_verification",
            "formal:statement_lock",
            "formal:lean_bridge",
        ),
        optional_artifacts=(
            "llm_agent_run.yaml",
            "prover_loop.yaml",
            "prover_rounds/index.yaml",
            "proof_verification.yaml",
            "statement_lock.yaml",
        ),
        manifest_states=("WAITING_LLM_AGENT_HUMAN_GATE",),
        notes=(
            "Live-capable proof candidate path; cannot declare proof completion.",
            "proof_verification.yaml may record scoped finite/computational checks, not a universal proof.",
            "Optional Lean bridge results append through proof_verification.yaml but remain unverified in PR70J; lean_checked is not earnable.",
            "Statement locks are equivalence gates for optional formal checks, not proof evidence.",
        ),
    ),
    AgentFlowLegacyMapping(
        stage_id="reviewers",
        implementation_status="implemented_coarse",
        runtime_kind="optional_runtime",
        physical_stages=("orchestrator:llm_prover_reviewers", "agents:llm_agents"),
        artifacts=("review.yaml",),
        optional_artifacts=("prover_rounds/index.yaml",),
        notes=("Reviewer approval remains advisory.",),
    ),
    AgentFlowLegacyMapping(
        stage_id="repair_target",
        implementation_status="implemented_advisory",
        runtime_kind="optional_runtime",
        physical_stages=("orchestration:repair_target", "llm_agents:append_prover_loop_round"),
        optional_artifacts=("repair_target.yaml",),
        notes=("Snapshot-excluded advisory sidecar; not a manifest trust surface.",),
    ),
    AgentFlowLegacyMapping(
        stage_id="line_numbered_proof_writer",
        implementation_status="implemented_advisory",
        runtime_kind="optional_runtime",
        physical_stages=("orchestration:proof_writer", "cli:write-proof"),
        optional_artifacts=("line_numbered_proof.yaml",),
        notes=("Only structured proof-shaped artifact that agent consensus can review.",),
    ),
    AgentFlowLegacyMapping(
        stage_id="agent_consensus_seal",
        implementation_status="implemented_advisory",
        runtime_kind="optional_runtime",
        physical_stages=("orchestration:agent_consensus_seal", "cli:seal-consensus"),
        artifacts=("idea_grade.yaml", "final_report.md"),
        optional_artifacts=("agent_consensus_review.yaml",),
        notes=("Amber advisory seal; hash-bound and not a machine/source check.",),
    ),
    AgentFlowLegacyMapping(
        stage_id="failure_classifier",
        implementation_status="implemented_coarse",
        runtime_kind="optional_runtime",
        physical_stages=("orchestration:failure_classifier", "kernel:failure_pattern_bank"),
        artifacts=("loop_control.yaml",),
        optional_artifacts=("failure_pattern_audit.yaml",),
        notes=("Typed routing and audit hints, not proof status.",),
    ),
    AgentFlowLegacyMapping(
        stage_id="grade",
        implementation_status="implemented_coarse",
        runtime_kind="coarse_runtime",
        physical_stages=("front_half:regrade", "front_half:grade_memory_memo"),
        front_half_stages=("regrade", "grade_memory_memo"),
        artifacts=("idea_grade.yaml",),
        notes=("Worth-continuing grade, not theorem verification.",),
    ),
    AgentFlowLegacyMapping(
        stage_id="concise_answer",
        implementation_status="implemented_coarse",
        runtime_kind="coarse_runtime",
        physical_stages=(
            "front_half:grade_memory_memo",
            "orchestration:agent_flow_status",
            "ui:memo",
            "ui:stage_trace_panel",
        ),
        front_half_stages=("grade_memory_memo",),
        artifacts=("final_report.md",),
        optional_artifacts=("stage_status.yaml",),
        notes=(
            "Compact memo exists.",
            "Expandable AGENT_FLOW trace is a display-safe UI surface backed by stage_status.yaml.",
            "stage_status.yaml remains run-level process metadata, not final-answer evidence.",
        ),
    ),
    AgentFlowLegacyMapping(
        stage_id="memory",
        implementation_status="partial",
        runtime_kind="coarse_runtime",
        physical_stages=("front_half:grade_memory_memo", "memory:write_run_memory"),
        front_half_stages=("grade_memory_memo",),
        artifacts=("memory/index.yaml",),
        optional_artifacts=("memory_context.yaml",),
        pending_work=("Prompt-time memory consumption remains planned/limited.",),
        notes=("Advisory memory only; not evidence.",),
    ),
)

_LEGACY_BY_STAGE_ID: dict[str, AgentFlowLegacyMapping] = {
    mapping.stage_id: mapping for mapping in LEGACY_MAPPING
}


def get_legacy_mapping(stage_id: str) -> AgentFlowLegacyMapping:
    """Return one legacy mapping by conceptual AGENT_FLOW stage id."""

    return _LEGACY_BY_STAGE_ID[stage_id]


def mapped_stage_ids() -> tuple[str, ...]:
    """Return mapped conceptual stage ids in AGENT_FLOW order."""

    return tuple(mapping.stage_id for mapping in LEGACY_MAPPING)


def conceptual_stage_ids_for_front_half_stage(front_half_stage: str) -> tuple[str, ...]:
    """Return conceptual stage ids currently touched by one front-half stage."""

    return tuple(
        mapping.stage_id for mapping in LEGACY_MAPPING if front_half_stage in mapping.front_half_stages
    )


def conceptual_stage_ids_for_front_half_plan(
    front_half_plan: Iterable[str],
) -> tuple[str, ...]:
    """Return conceptual stage ids touched by one front-half runtime plan.

    The output preserves AGENT_FLOW/legacy-map order, not runtime event order.
    This is a parity view only: it does not imply that partially mapped
    conceptual stages are fully implemented.
    """

    plan = tuple(front_half_plan)
    unknown = tuple(stage for stage in plan if stage not in LEGACY_FRONT_HALF_FULL_ORDER)
    if unknown:
        raise ValueError(f"unknown front-half plan stage(s): {unknown}")
    selected = set(plan)
    return tuple(
        mapping.stage_id
        for mapping in LEGACY_MAPPING
        if any(stage in selected for stage in mapping.front_half_stages)
    )


def front_half_stages_in_legacy_map() -> tuple[str, ...]:
    """Return front-half stage ids referenced by the mapping in legacy order."""

    seen: list[str] = []
    for mapping in LEGACY_MAPPING:
        for stage in mapping.front_half_stages:
            if stage not in seen:
                seen.append(stage)
    return tuple(seen)


def physical_stage_namespaces() -> tuple[str, ...]:
    """Return physical-stage namespaces referenced by the compatibility map."""

    seen: list[str] = []
    for mapping in LEGACY_MAPPING:
        for physical_stage in mapping.physical_stages:
            namespace, _, _ = physical_stage.partition(":")
            if namespace and namespace not in seen:
                seen.append(namespace)
    return tuple(seen)


def physical_stages_in_legacy_map() -> tuple[str, ...]:
    """Return physical-stage surfaces referenced by the map in first-seen order."""

    seen: list[str] = []
    for mapping in LEGACY_MAPPING:
        for physical_stage in mapping.physical_stages:
            if physical_stage not in seen:
                seen.append(physical_stage)
    return tuple(seen)


def manifest_states_in_legacy_map() -> tuple[str, ...]:
    """Return manifest current_state values referenced by the compatibility map."""

    seen: list[str] = []
    for mapping in LEGACY_MAPPING:
        for state in mapping.manifest_states:
            if state not in seen:
                seen.append(state)
    return tuple(seen)


def stage_ids_for_manifest_state(manifest_state: str) -> tuple[str, ...]:
    """Return conceptual stage ids currently associated with one manifest state."""

    return tuple(
        mapping.stage_id
        for mapping in LEGACY_MAPPING
        if manifest_state in mapping.manifest_states
    )


def stage_ids_for_physical_stage(physical_stage: str) -> tuple[str, ...]:
    """Return conceptual stage ids currently mapped to one physical surface."""

    return tuple(
        mapping.stage_id
        for mapping in LEGACY_MAPPING
        if physical_stage in mapping.physical_stages
    )


def stage_ids_for_legacy_artifact(artifact_path: str) -> tuple[str, ...]:
    """Return conceptual stage ids currently mapped to one legacy artifact path."""

    return tuple(
        mapping.stage_id
        for mapping in LEGACY_MAPPING
        if artifact_path in (*mapping.artifacts, *mapping.optional_artifacts)
    )


def legacy_artifact_paths() -> tuple[str, ...]:
    """Return unique legacy artifact paths in first-seen mapping order."""

    paths: list[str] = []
    for mapping in LEGACY_MAPPING:
        for artifact in (*mapping.artifacts, *mapping.optional_artifacts):
            if artifact not in paths:
                paths.append(artifact)
    return tuple(paths)


def shared_legacy_artifacts() -> dict[str, tuple[str, ...]]:
    """Return legacy artifact paths mapped to more than one conceptual stage."""

    return {
        artifact: stage_ids
        for artifact in legacy_artifact_paths()
        if len(stage_ids := stage_ids_for_legacy_artifact(artifact)) > 1
    }


def shared_physical_stages() -> dict[str, tuple[str, ...]]:
    """Return physical stages mapped to more than one conceptual stage."""

    return {
        physical_stage: stage_ids
        for physical_stage in physical_stages_in_legacy_map()
        if len(stage_ids := stage_ids_for_physical_stage(physical_stage)) > 1
    }


def stage_ids_with_pending_work() -> tuple[str, ...]:
    """Return conceptual stage ids whose current mapping records pending work."""

    return tuple(mapping.stage_id for mapping in LEGACY_MAPPING if mapping.pending_work)


def stage_ids_blocked_by_future_work() -> tuple[str, ...]:
    """Return stage ids whose AGENT_FLOW parity depends on a later slice."""

    return tuple(
        mapping.stage_id for mapping in LEGACY_MAPPING if mapping.blocked_by_future_work
    )


def stage_ids_by_implementation_status(status: str) -> tuple[str, ...]:
    """Return conceptual stage ids tagged with one implementation status."""

    return tuple(
        mapping.stage_id
        for mapping in LEGACY_MAPPING
        if mapping.implementation_status == status
    )


def stage_ids_by_runtime_kind(kind: str) -> tuple[str, ...]:
    """Return conceptual stage ids tagged with one runtime mapping kind."""

    return tuple(mapping.stage_id for mapping in LEGACY_MAPPING if mapping.runtime_kind == kind)


def unmapped_stage_outputs() -> dict[str, tuple[str, ...]]:
    """Return conceptual outputs not yet represented by legacy artifacts.

    A missing output is acceptable only when the mapping's pending-work notes
    explicitly name it. This keeps AGENT_FLOW artifact mismatches visible
    without pretending the legacy runtime already writes future files.
    """

    gaps: dict[str, tuple[str, ...]] = {}
    for mapping in LEGACY_MAPPING:
        stage = get_stage(mapping.stage_id)
        mapped = (*mapping.artifacts, *mapping.optional_artifacts)
        missing = tuple(
            output
            for output in stage.outputs
            if not _stage_output_is_mapped(output, mapped)
        )
        if missing:
            gaps[mapping.stage_id] = missing
    return gaps


def _stage_output_is_mapped(output: str, mapped_artifacts: tuple[str, ...]) -> bool:
    if output in mapped_artifacts:
        return True
    if output.endswith("/"):
        return any(artifact.startswith(output) for artifact in mapped_artifacts)
    return False


def _pending_work_mentions_output(mapping: AgentFlowLegacyMapping, output: str) -> bool:
    pending_text = "\n".join(mapping.pending_work)
    return output in pending_text


def validate_agent_flow_legacy_map() -> None:
    """Fail if the compatibility map drifts from the conceptual graph."""

    stage_ids = mapped_stage_ids()
    if stage_ids != ordered_stage_ids():
        raise ValueError("legacy mapping must cover AGENT_FLOW stages in canonical order")
    if len(stage_ids) != len(set(stage_ids)):
        raise ValueError("duplicate legacy mapping stage ids")
    mapped_front_half = set(front_half_stages_in_legacy_map())
    expected_front_half = set(LEGACY_FRONT_HALF_FULL_ORDER)
    if mapped_front_half != expected_front_half:
        raise ValueError(
            "legacy mapping front-half coverage drift: "
            f"mapped={sorted(mapped_front_half)} expected={sorted(expected_front_half)}"
        )
    for stage in LEGACY_FRONT_HALF_FAST_ORDER:
        if stage not in expected_front_half:
            raise ValueError(f"fast front-half stage not in full order: {stage}")
    for mapping in LEGACY_MAPPING:
        for state in mapping.manifest_states:
            if state not in RUN_MANIFEST_STATES:
                raise ValueError(
                    f"unknown manifest state for {mapping.stage_id}: {state}"
                )
            owners = stage_ids_for_manifest_state(state)
            if len(owners) != 1:
                raise ValueError(
                    f"manifest state must map to exactly one stage: {state} -> {owners}"
                )
        if len(mapping.artifacts) != len(set(mapping.artifacts)):
            raise ValueError(f"duplicate artifacts for {mapping.stage_id}")
        if len(mapping.optional_artifacts) != len(set(mapping.optional_artifacts)):
            raise ValueError(f"duplicate optional artifacts for {mapping.stage_id}")
        overlap = set(mapping.artifacts).intersection(mapping.optional_artifacts)
        if overlap:
            raise ValueError(
                f"artifact cannot be both required and optional for "
                f"{mapping.stage_id}: {sorted(overlap)}"
            )
        for artifact_path in (*mapping.artifacts, *mapping.optional_artifacts):
            validate_relative_artifact_path(
                f"legacy_mapping.{mapping.stage_id}",
                artifact_path,
                path_name="legacy artifact path",
            )
        if mapping.implementation_status not in IMPLEMENTATION_STATUSES:
            raise ValueError(
                f"unknown implementation status for {mapping.stage_id}: "
                f"{mapping.implementation_status}"
            )
        if mapping.runtime_kind not in RUNTIME_MAPPING_KINDS:
            raise ValueError(
                f"unknown runtime mapping kind for {mapping.stage_id}: "
                f"{mapping.runtime_kind}"
            )
        if mapping.implementation_status == "docs_only" and mapping.runtime_kind != "pending_future_work":
            raise ValueError(f"docs-only mapping must be pending future work: {mapping.stage_id}")
        if mapping.runtime_kind == "pending_future_work" and not (
            mapping.pending_work or mapping.blocked_by_future_work
        ):
            raise ValueError(f"pending mapping must explain pending work: {mapping.stage_id}")
        if mapping.blocked_by_future_work and not mapping.pending_work:
            raise ValueError(f"blocked mapping must also record pending work: {mapping.stage_id}")
        if mapping.runtime_kind == "direct_runtime" and not (
            mapping.physical_stages or mapping.front_half_stages
        ):
            raise ValueError(f"direct runtime mapping has no physical stage: {mapping.stage_id}")
        front_half_physical_stages: list[str] = []
        for physical_stage in mapping.physical_stages:
            namespace, separator, name = physical_stage.partition(":")
            if not separator or not namespace or not name:
                raise ValueError(
                    f"physical stage must be namespace:name for {mapping.stage_id}: "
                    f"{physical_stage}"
                )
            if namespace not in PHYSICAL_STAGE_NAMESPACES:
                raise ValueError(
                    f"unknown physical stage namespace for {mapping.stage_id}: "
                    f"{namespace}"
                )
            if namespace == "front_half":
                front_half_physical_stages.append(name)
        if tuple(front_half_physical_stages) != mapping.front_half_stages:
            raise ValueError(
                f"front-half physical stages must match front_half_stages for "
                f"{mapping.stage_id}"
            )
        missing_outputs = unmapped_stage_outputs().get(mapping.stage_id, ())
        unexplained = [
            output
            for output in missing_outputs
            if not _pending_work_mentions_output(mapping, output)
        ]
        if unexplained:
            raise ValueError(
                f"legacy mapping has unexplained output gap for {mapping.stage_id}: "
                f"{unexplained}"
            )


validate_agent_flow_legacy_map()

__all__ = [
    "AgentFlowLegacyMapping",
    "IMPLEMENTATION_STATUSES",
    "ImplementationStatus",
    "LEGACY_FRONT_HALF_FAST_ORDER",
    "LEGACY_FRONT_HALF_FULL_ORDER",
    "LEGACY_MAPPING",
    "PHYSICAL_STAGE_NAMESPACES",
    "RUN_MANIFEST_STATES",
    "RUNTIME_MAPPING_KINDS",
    "RuntimeMappingKind",
    "conceptual_stage_ids_for_front_half_plan",
    "conceptual_stage_ids_for_front_half_stage",
    "front_half_stages_in_legacy_map",
    "get_legacy_mapping",
    "legacy_artifact_paths",
    "manifest_states_in_legacy_map",
    "mapped_stage_ids",
    "physical_stage_namespaces",
    "physical_stages_in_legacy_map",
    "shared_legacy_artifacts",
    "shared_physical_stages",
    "stage_ids_for_legacy_artifact",
    "stage_ids_for_manifest_state",
    "stage_ids_for_physical_stage",
    "stage_ids_by_implementation_status",
    "stage_ids_by_runtime_kind",
    "stage_ids_blocked_by_future_work",
    "stage_ids_with_pending_work",
    "unmapped_stage_outputs",
    "validate_agent_flow_legacy_map",
]
