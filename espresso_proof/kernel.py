from __future__ import annotations

from pathlib import Path
from typing import Any

from .diagnostics import current_capabilities
from .io import read_yaml

try:
    from .validators import validate_run
except Exception:  # pragma: no cover - callers should still report the gap.
    validate_run = None


class KernelError(Exception):
    """Raised when a kernel-level workspace, run, or artifact operation fails."""


def snapshot(workspace: Path, requested_run: Path | None = None) -> dict[str, Any]:
    root = workspace.resolve()
    runs = find_runs(root)
    projects = find_projects(root)
    selected = requested_run.resolve() if requested_run else (Path(runs[0]["path"]) if runs else None)
    current = load_run_snapshot(root, selected) if selected else None
    return {
        "workspace": str(root),
        "capabilities": current_capabilities(),
        "projects": projects,
        "runs": runs,
        "selected_run": str(selected) if selected else None,
        "current": current,
    }


def find_projects(root: Path) -> list[dict[str, Any]]:
    projects = []
    for spec_path in sorted(root.glob("projects/*/workflow_spec.yaml")):
        spec = read_yaml(spec_path) or {}
        project_dir = spec_path.parent
        projects.append(
            {
                "project_id": spec.get("project_id", project_dir.name),
                "name": spec.get("name", project_dir.name),
                "domain": spec.get("domain", spec.get("domain_key", "")),
                "path": str(project_dir),
                "workflow_spec": str(spec_path),
            }
        )
    return projects


