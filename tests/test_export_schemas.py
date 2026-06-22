from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "export_schemas.py"


def _run_export_schemas(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_export_schemas_check_passes_after_export(tmp_path: Path) -> None:
    out_dir = tmp_path / "schemas"

    exported = _run_export_schemas("--out-dir", str(out_dir))
    assert exported.returncode == 0, exported.stderr + exported.stdout

    checked = _run_export_schemas("--check", "--out-dir", str(out_dir))
    assert checked.returncode == 0, checked.stderr + checked.stdout
    assert "Schema check passed" in checked.stdout


def test_export_schemas_check_reports_stale_schema(tmp_path: Path) -> None:
    out_dir = tmp_path / "schemas"
    exported = _run_export_schemas("--out-dir", str(out_dir))
    assert exported.returncode == 0, exported.stderr + exported.stdout

    schema_path = next(out_dir.glob("*.schema.json"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["title"] = "stale-test-title"
    schema_path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")

    checked = _run_export_schemas("--check", "--out-dir", str(out_dir))
    assert checked.returncode == 1
    assert f"stale {schema_path}" in checked.stdout
