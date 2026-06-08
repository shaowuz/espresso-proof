"""Front-half pass runner + durable audit artifact (Phase 04 physical split)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ..io import read_yaml, write_yaml
from ..llm_agents import LLMClient, load_agent_context
from ..models import FrontHalfRunReport
from ..validators import validate_run
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
        try:
            context = load_agent_context(run_dir)
            outcome = normalizer(run_dir=run_dir, client=client, context=context) or {}
            validate_run(run_dir)
        except Exception as exc:  # noqa: BLE001 - fail closed, record, optionally re-raise
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


# ---------------------------------------------------------------------------
# S1 — 01 Understanding Claim
# ---------------------------------------------------------------------------

__all__ = ["run_front_half_agent_pass", "build_front_half_run_report", "write_front_half_run", "FRONT_HALF_RUN_FILENAME"]
