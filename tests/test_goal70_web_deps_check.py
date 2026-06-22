from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "goal70_web_deps_check.py"


def _run_check(web_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--web-dir", str(web_dir)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_goal70_web_deps_check_fails_when_node_modules_missing(tmp_path: Path) -> None:
    web_dir = tmp_path / "本科 web"
    web_dir.mkdir()
    (web_dir / "package.json").write_text("{}", encoding="utf-8")
    (web_dir / "package-lock.json").write_text("{}", encoding="utf-8")

    completed = _run_check(web_dir)

    assert completed.returncode != 0
    assert "Goal 70 web dependency check failed" in completed.stdout
    assert "missing" in completed.stdout
    assert "node_modules" in completed.stdout


def test_goal70_web_deps_check_passes_when_required_bins_exist(tmp_path: Path) -> None:
    web_dir = tmp_path / "web"
    bin_dir = web_dir / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    (web_dir / "package.json").write_text("{}", encoding="utf-8")
    (web_dir / "package-lock.json").write_text("{}", encoding="utf-8")
    (bin_dir / "vitest").write_text("", encoding="utf-8")
    (bin_dir / "tsc").write_text("", encoding="utf-8")

    completed = _run_check(web_dir)

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "Goal 70 web dependency check passed" in completed.stdout
