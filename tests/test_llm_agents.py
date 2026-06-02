from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.io import read_yaml
from espresso_proof.llm_agents import OpenAIResponsesClient, collect_unsafe_language, safe_text
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.models import LLMAgentRunReport
from espresso_proof.validators import validate_run


FIXTURES = Path("tests/fixtures")


def external_agent_command(tmp_path: Path) -> str:
    script = tmp_path / "external_agent.py"
    script.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "request = json.loads(sys.stdin.read())",
                "json.dump({",
                "  'summary': 'External command returned candidate-only output.',",
                "  'steps': ['Check exact source hypotheses.'],",
                "  'source_requests': ['Exact theorem locator.'],",
                "  'missing_hypotheses': ['Genericity condition.'],",
                "  'risks': ['Route may be too narrow.'],",
                "  'verdict': 'needs_sources',",
                "  'recommended_human_action': 'revise',",
                "}, sys.stdout)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return f"{sys.executable} {script}"


def test_stub_llm_agents_write_schema_valid_candidate_artifact(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-llm-agents",
            "--run",
            str(run_dir),
            "--provider",
            "stub",
            "--model",
            "stub-espresso-agent",
        ],
    )

    assert result.exit_code == 0, result.output
    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["llm_agent_run"] == "llm_agent_run.yaml"
    report = LLMAgentRunReport.model_validate(read_yaml(run_dir / "llm_agent_run.yaml"))
    assert report.prover_attempt.status == "needs_human"
    assert report.verifier_reviews[0].verdict == "needs_sources"
    assert report.verifier_reviews[0].missing_hypotheses
    assert report.provider_diagnostic is None
    assert report.human_gate.status == "pending"
    assert report.human_gate.human_confirmed is False


def test_stub_espresso_reviewer_set_runs_three_role_reviewers(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-llm-agents",
            "--run",
            str(run_dir),
            "--provider",
            "stub",
            "--reviewer-set",
            "espresso",
        ],
    )

    assert result.exit_code == 0, result.output
    validate_run(run_dir)
    report = LLMAgentRunReport.model_validate(read_yaml(run_dir / "llm_agent_run.yaml"))
    reviewer_ids = {review.verifier_id for review in report.verifier_reviews}
    assert reviewer_ids == {
        "strategy_reviewer_001",
        "evidence_reviewer_002",
        "line_proof_reviewer_003",
    }
    assert any("Reviewer set: espresso" in note for note in report.notes)
    assert read_yaml(run_dir / "idea_grade.yaml")["status"] == "promising"


