from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from espresso_proof.evaluation.real_math_benchmark import (
    build_real_math_capability_report,
    case_from_run,
)
from espresso_proof.agents.llm_agents import ESPRESSO_REVIEWER_ROLES
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.mock_pipeline import run_mock_critique

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "goal70_completion_audit.py"
SMOKE_INPUT = ROOT / "tests" / "fixtures" / "promising_but_unproved" / "input.md"

NON_LIVE_STEPS = [
    "agent-flow spec/status",
    "agent-flow driver/pressure lanes",
    "strategy boundaries/update services",
    "hard invariant matrix",
    "cockpit trace honesty",
    "real math capability boundary",
    "optional formal bridge",
    "external-agent diagnostics",
    "schema export",
    "schema drift check",
]
WEB_STEPS = [
    "web dependency preflight",
    "web cockpit trace tests",
    "web cockpit typecheck",
]
STEP_COMMANDS = {
    "agent-flow spec/status": [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_agent_flow_spec.py",
        "tests/test_agent_flow_legacy_map.py",
        "tests/test_agent_flow_stage_status.py",
        "tests/test_agent_flow_stage_status_negative.py",
    ],
    "agent-flow driver/pressure lanes": [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_agent_flow_driver.py",
        "tests/test_agent_flow_pressure_lanes.py",
        "tests/test_run_concurrency.py",
        "tests/test_run_resume.py",
    ],
    "strategy boundaries/update services": [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_strategy_services.py",
        "tests/test_workflow_v2_time_allocation.py",
        "tests/test_workflow_v2_update_inbox.py",
    ],
    "hard invariant matrix": [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_workflow_v2_claim_clear_gate.py",
        "tests/test_workflow_v2_source_theorem_hardening.py",
        "tests/test_workflow_v2_async_pressure.py",
        "tests/test_workflow_v2_reviewer_objection_inbox.py",
        "tests/test_workflow_v2_prover_allocation_consumption.py",
        "tests/test_workflow_v2_memory_validators.py",
        "tests/test_workflow_v2_concise_final_validators.py",
        "tests/test_workflow_v2_docs_parity.py",
    ],
    "cockpit trace honesty": [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_cockpit_snapshot.py",
        "tests/test_cockpit_panels.py",
        "tests/test_interaction_polish.py",
        "tests/test_workbench_ux_v2.py",
    ],
    "real math capability boundary": [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_real_source_grounding.py",
        "tests/test_theorem_condition_ledger.py",
        "tests/test_real_math_benchmark.py",
    ],
    "optional formal bridge": [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_lean_bridge_fail_closed.py",
        "tests/test_statement_lock.py",
    ],
    "external-agent diagnostics": [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_codex_agent_adapter.py",
        "tests/test_llm_agents.py",
    ],
    "schema export": [sys.executable, "scripts/export_schemas.py"],
    "schema drift check": [sys.executable, "scripts/export_schemas.py", "--check"],
    "web dependency preflight": [
        sys.executable,
        "scripts/goal70_web_deps_check.py",
    ],
    "web cockpit trace tests": ["npm", "--prefix", "web", "test"],
    "web cockpit typecheck": ["npm", "--prefix", "web", "run", "typecheck"],
    "real Codex external-agent smoke": [
        sys.executable,
        "-c",
        "from espresso_proof.cli import app; app()",
        "real-agent-smoke",
        "--project",
        "real_codex_smoke",
        "--input",
        str(SMOKE_INPUT),
        "--literature-mode",
        "local",
        "--max-references",
        "8",
        "--provider",
        "external",
        "--front-half",
        "mock",
        "--model",
        "codex-cli",
        "--api-key-env",
        "OPENAI_API_KEY",
        "--external-agent-command-env",
        "ESPRESSO_EXTERNAL_AGENT_COMMAND",
        "--reviewer-set",
        "espresso",
        "--output-root",
        str(ROOT),
        "--prompt-version",
        "goal70-acceptance-smoke-v1",
        "--json-out",
        "goal70_real_agent_smoke_capability.json",
    ],
}


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _write_live_run_shape(
    run_dir: Path,
    *,
    provider: str = "external",
    model: str = "gpt-5.5-medium",
    provider_ready: bool = True,
    adapter_fallback_used: bool = False,
) -> None:
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = (
        read_yaml(manifest_path)
        if manifest_path.exists()
        else {"run_id": run_dir.name, "optional_artifacts": {}}
    )
    manifest.setdefault("optional_artifacts", {})["llm_agent_run"] = "llm_agent_run.yaml"
    run_id = manifest.get("run_id") or run_dir.name
    claim_id = "claim_goal70_smoke"
    artifacts = manifest.get("artifacts") or {}
    locked_claim_rel = artifacts.get("locked_claim")
    if locked_claim_rel:
        claim_id = read_yaml(run_dir / locked_claim_rel)["id"]
    input_artifacts = [
        rel
        for rel in (
            artifacts.get("locked_claim"),
            artifacts.get("strategy_brief"),
        )
        if isinstance(rel, str)
    ] or ["locked_claim.yaml", "strategy_brief.yaml"]
    write_yaml(
        manifest_path,
        manifest,
    )
    write_yaml(
        run_dir / "llm_agent_run.yaml",
        {
            "schema_version": 1,
            "claim_id": claim_id,
            "run_id": run_id,
            "generated_at": "2026-06-21T00:00:00+00:00",
            "provider": provider,
            "model": model,
            "input_artifacts": input_artifacts,
            "prover_attempt": {
                "agent_id": "llm_prover_001",
                "role": "prover",
                "provider": provider,
                "model": model,
                "created_at": "2026-06-21T00:00:00+00:00",
                "input_artifacts": input_artifacts,
                "output_summary": "Candidate route only; no proof status is claimed.",
                "proposed_steps": ["Check the main reduction against cited sources."],
                "source_requests": ["Need source locator for the main reduction."],
                "risks": ["Route may require an unstated hypothesis."],
                "status": "needs_human",
            },
            "verifier_reviews": [
                {
                    "verifier_id": f"{role}_{index:03d}",
                    "provider": provider,
                    "model": model,
                    "created_at": f"2026-06-21T00:00:0{index}+00:00",
                    "verdict": "needs_sources",
                    "objections": ["Candidate route still needs source grounding."],
                    "missing_hypotheses": ["State the exact boundedness hypothesis."],
                    "source_requests": ["Find a theorem locator for the reduction."],
                    "unsafe_language": [],
                    "scoped_objections": [],
                    "recommended_human_action": "revise",
                    "status": "needs_human",
                }
                for index, role in enumerate(ESPRESSO_REVIEWER_ROLES, start=1)
            ],
            "human_gate": {
                "gate_id": "human_gate_001",
                "status": "pending",
                "question": "Review candidate-only route before deeper automated work.",
                "allowed_decisions": ["continue", "revise", "stop"],
                "recommended_decision": "revise",
                "decision": None,
                "reason": None,
                "human_confirmed": False,
                "created_at": "2026-06-21T00:00:02+00:00",
                "resolved_at": None,
            },
            "provider_diagnostic": {
                "provider": provider,
                "model": model,
                "command_env": "ESPRESSO_EXTERNAL_AGENT_COMMAND",
                "command_present": True,
                "status": "ready" if provider_ready else "error",
                "network_check": "passed" if provider_ready else "failed",
                "message": "External JSON agent command returned schema-valid Espresso agent JSON.",
                "structured_output": {
                    "format": "json",
                    "schema_name": "espresso_agent_output",
                    "strict": True,
                    "store": False,
                    "adapter_diagnostic_key": "_espresso_codex_adapter",
                    "adapter_diagnostic": {
                        "adapter": "espresso_codex_agent",
                        "parse_status": "fallback" if adapter_fallback_used else "ok",
                        "fallback_used": adapter_fallback_used,
                        "fallback_reason": (
                            "adapter fallback for negative fixture"
                            if adapter_fallback_used
                            else ""
                        ),
                        "model": "gpt-5.5",
                        "effort": "medium",
                    },
                },
            },
            "agent_call_diagnostics": [
                {
                    "call_index": index,
                    "role": role,
                    "provider": provider,
                    "model": model,
                    "adapter": "espresso_codex_agent",
                    "parse_status": "fallback" if adapter_fallback_used else "ok",
                    "fallback_used": adapter_fallback_used,
                    "fallback_reason": (
                        "adapter fallback for negative fixture"
                        if adapter_fallback_used
                        else ""
                    ),
                    "request_id": f"fixture-request-{index}",
                    "verified_real_provider": (
                        provider == "external" and not adapter_fallback_used
                    ),
                }
                for index, role in enumerate(
                    ["prover", *ESPRESSO_REVIEWER_ROLES],
                    start=1,
                )
            ],
            "notes": [
                "Initial LLM agent runtime; outputs are candidate planning artifacts only.",
                "Reviewer set: espresso; roles: "
                + ", ".join(ESPRESSO_REVIEWER_ROLES)
                + ".",
            ],
        },
    )
    validate_run(run_dir)


