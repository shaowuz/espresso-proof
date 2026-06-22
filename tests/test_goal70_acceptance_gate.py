from __future__ import annotations

import json
import os
import subprocess
import sys
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "goal70_acceptance_gate.py"


def _run_gate(
    *args: str,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("ESPRESSO_EXTERNAL_AGENT_COMMAND", None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _load_gate_module():
    module_name = "goal70_acceptance_gate_test"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_goal70_acceptance_gate_dry_run_lists_default_non_live_steps() -> None:
    completed = _run_gate("--dry-run")

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "Goal 70 acceptance gate" in completed.stdout
    assert "web lane: no" in completed.stdout
    assert "tests/test_agent_flow_spec.py" in completed.stdout
    assert "tests/test_run_resume.py" in completed.stdout
    assert "tests/test_lean_bridge_fail_closed.py" in completed.stdout
    assert "tests/test_workbench_ux_v2.py" in completed.stdout
    assert "tests/test_workflow_v2_claim_clear_gate.py" in completed.stdout
    assert "tests/test_workflow_v2_reviewer_objection_inbox.py" in completed.stdout
    assert "tests/test_workflow_v2_memory_validators.py" in completed.stdout
    assert "tests/test_workflow_v2_docs_parity.py" in completed.stdout
    assert "tests/test_real_math_benchmark.py" in completed.stdout
    assert "scripts/export_schemas.py" in completed.stdout
    assert "scripts/export_schemas.py --check" in completed.stdout
    assert "web cockpit trace tests" not in completed.stdout
    assert "real-agent-smoke" not in completed.stdout


def test_goal70_acceptance_gate_live_smoke_is_explicit_opt_in() -> None:
    completed = _run_gate("--dry-run", "--live-smoke")

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "live smoke: yes" in completed.stdout
    assert "real-agent-smoke" in completed.stdout
    assert "--project real_codex_smoke" in completed.stdout
    assert "--input" in completed.stdout
    assert "promising_but_unproved" in completed.stdout
    assert "input.md" in completed.stdout
    assert "--literature-mode local" in completed.stdout
    assert "--max-references 8" in completed.stdout
    assert "--provider external" in completed.stdout
    assert "--front-half mock" in completed.stdout
    assert "--api-key-env OPENAI_API_KEY" in completed.stdout
    assert "--external-agent-command-env ESPRESSO_EXTERNAL_AGENT_COMMAND" in completed.stdout
    assert "--reviewer-set espresso" in completed.stdout
    assert "--repeat-count 1" in completed.stdout
    assert "--json-out" in completed.stdout
    assert "goal70_real_agent_smoke_capability.json" in completed.stdout
    assert "ESPRESSO_EXTERNAL_AGENT_COMMAND" in completed.stdout


def test_goal70_acceptance_gate_can_run_only_live_smoke() -> None:
    completed = _run_gate("--dry-run", "--only-live-smoke")

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "scope: live-smoke-only" in completed.stdout
    assert "web lane: no" in completed.stdout
    assert "real-agent-smoke" in completed.stdout
    assert "tests/test_agent_flow_spec.py" not in completed.stdout
    assert "scripts/export_schemas.py" not in completed.stdout


def test_goal70_acceptance_gate_web_lane_is_explicit_opt_in() -> None:
    completed = _run_gate("--dry-run", "--web")

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "web lane: yes" in completed.stdout
    assert "web dependency preflight" in completed.stdout
    assert "web cockpit trace tests" in completed.stdout
    assert "web cockpit typecheck" in completed.stdout
    assert " test" in completed.stdout
    assert " run typecheck" in completed.stdout


def test_goal70_acceptance_gate_can_run_only_web_lane() -> None:
    completed = _run_gate("--dry-run", "--only-web")

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "scope: web-only" in completed.stdout
    assert "web lane: yes" in completed.stdout
    assert "web dependency preflight" in completed.stdout
    assert "web cockpit trace tests" in completed.stdout
    assert "tests/test_agent_flow_spec.py" not in completed.stdout
    assert "scripts/export_schemas.py" not in completed.stdout
    assert "real-agent-smoke" not in completed.stdout


def test_goal70_acceptance_gate_accepts_web_command_prefix_override() -> None:
    completed = _run_gate(
        "--dry-run",
        "--only-web",
        env_overrides={
            "GOAL70_WEB_COMMAND_PREFIX_JSON": json.dumps(["custom-pm", "--dir", "web"])
        },
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "from GOAL70_WEB_COMMAND_PREFIX_JSON" in completed.stdout
    assert "web dependency preflight" in completed.stdout
    assert "custom-pm --dir web test" in completed.stdout
    assert "custom-pm --dir web run typecheck" in completed.stdout


def test_goal70_acceptance_gate_bundled_pnpm_fallback_disables_auto_install(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_gate_module()
    node_root = tmp_path / "node"
    node = node_root / "bin" / "node.exe"
    pnpm = node_root / "node_modules" / "pnpm" / "bin" / "pnpm.cjs"
    node.parent.mkdir(parents=True)
    pnpm.parent.mkdir(parents=True)
    node.write_text("", encoding="utf-8")
    pnpm.write_text("", encoding="utf-8")

    monkeypatch.delenv("GOAL70_WEB_COMMAND_PREFIX_JSON", raising=False)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(module, "BUNDLED_NODE_ROOT", node_root)

    prefix, note = module._web_command_prefix()

    assert prefix == (
        str(node),
        str(pnpm),
        "--config.verify-deps-before-run=false",
        "--dir",
        "web",
    )
    assert "without auto-install" in note


def test_goal70_acceptance_gate_writes_dry_run_json_report(tmp_path: Path) -> None:
    report_path = tmp_path / "goal70_gate.json"
    completed = _run_gate("--dry-run", "--only-web", "--json-out", str(report_path))

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert f"Wrote Goal 70 acceptance report -> {report_path}" in completed.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert report["status"] == "dry_run_passed"
    assert report["mode"] == "dry-run"
    assert report["scope"] == "web-only"
    assert report["web_lane"] is True
    assert report["live_smoke"] is False
    assert report["duration_seconds_total"] >= 0
    assert report["step_counts"] == {
        "total": 3,
        "passed": 0,
        "failed": 0,
        "dry_run": 3,
        "skipped_after_failure": 0,
    }
    assert report["failed_steps"] == []
    assert [step["name"] for step in report["steps"]] == [
        "web dependency preflight",
        "web cockpit trace tests",
        "web cockpit typecheck",
    ]
    assert all(step["status"] == "dry_run" for step in report["steps"])
    assert report["steps"][1]["command"][-1] == "test"


def test_goal70_acceptance_gate_live_report_lists_capability_evidence(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "goal70_live_gate.json"
    output_root = tmp_path / "live_smoke_runs"
    completed = _run_gate(
        "--dry-run",
        "--only-live-smoke",
        "--output-root",
        str(output_root),
        "--json-out",
        str(report_path),
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    step = report["steps"][0]
    assert report["scope"] == "live-smoke-only"
    assert report["live_smoke"] is True
    assert step["command"][0] == report["python"]
    assert step["command"][1:4] == [
        "-c",
        "from espresso_proof.cli import app; app()",
        "real-agent-smoke",
    ]
    assert step["command"][step["command"].index("--project") + 1] == "real_codex_smoke"
    assert "--input" in step["command"]
    assert step["command"][step["command"].index("--input") + 1] == str(
        ROOT / "tests" / "fixtures" / "promising_but_unproved" / "input.md"
    )
    assert step["command"][step["command"].index("--literature-mode") + 1] == "local"
    assert "--literature-provider" not in step["command"]
    assert step["command"][step["command"].index("--max-references") + 1] == "8"
    assert step["command"][step["command"].index("--front-half") + 1] == "mock"
    assert step["command"][step["command"].index("--api-key-env") + 1] == "OPENAI_API_KEY"
    assert (
        step["command"][step["command"].index("--external-agent-command-env") + 1]
        == "ESPRESSO_EXTERNAL_AGENT_COMMAND"
    )
    assert step["command"][step["command"].index("--reviewer-set") + 1] == "espresso"
    assert step["command"][step["command"].index("--repeat-count") + 1] == "1"
    assert "--json-out" in step["command"]
    assert len(step["command"]) == 32
    assert [token for token in step["command"][4:] if token.startswith("--")] == [
        "--project",
        "--input",
        "--literature-mode",
        "--max-references",
        "--provider",
        "--front-half",
        "--model",
        "--api-key-env",
        "--external-agent-command-env",
        "--reviewer-set",
        "--output-root",
        "--prompt-version",
        "--repeat-count",
        "--json-out",
    ]
    assert step["command"][4::2] == [
        "--project",
        "--input",
        "--literature-mode",
        "--max-references",
        "--provider",
        "--front-half",
        "--model",
        "--api-key-env",
        "--external-agent-command-env",
        "--reviewer-set",
        "--output-root",
        "--prompt-version",
        "--repeat-count",
        "--json-out",
    ]
    assert step["evidence_paths"] == [
        str(output_root / "goal70_real_agent_smoke_capability.json")
    ]
    assert step["missing_evidence_paths"] == []


def test_goal70_acceptance_gate_writes_failed_json_report_when_web_tool_missing(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "goal70_gate_failed.json"
    completed = _run_gate(
        "--only-web",
        "--json-out",
        str(report_path),
        env_overrides={
            "PATH": "",
            "GOAL70_WEB_COMMAND_PREFIX_JSON": json.dumps(
                ["definitely-missing-goal70-pm", "--dir", "web"]
            ),
        },
    )

    assert completed.returncode != 0
    assert "executable not found: definitely-missing-goal70-pm" in completed.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["scope"] == "web-only"
    assert report["step_counts"] == {
        "total": 3,
        "passed": 1,
        "failed": 1,
        "dry_run": 0,
        "skipped_after_failure": 1,
    }
    assert report["failed_steps"] == ["web cockpit trace tests"]
    assert [step["status"] for step in report["steps"]] == [
        "passed",
        "failed",
        "skipped_after_failure",
    ]
    assert report["steps"][1]["return_code"] == 127


def test_goal70_acceptance_gate_rejects_conflicting_only_modes() -> None:
    completed = _run_gate("--dry-run", "--only-web", "--only-live-smoke")

    assert completed.returncode != 0
    assert "cannot be combined" in completed.stderr


def test_goal70_acceptance_gate_can_skip_schema_diff_for_dirty_dev_trees() -> None:
    completed = _run_gate("--dry-run", "--skip-schema-diff")

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "scripts/export_schemas.py" in completed.stdout
    assert "schema drift check" not in completed.stdout
    assert "scripts/export_schemas.py --check" not in completed.stdout
