"""Front-half pass runner + durable audit artifact (Phase 04 physical split)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.claim.claim_clear import claim_clear_for_downstream, record_claim_clear_block
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.agents.llm_agents import LLMClient, load_agent_context
from espresso_proof.kernel.models import FrontHalfRunReport
from espresso_proof.kernel.observability import append_agent_event, monotonic_ms, now_iso
from espresso_proof.kernel.run_events import append_event
from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures
from espresso_proof.kernel.validators import validate_run
from .registry import FRONT_HALF_STAGES, _STAGE_REGISTRY
from . import stages as _stages  # noqa: F401 - ensure stage normalizers are registered

def run_front_half_agent_pass(
    *,
    run_dir: Path,
    client: LLMClient,
    stages: list[str] | None = None,
    strict: bool = True,
) -> dict:
    """Run the front-half stages, validating the run after each.

    Unimplemented stages are skipped (mock artifact untouched). With
    ``strict=True`` (default) any normalizer error or post-stage validation
    error aborts the pass — the kernel is the gate, so we fail closed rather
    than leave a run in an unvalidated state.
    """

    stage_list = list(stages) if stages is not None else list(FRONT_HALF_STAGES)
    generated_at = datetime.now(UTC).isoformat()
    results: list[dict] = []
    # Best-effort run_id for agent_event correlation (None when absent;
    # the artifacts are co-located in the run dir, so this is a convenience).
    try:
        _run_id = (read_yaml(run_dir / "run_manifest.yaml") or {}).get("run_id")
    except Exception:  # noqa: BLE001 - never break the pass on a manifest read
        _run_id = None

    for stage in stage_list:
        normalizer = _STAGE_REGISTRY.get(stage)
        if normalizer is None:
            results.append(
                {
                    "stage": stage,
                    "status": "skipped",
                    "validation": "not_run",
                    "errors": [],
                    "unsafe_language": [],
                }
            )
            continue
        gate = claim_clear_for_downstream(run_dir, stage=stage)
        if not gate.clear:
            record_claim_clear_block(run_dir, gate)
            results.append(
                {
                    "stage": stage,
                    "status": "skipped",
                    "validation": "not_run",
                    "errors": [
                        "blocked by claim-clear gate: " + "; ".join(gate.reasons)
                    ],
                    "unsafe_language": [],
                }
            )
            continue
        # Bracket each front-half agent call with provenance (events.jsonl
        # + agent_events.jsonl). Diagnostic only — never gates or upgrades status.
        provider = getattr(client, "provider", "unknown")
        started_at = now_iso()
        _t0 = monotonic_ms()
        append_event(run_dir, "agent_call_started", stage=stage)
        # Record a "started" agent_event before the call so a mid-call
        # process death leaves a dangling started record (see
        # observability.latest_unfinished_agent_call).
        append_agent_event(
            run_dir, agent_name=stage, provider=provider, adapter=provider, stage=stage,
            run_id=_run_id, agent_status="started", started_at=started_at,
            model=getattr(client, "model", None),
        )
        try:
            context = load_agent_context(run_dir)
            outcome = normalizer(run_dir=run_dir, client=client, context=context) or {}
            refresh_final_report_disclosures(run_dir)
            validate_run(run_dir)
        except Exception as exc:  # noqa: BLE001 - fail closed, record, optionally re-raise
            append_agent_event(
                run_dir, agent_name=stage, provider=provider, adapter=provider, stage=stage, run_id=_run_id,
                agent_status="failed", started_at=started_at, finished_at=now_iso(),
                duration_ms=monotonic_ms() - _t0, model=getattr(client, "model", None),
                validation_status="failed", error_summary=str(exc),
            )
            append_event(run_dir, "agent_call_failed", stage=stage, detail=str(exc))
            results.append(
                {
                    "stage": stage,
                    "status": "error",
                    "validation": "error",
                    "errors": [str(exc)],
                    "unsafe_language": [],
                }
            )
            if strict:
                raise
            continue
        append_agent_event(
            run_dir, agent_name=stage, provider=provider, adapter=provider, stage=stage, run_id=_run_id,
            agent_status="finished", started_at=started_at, finished_at=now_iso(),
            duration_ms=monotonic_ms() - _t0, model=getattr(client, "model", None),
            validation_status="passed",
        )
        append_event(run_dir, "agent_call_finished", stage=stage)
        results.append(
            {
                "stage": stage,
                "status": "ok",
                "validation": "ok",
                "errors": [],
                "unsafe_language": list(outcome.get("unsafe_language", [])),
            }
        )

    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "provider": getattr(client, "provider", "unknown"),
        "model": getattr(client, "model", "unknown"),
        "stages": results,
        "notes": [
            "Front-half agent pass; outputs are candidate planning artifacts only.",
            "This pass must not upgrade source_backed, checked evidence, or proof status.",
            "Every stage is re-validated by the Python kernel before being accepted.",
        ],
    }


FRONT_HALF_RUN_FILENAME = "front_half_run.yaml"


def build_front_half_run_report(
    run_dir: Path,
    *,
    pass_report: dict | None,
    provider: str,
    model: str,
) -> dict:
    """Build a model-valid ``front_half_run`` report dict (audit-only).

    With ``pass_report=None`` (mock mode, no live pass) every stage is recorded
    as a durable ``skipped`` entry rather than dropped.
    """

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    if pass_report is not None:
        # The artifact records the client that actually executed the pass
        # (captured from the live client), never the CLI flag defaults.
        provider = str(pass_report.get("provider") or provider)
        model = str(pass_report.get("model") or model)
    if pass_report is None:
        stages = [
            {
                "stage": stage,
                "status": "skipped",
                "validation": "not_run",
                "errors": [],
                "unsafe_language": [],
            }
            for stage in FRONT_HALF_STAGES
        ]
        generated_at = datetime.now(UTC).isoformat()
        notes = [
            "Front-half pass skipped (mock mode); no stage executed.",
            "Process audit only; never upgrades source/evidence/proof status.",
        ]
    else:
        stages = pass_report["stages"]
        generated_at = pass_report.get("generated_at", datetime.now(UTC).isoformat())
        notes = pass_report.get("notes", [])

    report = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": generated_at,
        "provider": provider,
        "model": model,
        "stages": stages,
        "notes": notes,
    }
    FrontHalfRunReport.model_validate(report)
    return report


def write_front_half_run(
    run_dir: Path,
    *,
    pass_report: dict | None,
    provider: str,
    model: str,
) -> dict:
    """Persist ``front_half_run.yaml`` (rewritten, not appended -> idempotent) and
    register it as the optional manifest artifact ``front_half_run``."""

    report = build_front_half_run_report(
        run_dir, pass_report=pass_report, provider=provider, model=model
    )
    write_yaml(run_dir / FRONT_HALF_RUN_FILENAME, report)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["front_half_run"] = FRONT_HALF_RUN_FILENAME
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return report


def front_half_stage_ok(run_dir: Path, stage: str) -> bool:
    """True iff the durable front-half audit recorded this stage ok+validated.

    The provenance-true trigger for merge-beside-agent-output behavior: a full
    live run records the stage ok; fast runs omit the stage, mock runs record
    ``skipped``, an aborted pass leaves no file at all. ANY read/parse problem
    is False (fail closed to today's replace semantics — never invents agent
    provenance).
    """

    try:
        path = Path(run_dir) / FRONT_HALF_RUN_FILENAME
        if not path.exists():
            return False
        report = read_yaml(path) or {}
        for item in report.get("stages", []):
            if isinstance(item, dict) and item.get("stage") == stage:
                return item.get("status") == "ok" and item.get("validation") == "ok"
        return False
    except Exception:  # noqa: BLE001 - provenance probe must fail closed
        return False


# ---------------------------------------------------------------------------
# S1 — 01 Understanding Claim
# ---------------------------------------------------------------------------

__all__ = [
    "run_front_half_agent_pass",
    "build_front_half_run_report",
    "write_front_half_run",
    "front_half_stage_ok",
    "FRONT_HALF_RUN_FILENAME",
]