def _steps(names: list[str], *, evidence_paths: list[str] | None = None) -> list[dict]:
    out = []
    for index, name in enumerate(names):
        out.append(
            {
                "name": name,
                "command": list(STEP_COMMANDS[name]),
                "status": "passed",
                "return_code": 0,
                "duration_seconds": 0.01,
                "missing_paths": [],
                "evidence_paths": evidence_paths if index == 0 and evidence_paths else [],
                "missing_evidence_paths": [],
                "note": "",
            }
        )
    return out


def _gate_report(
    *,
    scope: str,
    web_lane: bool,
    live_smoke: bool,
    steps: list[dict],
    skip_schema_diff: bool = False,
    output_root: Path | None = None,
    prompt_version: str = "test-goal70-audit",
) -> dict:
    duration_seconds_total = round(
        sum(float(step["duration_seconds"]) for step in steps),
        3,
    )
    return {
        "schema_version": 1,
        "generated_at": "2026-06-21T00:00:00+00:00",
        "repo": str(ROOT),
        "python": sys.executable,
        "status": "passed",
        "mode": "execute",
        "scope": scope,
        "web_lane": web_lane,
        "live_smoke": live_smoke,
        "skip_schema_diff": skip_schema_diff,
        "output_root": str(output_root or ROOT),
        "prompt_version": prompt_version,
        "duration_seconds_total": duration_seconds_total,
        "step_counts": {
            "total": len(steps),
            "passed": len(steps),
            "failed": 0,
            "dry_run": 0,
            "skipped_after_failure": 0,
        },
        "failed_steps": [],
        "steps": steps,
    }


