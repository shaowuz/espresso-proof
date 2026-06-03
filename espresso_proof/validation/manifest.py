from __future__ import annotations

from pathlib import Path

from ..models import RunManifest


class ManifestPathValidationError(ValueError):
    """Raised when a run manifest references an invalid local path."""


def validate_manifest_paths(run_dir: Path, manifest: RunManifest) -> None:
    if manifest.run_id != run_dir.name:
        raise ManifestPathValidationError(
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
            raise ManifestPathValidationError(
                f"{label}: manifest path must be relative: {raw_path!r}"
            )
        if ".." in path.parts:
            raise ManifestPathValidationError(
                f"{label}: manifest path cannot contain '..': {raw_path!r}"
            )
        resolved = (run_dir / path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ManifestPathValidationError(
                f"{label}: manifest path escapes run directory: {raw_path!r}"
            ) from exc
        if not resolved.exists():
            raise ManifestPathValidationError(f"{label}: manifest path is missing: {raw_path!r}")
        if not resolved.is_file():
            raise ManifestPathValidationError(f"{label}: manifest path is not a file: {raw_path!r}")
