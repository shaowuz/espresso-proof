"""Cross-artifact validation for the durable ``front_half_run.yaml`` audit
artifact (Phase 03).

This is invoked at the kernel-driven write boundary (inside the front-half
StageRunner transaction), so a malformed / mismatched / status-smuggling
front_half_run fails closed and rolls back. ``validate_run`` independently
enforces the artifact's existence and run-directory path confinement via the
manifest path validator; this adds the content checks.

The artifact is process evidence only: it can never set or upgrade
proof/source/evidence status (the model forbids unknown fields, so a
``source_backed`` / ``checked`` / ``proved`` / ``human_confirmed`` field is
rejected on load).
"""

from __future__ import annotations

from pathlib import Path

from ..io import read_yaml
from ..models import FrontHalfRunReport
from .errors import KernelValidationError


def validate_front_half_run(run_dir: Path) -> None:
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    optional = manifest.get("optional_artifacts", {})
    rel = optional.get("front_half_run")
    if not rel:
        return  # optional + old-run compatible: absence is fine

    # local import to avoid any import cycle with front_half_agents
    from ..front_half_agents import FRONT_HALF_STAGES

    path = run_dir / rel
    try:
        report = FrontHalfRunReport.model_validate(read_yaml(path))
    except Exception as exc:  # noqa: BLE001 - surface as a kernel validation error
        raise KernelValidationError(f"front_half_run.yaml failed schema validation\n{exc}") from exc

    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    if report.claim_id != claim["id"]:
        raise KernelValidationError("front_half_run claim_id does not match locked claim")
    if report.run_id != manifest["run_id"]:
        raise KernelValidationError("front_half_run run_id does not match the run manifest")

    known_stages = set(FRONT_HALF_STAGES)
    seen: set[str] = set()
    for stage in report.stages:
        if stage.stage not in known_stages:
            raise KernelValidationError(
                f"front_half_run references unknown stage {stage.stage!r}"
            )
        if stage.stage in seen:
            raise KernelValidationError(
                f"front_half_run has a duplicate entry for stage {stage.stage!r}"
            )
        seen.add(stage.stage)
        if stage.status == "error" and stage.validation == "ok":
            raise KernelValidationError(
                f"front_half_run stage {stage.stage!r}: error stage cannot validate as ok"
            )
        for artifact_path in (*stage.input_artifacts, *stage.output_artifacts):
            candidate = (run_dir / artifact_path).resolve()
            if run_dir.resolve() not in candidate.parents and candidate != run_dir.resolve():
                raise KernelValidationError(
                    f"front_half_run stage {stage.stage!r} artifact escapes run dir: {artifact_path!r}"
                )


__all__ = ["validate_front_half_run"]