def find_runs(root: Path) -> list[dict[str, Any]]:
    run_manifests = sorted(
        root.glob("projects/*/runs/*/run_manifest.yaml"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    runs = []
    for manifest_path in run_manifests:
        run_dir = manifest_path.parent
        manifest = read_yaml(manifest_path) or {}
        runs.append(
            {
                "path": str(run_dir),
                "project_id": manifest.get("project_id", run_dir.parent.parent.name),
                "run_id": manifest.get("run_id", run_dir.name),
                "state": manifest.get("current_state", "unknown"),
                "created_at": manifest.get("created_at", ""),
                "mtime": run_dir.stat().st_mtime,
            }
        )
    return runs


def load_run_snapshot(root: Path, run_dir: Path) -> dict[str, Any]:
    run_dir = require_inside(root, run_dir.resolve())
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path) or {}
    validation = validate_run_status(run_dir)
    artifact_paths = {"run_manifest": str(manifest_path)}
    artifacts = {"run_manifest": manifest}
    previews = {"run_manifest": artifact_preview(manifest_path)}
    diagnostics = [
        artifact_diagnostic("run_manifest", manifest_path, required=True),
    ]

    for key, rel_path in manifest.get("artifacts", {}).items():
        path = safe_run_path(run_dir, rel_path)
        artifact_paths[key] = str(path)
        artifacts[key] = read_artifact(path)
        previews[key] = artifact_preview(path)
        diagnostics.append(artifact_diagnostic(key, path, required=True))

    for key, rel_path in manifest.get("optional_artifacts", {}).items():
        path = safe_run_path(run_dir, rel_path)
        artifact_paths[key] = str(path)
        artifacts[key] = read_artifact(path) if path.exists() else None
        previews[key] = artifact_preview(path) if path.exists() else None
        diagnostics.append(artifact_diagnostic(key, path, required=False))

    missing_required = [
        item["key"] for item in diagnostics if item["required"] and not item["exists"]
    ]
    missing_optional = [
        item["key"] for item in diagnostics if not item["required"] and not item["exists"]
    ]

    return {
        "run_dir": str(run_dir),
        "state": classify_state(validation, artifacts, missing_required),
        "validation": validation,
        "manifest": manifest,
        "artifact_paths": artifact_paths,
        "artifacts": artifacts,
        "artifact_previews": previews,
        "artifact_diagnostics": diagnostics,
        "missing_artifacts": missing_required,
        "missing_optional_artifacts": missing_optional,
        "progress": progress_items(run_dir, manifest),
    }


def validate_run_status(run_dir: Path) -> dict[str, str]:
    if validate_run is None:
        return {"status": "not_available", "message": "validator import failed"}
    try:
        validate_run(run_dir)
        return {"status": "valid", "message": ""}
    except Exception as exc:
        return {"status": "partial", "message": str(exc)}


def classify_state(
    validation: dict[str, Any],
    artifacts: dict[str, Any],
    missing_required: list[str],
) -> str:
    if missing_required:
        return "partial"
    if validation["status"] == "valid":
        loop_control = artifacts.get("loop_control") or {}
        idea_grade = artifacts.get("idea_grade") or {}
        if loop_control.get("next_action") in {"ask_human", "run_strategy_brief", "stop"}:
            return "stopped"
        if idea_grade.get("human_review_required") is True:
            return "stopped"
        return "completed"
    if validation["status"] == "partial":
        return "partial"
    return "error"


def progress_items(run_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    dependencies = manifest.get("dependencies", {})
    items = [
        {
            "key": "run_manifest",
            "path": str(run_dir / "run_manifest.yaml"),
            "exists": True,
            "depends_on": [],
        }
    ]
    for key, rel_path in manifest.get("artifacts", {}).items():
        path = safe_run_path(run_dir, rel_path)
        items.append(
            {
                "key": key,
                "path": str(path),
                "exists": path.exists(),
                "depends_on": dependencies.get(key, {}).get("depends_on", []),
            }
        )
    for key, rel_path in manifest.get("optional_artifacts", {}).items():
        path = safe_run_path(run_dir, rel_path)
        items.append({"key": key, "path": str(path), "exists": path.exists(), "depends_on": []})
    return items


def artifact_diagnostic(key: str, path: Path, required: bool) -> dict[str, Any]:
    return {
        "key": key,
        "path": str(path),
        "required": required,
        "exists": path.exists(),
    }


def read_artifact(path: Path) -> Any:
    if not path.exists():
        return None
    # A bankable artifact may be a per-item bank directory (after
    # `materialize-banks`); aggregate it leniently to the same dict shape as the
    # old single-file artifact so the read-only snapshot works on both layouts.
    if path.is_dir():
        from .research_bank import aggregate_bank_dict, is_bank_dir

        return aggregate_bank_dict(path) if is_bank_dir(path) else None
    if path.suffix in {".yaml", ".yml"}:
        return read_yaml(path)
    return path.read_text(encoding="utf-8")


def artifact_preview(path: Path, limit: int = 4000) -> str | None:
    if not path.exists():
        return None
    if path.is_dir():
        import yaml

        from .research_bank import aggregate_bank_dict, is_bank_dir

        if not is_bank_dir(path):
            return None
        text = yaml.safe_dump(
            aggregate_bank_dict(path), sort_keys=False, allow_unicode=False
        )
    else:
        text = path.read_text(encoding="utf-8")
    if len(text) > limit:
        return text[:limit].rstrip() + "\n...[truncated]"
    return text


def safe_run_path(run_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        raise KernelError(f"artifact path must be relative: {raw_path}")
    if ".." in path.parts:
        raise KernelError(f"artifact path cannot contain '..': {raw_path}")
    root = run_dir.resolve()
    candidate = run_dir / path
    try:
        candidate.parent.resolve().relative_to(root)
    except ValueError as exc:
        raise KernelError(f"artifact parent escapes run directory: {raw_path}") from exc
    if candidate.exists():
        if candidate.is_symlink():
            raise KernelError(f"artifact path is a symlink: {raw_path}")
        try:
            candidate.resolve().relative_to(root)
        except ValueError as exc:
            raise KernelError(f"artifact path escapes run directory: {raw_path}") from exc
    return candidate


def require_inside(root: Path, path: Path) -> Path:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise KernelError(f"path is outside workspace: {path}") from exc
    return path