def test_run_flow_creates_literature_and_espresso_agent_artifacts(tmp_path: Path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-flow",
            "--input",
            str(FIXTURES / "promising_but_unproved" / "input.md"),
            "--project",
            "hodge_atoms",
            "--literature-mode",
            "local",
            "--provider",
            "stub",
            "--reviewer-set",
            "espresso",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    match = re.search(r"Wrote run artifacts to\s+(.+)", result.output)
    assert match, result.output
    run_dir = Path(match.group(1).strip())
    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["current_state"] == "WAITING_LLM_AGENT_HUMAN_GATE"
    assert manifest["optional_artifacts"]["llm_agent_run"] == "llm_agent_run.yaml"
    literature = read_yaml(run_dir / "literature_review.yaml")
    assert literature["candidate_references"]
    assert {item["status"] for item in literature["candidate_references"]}.issubset(
        {"candidate", "unverified", "not_found"}
    )
    report = LLMAgentRunReport.model_validate(read_yaml(run_dir / "llm_agent_run.yaml"))
    assert {review.verifier_id for review in report.verifier_reviews} == {
        "strategy_reviewer_001",
        "evidence_reviewer_002",
        "line_proof_reviewer_003",
    }
    assert report.human_gate.status == "pending"


def test_run_flow_openai_provider_requires_api_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-flow",
            "--input",
            str(FIXTURES / "promising_but_unproved" / "input.md"),
            "--project",
            "hodge_atoms",
            "--literature-mode",
            "local",
            "--provider",
            "openai",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code != 0
    assert "OPENAI_API_KEY is not set" in str(result.exception)
    assert not list(tmp_path.glob("projects/*/runs/*/run_manifest.yaml"))


def test_run_flow_openai_network_preflight_fails_before_writing_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_urlopen(request, timeout: int):
        raise RuntimeError("diagnostic network unavailable")

    monkeypatch.setattr("espresso_proof.llm_agents.urllib.request.urlopen", fake_urlopen)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-flow",
            "--input",
            str(FIXTURES / "promising_but_unproved" / "input.md"),
            "--project",
            "hodge_atoms",
            "--literature-mode",
            "local",
            "--provider",
            "openai",
            "--agent-network-preflight",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code != 0
    assert "diagnostic network unavailable" in str(result.exception)
    assert not list(tmp_path.glob("projects/*/runs/*/run_manifest.yaml"))


def test_run_flow_openai_network_preflight_writes_provider_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls: list[dict] = []
    response_text = json.dumps(
        {
            "summary": "Candidate-only diagnostic and route.",
            "steps": ["Request exact theorem locators."],
            "source_requests": ["Exact source locator."],
            "missing_hypotheses": ["Genericity condition."],
            "risks": ["Route may be too narrow."],
            "verdict": "needs_sources",
            "recommended_human_action": "revise",
        }
    )

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, exc_type, exc, traceback) -> bool:
            return False

        def read(self) -> bytes:
            return json.dumps({"output_text": response_text}).encode("utf-8")

    def fake_urlopen(request, timeout: int):
        calls.append(json.loads(request.data.decode("utf-8")))
        return FakeResponse()

    monkeypatch.setattr("espresso_proof.llm_agents.urllib.request.urlopen", fake_urlopen)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-flow",
            "--input",
            str(FIXTURES / "promising_but_unproved" / "input.md"),
            "--project",
            "hodge_atoms",
            "--literature-mode",
            "local",
            "--provider",
            "openai",
            "--reviewer-set",
            "espresso",
            "--agent-network-preflight",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    match = re.search(r"Wrote run artifacts to\s+(.+)", result.output)
    assert match, result.output
    run_dir = Path(match.group(1).strip())
    validate_run(run_dir)
    report = LLMAgentRunReport.model_validate(read_yaml(run_dir / "llm_agent_run.yaml"))
    assert report.provider == "openai"
    assert report.provider_diagnostic is not None
    assert report.provider_diagnostic.status == "ready"
    assert report.provider_diagnostic.network_check == "passed"
    assert report.provider_diagnostic.structured_output["store"] is False
    assert len(calls) == 5
    assert calls[0]["input"].startswith("Return a minimal candidate-only Espresso agent response")
    assert all(call["store"] is False for call in calls)


def test_run_flow_external_provider_uses_json_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", external_agent_command(tmp_path))
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-flow",
            "--input",
            str(FIXTURES / "promising_but_unproved" / "input.md"),
            "--project",
            "hodge_atoms",
            "--literature-mode",
            "local",
            "--provider",
            "external",
            "--model",
            "chatgpt-account-adapter",
            "--reviewer-set",
            "espresso",
            "--agent-network-preflight",
            "--output-root",
            str(tmp_path / "runs"),
        ],
    )

    assert result.exit_code == 0, result.output
    match = re.search(r"Wrote run artifacts to\s+(.+)", result.output)
    assert match, result.output
    run_dir = Path(match.group(1).strip())
    validate_run(run_dir)
    report = LLMAgentRunReport.model_validate(read_yaml(run_dir / "llm_agent_run.yaml"))
    assert report.provider == "external"
    assert report.model == "chatgpt-account-adapter"
    assert report.provider_diagnostic is not None
    assert report.provider_diagnostic.provider == "external"
    assert report.provider_diagnostic.status == "ready"
    assert report.provider_diagnostic.network_check == "passed"
    assert {review.verifier_id for review in report.verifier_reviews} == {
        "strategy_reviewer_001",
        "evidence_reviewer_002",
        "line_proof_reviewer_003",
    }


