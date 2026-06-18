from __future__ import annotations

from pathlib import Path, PureWindowsPath

from espresso_proof.kernel.models import RunManifest


class ManifestPathValidationError(ValueError):
    """Raised when a run manifest references an invalid local path."""


def validate_relative_artifact_path(
    label: str,
    raw_path: str,
    *,
    path_name: str = "manifest path",
) -> Path:
    raw = str(raw_path)
    path = Path(raw)
    windows_path = PureWindowsPath(raw)
    if (
        path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or bool(windows_path.root)
    ):
        raise ManifestPathValidationError(f"{label}: {path_name} must be relative: {raw_path!r}")
    if ".." in path.parts or ".." in windows_path.parts:
        raise ManifestPathValidationError(f"{label}: {path_name} cannot contain '..': {raw_path!r}")
    if "\\" in raw:
        raise ManifestPathValidationError(
            f"{label}: {path_name} must use POSIX '/' separators: {raw_path!r}"
        )
    return path


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
        path = validate_relative_artifact_path(label, raw_path)
        resolved = (run_dir / path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ManifestPathValidationError(
                f"{label}: manifest path escapes run directory: {raw_path!r}"
            ) from exc
        if not resolved.exists():
            raise ManifestPathValidationError(f"{label}: manifest path is missing: {raw_path!r}")
        # An artifact path is normally a single file, but a research-bank artifact
        # may be a DIRECTORY that contains a ``_meta.yaml`` (per-item bank). The
        # escape/``..``/absolute guards above still apply to the directory path.
        if not resolved.is_file():
            if resolved.is_dir() and (resolved / "_meta.yaml").is_file():
                continue
            raise ManifestPathValidationError(
                f"{label}: manifest path is not a file or research bank directory: {raw_path!r}"
            )