def _capability_report(
    *,
    run_dir: Path | None = None,
    agent_solved: bool = False,
    overclaim: bool = False,
    checked_scoped_fact: bool = False,
    source_supported_argument: bool = False,
    formal_solution: bool = False,
    human_attested_solution: bool = False,
) -> dict:
    target_run_dir = run_dir or ROOT
    report = build_real_math_capability_report(
        [
            case_from_run(
                target_run_dir,
                case_id=target_run_dir.name,
                prompt_version="goal70-acceptance-smoke-v1",
                repeat_count=1,
            )
        ]
    )
    case = report["cases"][0]
    metrics = report["metrics"]
    case["overclaim"] = overclaim
    case["agent_solved"] = agent_solved
    case["checked_scoped_fact"] = checked_scoped_fact
    case["source_supported_argument"] = source_supported_argument
    case["formal_solution"] = formal_solution
    case["human_attested_solution"] = human_attested_solution
    metrics["overclaim_count"] = 1 if overclaim else 0
    metrics["agent_solved_rate"] = 1.0 if agent_solved else 0.0
    metrics["checked_scoped_fact_rate"] = 1.0 if checked_scoped_fact else 0.0
    metrics["source_supported_argument_rate"] = 1.0 if source_supported_argument else 0.0
    metrics["formal_solution_rate"] = 1.0 if formal_solution else 0.0
    metrics["human_attested_solution_rate"] = 1.0 if human_attested_solution else 0.0
    return report


def _write_complete_evidence(tmp_path: Path) -> tuple[Path, Path, Path]:
    live_run_dir = run_mock_critique("real_codex_smoke", SMOKE_INPUT, tmp_path)
    _write_live_run_shape(live_run_dir)
    non_live = _write_json(
        tmp_path / "non_live.json",
        _gate_report(
            scope="full non-live gate",
            web_lane=False,
            live_smoke=False,
            steps=_steps(NON_LIVE_STEPS),
        ),
    )
    web = _write_json(
        tmp_path / "web.json",
        _gate_report(
            scope="web-only",
            web_lane=True,
            live_smoke=False,
            steps=_steps(WEB_STEPS),
        ),
    )
    capability = _write_json(
        tmp_path / "goal70_real_agent_smoke_capability.json",
        _capability_report(run_dir=live_run_dir),
    )
    live_steps = _steps(
        ["real Codex external-agent smoke"],
        evidence_paths=[str(capability)],
    )
    live_steps[0]["command"] = [
        sys.executable,
        "-c",
        "from espresso_proof.cli import app; app()",
        "real-agent-smoke",
        "--project",
        "real_codex_smoke",
        "--input",
        str(SMOKE_INPUT),
        "--literature-mode",
        "local",
        "--max-references",
        "8",
        "--provider",
        "external",
        "--front-half",
        "mock",
        "--model",
        "codex-cli",
        "--api-key-env",
        "OPENAI_API_KEY",
        "--external-agent-command-env",
        "ESPRESSO_EXTERNAL_AGENT_COMMAND",
        "--reviewer-set",
        "espresso",
        "--output-root",
        str(tmp_path),
        "--prompt-version",
        "goal70-acceptance-smoke-v1",
        "--repeat-count",
        "1",
        "--json-out",
        str(capability),
    ]
    live = _write_json(
        tmp_path / "live.json",
        _gate_report(
            scope="live-smoke-only",
            web_lane=False,
            live_smoke=True,
            steps=live_steps,
            output_root=tmp_path,
            prompt_version="goal70-acceptance-smoke-v1",
        ),
    )
    return non_live, web, live