def test_bridge_run_flow_creates_selectable_run(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/espresso_vscode_bridge.py",
            "run-flow",
            "--workspace",
            str(tmp_path),
            "--project",
            "hodge_atoms",
            "--hypothesis",
            "Promising but not proved Hodge atom sketch for cubic fourfolds.",
            "--literature-mode",
            "local",
            "--provider",
            "stub",
            "--reviewer-set",
            "espresso",
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    run_dir = Path(payload["payload"]["run"])
    validate_run(run_dir)
    assert read_yaml(run_dir / "run_manifest.yaml")["optional_artifacts"]["llm_agent_run"] == "llm_agent_run.yaml"


def test_bridge_run_flow_openai_missing_key_does_not_write_run(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/espresso_vscode_bridge.py",
            "run-flow",
            "--workspace",
            str(tmp_path),
            "--project",
            "hodge_atoms",
            "--hypothesis",
            "Promising but not proved Hodge atom sketch for cubic fourfolds.",
            "--literature-mode",
            "local",
            "--provider",
            "openai",
            "--agent-network-preflight",
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "OPENAI_API_KEY is not set" in payload["error"]
    assert not list(tmp_path.glob("projects/*/runs/*/run_manifest.yaml"))


def test_bridge_run_flow_external_provider_creates_selectable_run(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["ESPRESSO_EXTERNAL_AGENT_COMMAND"] = external_agent_command(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "scripts/espresso_vscode_bridge.py",
            "run-flow",
            "--workspace",
            str(workspace),
            "--project",
            "hodge_atoms",
            "--hypothesis",
            "Promising but not proved Hodge atom sketch for cubic fourfolds.",
            "--literature-mode",
            "local",
            "--provider",
            "external",
            "--model",
            "chatgpt-account-adapter",
            "--reviewer-set",
            "espresso",
            "--agent-network-preflight",
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    run_dir = Path(payload["payload"]["run"])
    validate_run(run_dir)
    report = LLMAgentRunReport.model_validate(read_yaml(run_dir / "llm_agent_run.yaml"))
    assert report.provider == "external"
    assert report.provider_diagnostic is not None
    assert report.provider_diagnostic.status == "ready"


def test_bridge_snapshot_reports_missing_openai_key(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/espresso_vscode_bridge.py",
            "snapshot",
            "--workspace",
            str(tmp_path),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    capability = next(
        item for item in payload["payload"]["capabilities"] if item["label"] == "OpenAI provider key"
    )
    assert capability["status"] == "missing OPENAI_API_KEY"


def test_agent_doctor_reports_missing_openai_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["agent-doctor", "--provider", "openai"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "missing_key"
    assert payload["api_key_present"] is False
    assert payload["network_check"] == "skipped"


def test_agent_doctor_external_reports_missing_command(monkeypatch) -> None:
    monkeypatch.delenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["agent-doctor", "--provider", "external"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["provider"] == "external"
    assert payload["status"] == "missing_command"
    assert payload["command_present"] is False


def test_agent_doctor_external_network_check_uses_json_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", external_agent_command(tmp_path))
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "agent-doctor",
            "--provider",
            "external",
            "--model",
            "chatgpt-account-adapter",
            "--network-check",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["provider"] == "external"
    assert payload["model"] == "chatgpt-account-adapter"
    assert payload["status"] == "ready"
    assert payload["network_check"] == "passed"
    assert payload["sample_verdict"] == "needs_sources"


def test_bridge_agent_doctor_reports_missing_openai_key(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("OPENAI_API_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/espresso_vscode_bridge.py",
            "agent-doctor",
            "--workspace",
            str(tmp_path),
            "--provider",
            "openai",
            "--network-check",
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    diagnostic = payload["payload"]["diagnostic"]
    assert diagnostic["status"] == "missing_key"
    assert diagnostic["network_check"] == "not_run"


def test_resolve_agent_gate_does_not_upgrade_grade_or_source(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run-llm-agents",
            "--run",
            str(run_dir),
            "--provider",
            "stub",
        ],
    )
    assert result.exit_code == 0, result.output
    before_grade = read_yaml(run_dir / "idea_grade.yaml")
    before_grounding = read_yaml(run_dir / "source_grounding.yaml")

    resolved = runner.invoke(
        app,
        [
            "resolve-agent-gate",
            "--run",
            str(run_dir),
            "--decision",
            "revise",
            "--reason",
            "Need exact source locator before continuing.",
        ],
    )

    assert resolved.exit_code == 0, resolved.output
    validate_run(run_dir)
    report = LLMAgentRunReport.model_validate(read_yaml(run_dir / "llm_agent_run.yaml"))
    assert report.human_gate.status == "resolved"
    assert report.human_gate.decision == "revise"
    assert report.human_gate.human_confirmed is True
    assert read_yaml(run_dir / "idea_grade.yaml") == before_grade
    assert read_yaml(run_dir / "source_grounding.yaml") == before_grounding


def test_openai_provider_requires_api_key(tmp_path: Path, monkeypatch) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-llm-agents",
            "--run",
            str(run_dir),
            "--provider",
            "openai",
        ],
    )

    assert result.exit_code != 0
    assert "OPENAI_API_KEY is not set" in str(result.exception)


def test_openai_client_uses_responses_structured_outputs_without_storing(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured: dict[str, object] = {}
    response_text = json.dumps(
        {
            "summary": "Candidate only.",
            "steps": ["Check source hypotheses."],
            "source_requests": ["Exact theorem locator."],
            "missing_hypotheses": ["Genericity condition."],
            "risks": ["Route may be too narrow."],
            "verdict": "needs_sources",
            "recommended_human_action": "revise",
        }
    )

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, exc_type, exc, traceback) -> bool:
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "output": [
                        {
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": response_text,
                                }
                            ]
                        }
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout: int):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["authorization"] = request.get_header("Authorization")
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("espresso_proof.llm_agents.urllib.request.urlopen", fake_urlopen)
    client = OpenAIResponsesClient(model="gpt-4.1", timeout=12)

    result = client.complete_json(system="Return JSON only.", prompt="Check this sketch.")

    assert result["verdict"] == "needs_sources"
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["timeout"] == 12
    assert captured["authorization"] == "Bearer test-key"
    payload = captured["payload"]
    assert payload["model"] == "gpt-4.1"
    assert payload["instructions"] == "Return JSON only."
    assert payload["input"] == "Check this sketch."
    assert payload["store"] is False
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["name"] == "espresso_agent_output"


def test_agent_doctor_network_check_uses_openai_structured_output(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured: dict[str, object] = {}
    response_text = json.dumps(
        {
            "summary": "Diagnostic candidate only.",
            "steps": ["Return schema-valid JSON."],
            "source_requests": ["No source request for diagnostic."],
            "missing_hypotheses": [],
            "risks": ["Diagnostic only."],
            "verdict": "needs_sources",
            "recommended_human_action": "revise",
        }
    )

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, exc_type, exc, traceback) -> bool:
            return False

        def read(self) -> bytes:
            return json.dumps({"output_text": response_text}).encode("utf-8")

    def fake_urlopen(request, timeout: int):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("espresso_proof.llm_agents.urllib.request.urlopen", fake_urlopen)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "agent-doctor",
            "--provider",
            "openai",
            "--model",
            "gpt-4.1",
            "--network-check",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ready"
    assert payload["network_check"] == "passed"
    assert payload["sample_verdict"] == "needs_sources"
    assert captured["url"] == "https://api.openai.com/v1/responses"
    request_payload = captured["payload"]
    assert request_payload["store"] is False
    assert request_payload["text"]["format"]["type"] == "json_schema"


def test_safe_language_filter_preserves_unverified() -> None:
    text = safe_text("This remains unverified; do not say proof-close.")

    assert "unverified" in text
    assert "proof-close" not in text
    assert "proof-close" in collect_unsafe_language({"summary": "proof-close", "steps": [], "risks": []})
    assert "verified" not in collect_unsafe_language(
        {"summary": "This remains unverified.", "steps": [], "risks": []}
    )
