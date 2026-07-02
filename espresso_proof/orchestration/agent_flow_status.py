"""AGENT_FLOW stage-status projection helpers.

The projection is process metadata only. It makes conceptual stage state
visible for UI/orchestration migration work, but it does not own mathematical
proof, source, evidence, refutation, grade, or human-confirmed status.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.io import path_exists, read_yaml, write_yaml
from espresso_proof.kernel.models import StageStatusReport
from espresso_proof.kernel.observability import now_iso
from espresso_proof.kernel.pressure_status import artifact_revision_hash
from espresso_proof.kernel.agent_flow_legacy_map import (
    LEGACY_MAPPING,
    AgentFlowLegacyMapping,
)
from espresso_proof.kernel.agent_flow_spec import (
    get_stage,
    stages_blocked_by_unclear_claim,
)
from espresso_proof.validation.errors import KernelValidationError

STAGE_STATUS_FILENAME = "stage_status.yaml"
_DEPENDENCY_READY_STATUSES = {"queued", "running", "done", "failed"}

_AGENT_STATUS_STAGE_MAP: dict[str, str] = {
    "understanding_claim": "claim_understanding",
    "literature_review": "literature",
    "examples": "examples",
    "counterexamples": "counterexamples",
    "counterexample_admissibility": "counterexamples",
    "failure_classifier": "failure_patterns",
}


def load_stage_status_projection(run_dir: Path) -> StageStatusReport:
    """Load persisted stage status or synthesize a read-time legacy projection."""

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    rel = (manifest.get("optional_artifacts") or {}).get("stage_status")
    if rel:
        if rel != STAGE_STATUS_FILENAME:
            raise KernelValidationError(
                "stage_status optional artifact must be stage_status.yaml"
            )
        if not path_exists(run_dir / rel):
            raise KernelValidationError("stage_status optional artifact is missing")
        return StageStatusReport.model_validate(read_yaml(run_dir / rel))
    return StageStatusReport.model_validate(
        build_stage_status_projection(run_dir, include_artifact_hashes=False)
    )


def write_stage_status_projection(
    run_dir: Path,
    *,
    include_artifact_hashes: bool = True,
) -> dict:
    """Persist a synthetic process-status projection as an optional artifact."""

    run_dir = Path(run_dir)
    report = build_stage_status_projection(
        run_dir,
        include_artifact_hashes=include_artifact_hashes,
    )
    StageStatusReport.model_validate(report)
    write_yaml(run_dir / STAGE_STATUS_FILENAME, report)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["stage_status"] = STAGE_STATUS_FILENAME
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return report


def build_stage_status_projection(
    run_dir: Path,
    *,
    include_artifact_hashes: bool = True,
) -> dict:
    """Build a legacy-compatible AGENT_FLOW process projection without writing."""

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts") or {}
    claim = read_yaml(run_dir / artifacts["locked_claim"])
    generated_at = now_iso()
    front_half_results = _front_half_results(run_dir, manifest)
    agent_statuses = _agent_statuses(run_dir)
    claim_clear_blocked = manifest.get("current_state") == "WAITING_CLAIM_CLARIFICATION"
    blocked_stage_ids = set(stages_blocked_by_unclear_claim())

    stages: list[dict] = []
    for mapping in LEGACY_MAPPING:
        stage = get_stage(mapping.stage_id)
        entry = {
            "stage_id": stage.id,
            "display_name": stage.display_name,
            "status": "not_started",
            "status_reason": "",
            "started_at": None,
            "finished_at": None,
            "depends_on": list(stage.depends_on),
            "reads_from": list(stage.reads_from),
            "outputs": list(stage.outputs),
            "parallel_group": stage.parallel_group,
            "evidence_role": stage.evidence_role,
            "advisory_only": True,
            "claim_clear_blocked": False,
            "error_summary": None,
            "artifact_hashes": (
                _artifact_hashes(run_dir, mapping) if include_artifact_hashes else {}
            ),
        }
        _apply_legacy_status(
            entry,
            mapping,
            front_half_results,
            agent_statuses,
            run_dir,
            generated_at,
        )
        if claim_clear_blocked and stage.id in blocked_stage_ids:
            entry["status"] = "skipped"
            entry["status_reason"] = "blocked by claim-clear gate"
            entry["finished_at"] = generated_at
            entry["claim_clear_blocked"] = True
            entry["error_summary"] = None
        stages.append(entry)

    _withhold_status_until_dependencies_are_done(stages, generated_at=generated_at)

    return {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": generated_at,
        "advisory_only": True,
        "effect": "process_status_only",
        "stages": stages,
        "notes": [
            "AGENT_FLOW process-status projection only.",
            "Stage completion does not imply mathematical validation.",
            "parallel_group records conceptual AGENT_FLOW lanes; legacy full runs may still execute them sequentially.",
        ],
    }


def _front_half_results(run_dir: Path, manifest: dict) -> dict[str, dict]:
    rel = (manifest.get("optional_artifacts") or {}).get("front_half_run")
    if not rel or not (run_dir / rel).exists():
        return {}
    report = read_yaml(run_dir / rel) or {}
    return {
        str(item.get("stage")): item
        for item in report.get("stages", [])
        if isinstance(item, dict) and item.get("stage")
    }


def _agent_statuses(run_dir: Path) -> dict[str, dict]:
    path = run_dir / "agent_run_status.yaml"
    if not path.exists():
        return {}
    board = read_yaml(path) or {}
    return {
        str(item.get("agent")): item
        for item in board.get("agents", [])
        if isinstance(item, dict) and item.get("agent")
    }


def _apply_legacy_status(
    entry: dict,
    mapping: AgentFlowLegacyMapping,
    front_half_results: dict[str, dict],
    agent_statuses: dict[str, dict],
    run_dir: Path,
    generated_at: str,
) -> None:
    stage_results = [
        front_half_results[stage]
        for stage in mapping.front_half_stages
        if stage in front_half_results
    ]
    if stage_results:
        statuses = {str(result.get("status")) for result in stage_results}
        if "error" in statuses:
            entry["status"] = "failed"
            entry["error_summary"] = "; ".join(
                error
                for result in stage_results
                for error in (result.get("errors") or [])
                if isinstance(error, str)
            ) or "front-half stage failed"
        elif "ok" in statuses:
            entry["status"] = "done"
            if "skipped" in statuses:
                entry["status_reason"] = "coarse legacy stage partially executed"
        else:
            entry["status"] = "skipped"
            entry["status_reason"] = "legacy front-half stage skipped"
        if entry["status"] in {"done", "skipped", "failed"}:
            entry["finished_at"] = generated_at
        return

    agent_name = _AGENT_STATUS_STAGE_MAP.get(mapping.stage_id)
    if agent_name and agent_name in agent_statuses:
        agent_entry = agent_statuses[agent_name]
        agent_status = str(agent_entry.get("status") or "not_run")
        allow_not_run_artifact_fallback = (
            mapping.stage_id == "failure_classifier"
            and agent_status == "not_run"
            and _any_mapping_artifact_exists(run_dir, mapping)
        )
        if not allow_not_run_artifact_fallback:
            _apply_agent_status(entry, agent_entry, generated_at)
            return

    if mapping.stage_id == "scaffold":
        entry["status"] = "done"
        entry["finished_at"] = generated_at
        return

    if mapping.stage_id == "claim_clear_gate":
        if (run_dir / "claim_clear_gate.yaml").exists():
            entry["status"] = "done"
            entry["finished_at"] = generated_at
        else:
            entry["status"] = "done"
            entry["status_reason"] = "legacy run has no claim_clear_gate artifact"
            entry["finished_at"] = generated_at
        return

    if mapping.stage_id == "human_strategy_preference":
        choice_path = run_dir / "human_choice.yaml"
        choice = read_yaml(choice_path) if choice_path.exists() else {}
        choice_status = str(choice.get("status") or "")
        if choice_status == "selected":
            entry["status"] = "done"
            entry["finished_at"] = generated_at
        elif choice_status == "declined":
            entry["status"] = "skipped"
            entry["status_reason"] = "human strategy preference declined"
            entry["finished_at"] = generated_at
        elif choice_status == "pending":
            entry["status"] = "queued"
            entry["status_reason"] = "waiting for human strategy preference"
        return

    if mapping.stage_id == "agent_consensus_seal":
        if (run_dir / "agent_consensus_review.yaml").exists():
            entry["status"] = "done"
            entry["finished_at"] = generated_at
        elif (run_dir / "proof_gap_resolution.yaml").exists():
            entry["status"] = "failed"
            entry["status_reason"] = "seal failed closed; repair plan written"
            entry["error_summary"] = "unresolved line-numbered proof dependencies"
            entry["finished_at"] = generated_at
        else:
            entry["status_reason"] = "waiting for line_numbered_proof_writer"
        return

    if mapping.runtime_kind in {"optional_runtime", "pending_future_work", "dev_test_only"}:
        if _any_mapping_artifact_exists(run_dir, mapping):
            entry["status"] = "done"
            entry["finished_at"] = generated_at
        return

    if _any_mapping_artifact_exists(run_dir, mapping):
        entry["status"] = "done"
        entry["finished_at"] = generated_at


def _apply_agent_status(entry: dict, agent_entry: dict, generated_at: str) -> None:
    status = str(agent_entry.get("status") or "not_run")
    if status == "not_run":
        entry["status"] = "not_started"
        return
    if status in {"queued", "running"}:
        entry["status"] = status
        entry["started_at"] = agent_entry.get("started_at")
        return
    if status == "done":
        entry["status"] = "done"
    elif status == "skipped":
        entry["status"] = "skipped"
        entry["status_reason"] = agent_entry.get("error_summary") or "agent skipped"
    elif status == "failed":
        entry["status"] = "failed"
        entry["error_summary"] = agent_entry.get("error_summary") or "agent failed"
    else:
        entry["status"] = "not_started"
        return
    entry["started_at"] = agent_entry.get("started_at")
    entry["finished_at"] = agent_entry.get("finished_at") or generated_at


def _withhold_status_until_dependencies_are_done(
    stages: list[dict],
    *,
    generated_at: str,
) -> None:
    """Keep legacy projections from claiming stages that AGENT_FLOW cannot start yet."""

    status_by_id: dict[str, str] = {}
    for entry in stages:
        missing_done_dependencies = [
            dependency
            for dependency in entry["depends_on"]
            if status_by_id.get(dependency) != "done"
        ]
        if entry["status"] in _DEPENDENCY_READY_STATUSES and missing_done_dependencies:
            if entry["status"] == "done" and entry["stage_id"] in {
                "grade",
                "concise_answer",
            }:
                entry["status"] = "blocked"
                entry["status_reason"] = (
                    "legacy artifact exists, but target AGENT_FLOW hard "
                    "dependencies are pending: "
                    + ", ".join(missing_done_dependencies)
                )
                entry["started_at"] = None
                entry["finished_at"] = entry.get("finished_at") or generated_at
                entry["error_summary"] = "target AGENT_FLOW stage dependency not satisfied"
            else:
                entry["status"] = "not_started"
                entry["status_reason"] = (
                    "legacy projection withheld until hard dependencies are done: "
                    + ", ".join(missing_done_dependencies)
                )
                entry["started_at"] = None
                entry["finished_at"] = None
                entry["error_summary"] = None
        status_by_id[entry["stage_id"]] = entry["status"]


def _any_mapping_artifact_exists(run_dir: Path, mapping: AgentFlowLegacyMapping) -> bool:
    return any((run_dir / rel).exists() for rel in (*mapping.artifacts, *mapping.optional_artifacts))


def _artifact_hashes(run_dir: Path, mapping: AgentFlowLegacyMapping) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in (*mapping.artifacts, *mapping.optional_artifacts):
        if rel == STAGE_STATUS_FILENAME:
            continue
        if mapping.stage_id == "scaffold" and rel == "run_manifest.yaml":
            continue
        if (run_dir / rel).exists():
            hashes[rel] = artifact_revision_hash(run_dir, rel)
    if mapping.stage_id == "scaffold":
        for rel in ("input.md",):
            if (run_dir / rel).exists():
                hashes[rel] = artifact_revision_hash(run_dir, rel)
    return hashes


__all__ = [
    "STAGE_STATUS_FILENAME",
    "build_stage_status_projection",
    "load_stage_status_projection",
    "write_stage_status_projection",
]