def _capability_path(tmp_path: Path) -> Path:
    return tmp_path / "goal70_real_agent_smoke_capability.json"


def _live_run_dir(tmp_path: Path) -> Path:
    return Path(
        json.loads(_capability_path(tmp_path).read_text(encoding="utf-8"))["cases"][0][
            "run_dir"
        ]
    )


def _run_audit(
    non_live: Path,
    web: Path,
    live: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--non-live",
            str(non_live),
            "--web",
            str(web),
            "--live-smoke",
            str(live),
            *extra,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_goal70_completion_audit_passes_complete_evidence(tmp_path: Path) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    audit_out = tmp_path / "audit.json"

    completed = _run_audit(non_live, web, live, "--json-out", str(audit_out))

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "Goal 70 completion audit PASSED" in completed.stdout
    audit = json.loads(audit_out.read_text(encoding="utf-8"))
    assert audit["status"] == "passed"
    assert audit["check_counts"]["failed"] == 0
    assert audit["failed_checks"] == []


def test_goal70_completion_audit_rejects_skipped_schema_diff(tmp_path: Path) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(non_live.read_text(encoding="utf-8"))
    payload["skip_schema_diff"] = True
    _write_json(non_live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "non-live schema drift checked" in completed.stdout
    assert "skip_schema_diff=True" in completed.stdout


def test_goal70_completion_audit_rejects_inconsistent_step_evidence(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(web.read_text(encoding="utf-8"))
    payload["step_counts"]["passed"] = 999
    payload["steps"][1]["missing_evidence_paths"] = ["web/dist/trace-proof.txt"]
    _write_json(web, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "web step counts match steps" in completed.stdout
    assert "web no missing paths recorded" in completed.stdout
    assert "missing_evidence_paths" in completed.stdout


def test_goal70_completion_audit_rejects_malformed_step_records(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(web.read_text(encoding="utf-8"))
    payload["steps"][0].pop("missing_paths")
    payload["steps"].append("not-a-step-record")
    payload["step_counts"] = {
        "total": len(payload["steps"]),
        "passed": len(payload["steps"]),
        "failed": 0,
        "dry_run": 0,
        "skipped_after_failure": 0,
    }
    _write_json(web, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "web step records have gate-report shape" in completed.stdout
    assert "missing_fields=missing_paths" in completed.stdout
    assert "steps[3] type=str" in completed.stdout


def test_goal70_completion_audit_rejects_non_list_failed_steps(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(web.read_text(encoding="utf-8"))
    payload["failed_steps"] = ""
    _write_json(web, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "web no failed steps" in completed.stdout
    assert "failed_steps= expected=[]" in completed.stdout


def test_goal70_completion_audit_rejects_boolean_step_counts(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    payload["step_counts"] = {
        "total": True,
        "passed": True,
        "failed": False,
        "dry_run": False,
        "skipped_after_failure": False,
    }
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live-smoke step counts match steps" in completed.stdout
    assert "total=True (bool)" in completed.stdout
    assert "failed=False (bool)" in completed.stdout


def test_goal70_completion_audit_rejects_boolean_step_return_code(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(web.read_text(encoding="utf-8"))
    payload["steps"][0]["return_code"] = False
    _write_json(web, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "web step records have gate-report shape" in completed.stdout
    assert "return_code_type=bool" in completed.stdout


def test_goal70_completion_audit_rejects_inconsistent_duration_total(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(non_live.read_text(encoding="utf-8"))
    payload["duration_seconds_total"] = 999.0
    _write_json(non_live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "non-live duration totals match steps" in completed.stdout
    assert "duration_seconds_total=999.0" in completed.stdout


def test_goal70_completion_audit_rejects_malformed_or_naive_generated_at(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    non_live_payload = json.loads(non_live.read_text(encoding="utf-8"))
    non_live_payload["generated_at"] = "not-a-timestamp"
    _write_json(non_live, non_live_payload)
    web_payload = json.loads(web.read_text(encoding="utf-8"))
    web_payload["generated_at"] = "2026-06-21T00:00:00"
    _write_json(web, web_payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "non-live generated_at is timezone-aware ISO timestamp" in completed.stdout
    assert "web generated_at is timezone-aware ISO timestamp" in completed.stdout
    assert "not-a-timestamp" in completed.stdout
    assert "timezone required" in completed.stdout


def test_goal70_completion_audit_rejects_report_from_other_checkout(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(non_live.read_text(encoding="utf-8"))
    payload["repo"] = str(tmp_path / "different-checkout")
    _write_json(non_live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "non-live report repo matches current checkout" in completed.stdout
    assert "different-checkout" in completed.stdout


def test_goal70_completion_audit_rejects_report_from_other_python(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(web.read_text(encoding="utf-8"))
    payload["python"] = str(tmp_path / "other-python.exe")
    _write_json(web, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "web report python matches current interpreter" in completed.stdout
    assert "other-python.exe" in completed.stdout


def test_goal70_completion_audit_rejects_python_step_from_other_interpreter(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(non_live.read_text(encoding="utf-8"))
    step = next(
        item
        for item in payload["steps"]
        if item["name"] == "agent-flow spec/status"
    )
    step["command"][0] = str(tmp_path / "other-python.exe")
    _write_json(non_live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert (
        "non-live python-backed step commands use current interpreter"
        in completed.stdout
    )
    assert "other-python.exe" in completed.stdout


def test_goal70_completion_audit_rejects_narrowed_python_step_arguments(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(non_live.read_text(encoding="utf-8"))
    step = next(
        item
        for item in payload["steps"]
        if item["name"] == "agent-flow spec/status"
    )
    insert_at = step["command"].index("-q") + 1
    step["command"][insert_at:insert_at] = ["-k", "not stage_status_negative"]
    _write_json(non_live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "non-live python-backed step command shape is exact" in completed.stdout
    assert "not stage_status_negative" in completed.stdout


def test_goal70_completion_audit_rejects_live_evidence_outside_output_root(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    payload["output_root"] = str(tmp_path / "live-output-root")
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live capability evidence path is under live output root" in completed.stdout
    assert "live-output-root" in completed.stdout


def test_goal70_completion_audit_rejects_live_evidence_noncanonical_filename(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    canonical = _capability_path(tmp_path)
    renamed = tmp_path / "renamed_live_capability.json"
    renamed.write_text(canonical.read_text(encoding="utf-8"), encoding="utf-8")
    payload = json.loads(live.read_text(encoding="utf-8"))
    payload["steps"][0]["evidence_paths"] = [str(renamed)]
    command = payload["steps"][0]["command"]
    command[command.index("--json-out") + 1] = str(renamed)
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live capability evidence path uses canonical filename" in completed.stdout
    assert "renamed_live_capability.json" in completed.stdout


def test_goal70_completion_audit_rejects_live_case_run_dir_outside_output_root(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    outside = tmp_path.parent / "other-goal70-run"
    outside.mkdir(exist_ok=True)
    capability_path = _capability_path(tmp_path)
    payload = json.loads(capability_path.read_text(encoding="utf-8"))
    payload["cases"][0]["run_dir"] = str(outside)
    payload["cases"][0]["case_id"] = "not-the-run-dir-name"
    _write_json(capability_path, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke case run directory is under live output root" in completed.stdout
    assert "live smoke case id matches run directory" in completed.stdout


def test_goal70_completion_audit_rejects_live_run_dir_without_manifest(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    (_live_run_dir(tmp_path) / "run_manifest.yaml").unlink()

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke run manifest exists" in completed.stdout
    assert "live smoke run manifest records llm agent artifact" in completed.stdout


def test_goal70_completion_audit_rejects_live_llm_artifact_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    _write_live_run_shape(
        _live_run_dir(tmp_path),
        provider="openai",
        model="different-model",
        provider_ready=False,
        adapter_fallback_used=True,
    )

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke llm agent artifact matches capability row" in completed.stdout
    assert "live smoke llm agent diagnostic matches capability row" in completed.stdout


def test_goal70_completion_audit_rejects_invalid_live_llm_agent_schema(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    agent_path = _live_run_dir(tmp_path) / "llm_agent_run.yaml"
    agent = read_yaml(agent_path)
    agent["verifier_reviews"] = []
    write_yaml(agent_path, agent)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke llm agent run validates against kernel schema" in completed.stdout
    assert "at least one verifier review" in completed.stdout


def test_goal70_completion_audit_rejects_non_codex_adapter_diagnostic(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    agent_path = _live_run_dir(tmp_path) / "llm_agent_run.yaml"
    agent = read_yaml(agent_path)
    structured = agent["provider_diagnostic"]["structured_output"]
    structured["adapter_diagnostic"]["adapter"] = "other_external_agent"
    write_yaml(agent_path, agent)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke provider diagnostic proves Codex adapter path" in completed.stdout
    assert "other_external_agent" in completed.stdout


def test_goal70_completion_audit_rejects_non_espresso_reviewer_artifact(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    agent_path = _live_run_dir(tmp_path) / "llm_agent_run.yaml"
    agent = read_yaml(agent_path)
    agent["verifier_reviews"] = agent["verifier_reviews"][:1]
    agent["notes"] = [
        note for note in agent["notes"] if not note.startswith("Reviewer set:")
    ]
    write_yaml(agent_path, agent)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke llm agent run records espresso reviewer set" in completed.stdout
    assert "verifier_ids" in completed.stdout


def test_goal70_completion_audit_rejects_live_run_identity_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    run_dir = _live_run_dir(tmp_path)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest["run_id"] = "different-manifest-run"
    write_yaml(manifest_path, manifest)
    agent_path = run_dir / "llm_agent_run.yaml"
    agent = read_yaml(agent_path)
    agent["run_id"] = "different-agent-run"
    write_yaml(agent_path, agent)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke run identity matches manifest and llm artifact" in completed.stdout
    assert "different-manifest-run" in completed.stdout
    assert "different-agent-run" in completed.stdout


def test_goal70_completion_audit_rejects_live_run_that_fails_kernel_validation(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    run_dir = _live_run_dir(tmp_path)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest["artifacts"]["locked_claim"] = "missing_locked_claim.yaml"
    write_yaml(manifest_path, manifest)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke run validates through kernel" in completed.stdout
    assert "missing_locked_claim.yaml" in completed.stdout


def test_goal70_completion_audit_rejects_non_recomputable_live_capability(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    capability_path = _capability_path(tmp_path)
    payload = json.loads(capability_path.read_text(encoding="utf-8"))
    payload["metrics"]["useful_route_rate"] = (
        0.0 if payload["metrics"]["useful_route_rate"] == 1.0 else 1.0
    )
    payload["cases"][0]["useful_route_found"] = not payload["cases"][0][
        "useful_route_found"
    ]
    _write_json(capability_path, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live capability report matches recomputed run capability" in completed.stdout
    assert "useful_route" in completed.stdout


def test_goal70_completion_audit_rejects_duplicate_or_extra_steps(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(non_live.read_text(encoding="utf-8"))
    payload["steps"].append(dict(payload["steps"][0]))
    payload["steps"].append(
        {
            "name": "narrow smoke masquerading as helpful extra",
            "command": [sys.executable, "-m", "pytest", "-q", "tests/test_goal70_completion_audit.py"],
            "status": "passed",
            "return_code": 0,
            "duration_seconds": 0.01,
            "missing_paths": [],
            "evidence_paths": [],
            "missing_evidence_paths": [],
            "note": "",
        }
    )
    payload["step_counts"] = {
        "total": len(payload["steps"]),
        "passed": len(payload["steps"]),
        "failed": 0,
        "dry_run": 0,
        "skipped_after_failure": 0,
    }
    _write_json(non_live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "non-live step set is exact" in completed.stdout
    assert "narrow smoke masquerading" in completed.stdout
    assert "agent-flow spec/status" in completed.stdout


def test_goal70_completion_audit_rejects_spoofed_required_step_command(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(non_live.read_text(encoding="utf-8"))
    step = next(item for item in payload["steps"] if item["name"] == "hard invariant matrix")
    step["command"] = [sys.executable, "-m", "pytest", "-q", "tests/test_goal70_completion_audit.py"]
    _write_json(non_live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "non-live required step commands cover expected targets" in completed.stdout
    assert "hard invariant matrix: tests/test_workflow_v2_claim_clear_gate.py" in completed.stdout


def test_goal70_completion_audit_rejects_web_test_command_suffix_spoof(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(web.read_text(encoding="utf-8"))
    step = next(item for item in payload["steps"] if item["name"] == "web cockpit trace tests")
    step["command"] = [sys.executable, "-m", "pytest"]
    _write_json(web, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "web required step commands cover expected targets" in completed.stdout
    assert "web cockpit trace tests: test" in completed.stdout


def test_goal70_completion_audit_rejects_web_commands_without_workspace_prefix(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(web.read_text(encoding="utf-8"))
    by_name = {step["name"]: step for step in payload["steps"]}
    by_name["web cockpit trace tests"]["command"] = ["echo", "test"]
    by_name["web cockpit typecheck"]["command"] = ["echo", "run", "typecheck"]
    _write_json(web, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "web cockpit commands share web workspace prefix" in completed.stdout
    assert "does not target web workspace" in completed.stdout


def test_goal70_completion_audit_rejects_echo_web_command_prefix(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(web.read_text(encoding="utf-8"))
    by_name = {step["name"]: step for step in payload["steps"]}
    by_name["web cockpit trace tests"]["command"] = ["echo", "web", "test"]
    by_name["web cockpit typecheck"]["command"] = [
        "echo",
        "web",
        "run",
        "typecheck",
    ]
    _write_json(web, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "web cockpit commands share web workspace prefix" in completed.stdout
    assert "unapproved web runner prefix" in completed.stdout
    assert "echo" in completed.stdout


def test_goal70_completion_audit_accepts_bundled_pnpm_web_command_prefix(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(web.read_text(encoding="utf-8"))
    prefix = [
        str(tmp_path / "node" / "bin" / "node.exe"),
        str(tmp_path / "node" / "node_modules" / "pnpm" / "bin" / "pnpm.cjs"),
        "--config.verify-deps-before-run=false",
        "--dir",
        "web",
    ]
    by_name = {step["name"]: step for step in payload["steps"]}
    by_name["web cockpit trace tests"]["command"] = [*prefix, "test"]
    by_name["web cockpit typecheck"]["command"] = [*prefix, "run", "typecheck"]
    _write_json(web, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_goal70_completion_audit_rejects_live_command_option_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    command[command.index("--output-root") + 1] = str(tmp_path / "other-output-root")
    command[command.index("--prompt-version") + 1] = "different-command-prompt"
    command[command.index("--json-out") + 1] = str(tmp_path / "other-output-root" / "capability.json")
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command output root matches report" in completed.stdout
    assert "live smoke command json-out matches evidence path" in completed.stdout
    assert "live smoke command prompt version matches report" in completed.stdout


def test_goal70_completion_audit_rejects_live_command_entrypoint_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    command[2] = "from other_package.cli import app; app()"
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command invokes checked-in Espresso CLI" in completed.stdout
    assert "other_package.cli" in completed.stdout


def test_goal70_completion_audit_rejects_live_command_extra_option(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    insert_at = command.index("--json-out")
    command[insert_at:insert_at] = ["--unexpected-live-flag", "true"]
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command option set is exact" in completed.stdout
    assert "--unexpected-live-flag" in completed.stdout


def test_goal70_completion_audit_rejects_live_command_extra_positional_token(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    command.insert(4, "unexpected-positional-token")
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command option-value shape is exact" in completed.stdout
    assert "unexpected-positional-token" in completed.stdout


def test_goal70_completion_audit_rejects_live_command_input_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    other_input = tmp_path / "other-input.md"
    other_input.write_text("A different smoke problem.\n", encoding="utf-8")
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    command[command.index("--input") + 1] = str(other_input)
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command input matches canonical fixture" in completed.stdout
    assert "other-input.md" in completed.stdout


def test_goal70_completion_audit_rejects_live_command_project_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    command[command.index("--project") + 1] = "other_smoke_project"
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command project is real Codex smoke" in completed.stdout
    assert "live smoke manifest project matches command project" in completed.stdout
    assert "other_smoke_project" in completed.stdout


def test_goal70_completion_audit_rejects_live_command_provider_model_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    command[command.index("--provider") + 1] = "openai"
    command[command.index("--model") + 1] = "not-codex-cli"
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command provider is external" in completed.stdout
    assert "live smoke command model selects Codex CLI adapter" in completed.stdout
    assert "command_provider=openai" in completed.stdout
    assert "command_model=not-codex-cli" in completed.stdout


def test_goal70_completion_audit_rejects_live_command_external_agent_env_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    command[command.index("--external-agent-command-env") + 1] = "OTHER_AGENT_COMMAND"
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command external agent env is explicit" in completed.stdout
    assert (
        "live smoke provider diagnostic command env matches command"
        in completed.stdout
    )
    assert "OTHER_AGENT_COMMAND" in completed.stdout


def test_goal70_completion_audit_rejects_live_command_api_key_env_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    command[command.index("--api-key-env") + 1] = "OTHER_OPENAI_KEY"
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command api key env is explicit" in completed.stdout
    assert "command_api_key_env=OTHER_OPENAI_KEY" in completed.stdout


def test_goal70_completion_audit_rejects_live_diagnostic_command_env_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    agent_path = _live_run_dir(tmp_path) / "llm_agent_run.yaml"
    agent = read_yaml(agent_path)
    agent["provider_diagnostic"]["command_env"] = "OTHER_AGENT_COMMAND"
    write_yaml(agent_path, agent)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert (
        "live smoke provider diagnostic command env matches command"
        in completed.stdout
    )
    assert "diagnostic_command_env=OTHER_AGENT_COMMAND" in completed.stdout


def test_goal70_completion_audit_rejects_live_command_mode_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    command[command.index("--literature-mode") + 1] = "live"
    command[command.index("--front-half") + 1] = "external"
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command literature mode is local" in completed.stdout
    assert "live smoke command front half is mock" in completed.stdout
    assert "command_literature_mode=live" in completed.stdout
    assert "command_front_half=external" in completed.stdout


def test_goal70_completion_audit_rejects_live_command_max_references_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    command[command.index("--max-references") + 1] = "3"
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command max references is fixed" in completed.stdout
    assert "command_max_references=3" in completed.stdout


def test_goal70_completion_audit_rejects_live_command_literature_provider(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    insert_at = command.index("--literature-mode") + 2
    command[insert_at:insert_at] = ["--literature-provider", "arxiv"]
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command has no live literature providers" in completed.stdout
    assert "arxiv" in completed.stdout


def test_goal70_completion_audit_rejects_live_command_reviewer_set_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    command[command.index("--reviewer-set") + 1] = "generic"
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command reviewer set is espresso" in completed.stdout
    assert "command_reviewer_set=generic" in completed.stdout


def test_goal70_completion_audit_rejects_live_command_repeat_count_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    payload = json.loads(live.read_text(encoding="utf-8"))
    command = payload["steps"][0]["command"]
    command[command.index("--repeat-count") + 1] = "2"
    _write_json(live, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke command repeat count matches capability case" in completed.stdout
    assert "command_repeat_count=2" in completed.stdout


def test_goal70_completion_audit_rejects_live_manifest_project_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    run_dir = _live_run_dir(tmp_path)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest["project_id"] = "other_smoke_project"
    write_yaml(manifest_path, manifest)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke manifest project matches command project" in completed.stdout
    assert "manifest_project_id=other_smoke_project" in completed.stdout


def test_goal70_completion_audit_rejects_live_run_input_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    (_live_run_dir(tmp_path) / "input.md").write_text(
        "A different persisted smoke problem.\n",
        encoding="utf-8",
    )

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke run input matches canonical fixture" in completed.stdout


def test_goal70_completion_audit_rejects_live_overclaim_or_solver_claim(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    capability_path = _capability_path(tmp_path)
    live_run_dir = _live_run_dir(tmp_path)
    _write_json(
        capability_path,
        _capability_report(run_dir=live_run_dir, agent_solved=True, overclaim=True),
    )

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live capability metrics are honest" in completed.stdout
    assert "live smoke does not claim agent solved" in completed.stdout


def test_goal70_completion_audit_rejects_live_capability_inconsistency(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    capability_path = _capability_path(tmp_path)
    _write_json(
        capability_path,
        _capability_report(
            run_dir=_live_run_dir(tmp_path),
            agent_solved=False,
            source_supported_argument=True,
            human_attested_solution=True,
        ),
    )

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live capability metrics do not claim solution capability" in completed.stdout
    assert "live smoke case preserves capability separation" in completed.stdout
    assert (
        "live smoke agent_solved is derived only from machine/source/formal labels"
        in completed.stdout
    )


def test_goal70_completion_audit_rejects_live_prompt_version_mismatch(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    capability_path = _capability_path(tmp_path)
    payload = _capability_report(run_dir=_live_run_dir(tmp_path))
    payload["cases"][0]["prompt_version"] = "different-live-prompt-v2"
    _write_json(capability_path, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke prompt version matches gate report" in completed.stdout
    assert "different-live-prompt-v2" in completed.stdout


def test_goal70_completion_audit_rejects_missing_live_provenance(
    tmp_path: Path,
) -> None:
    non_live, web, live = _write_complete_evidence(tmp_path)
    capability_path = _capability_path(tmp_path)
    payload = _capability_report(run_dir=_live_run_dir(tmp_path))
    payload["cases"][0].pop("prompt_version")
    payload["cases"][0]["provider"] = "stub"
    _write_json(capability_path, payload)

    completed = _run_audit(non_live, web, live)

    assert completed.returncode != 0
    assert "live smoke case records benchmark provenance" in completed.stdout
    assert "provider=stub" in completed.stdout
