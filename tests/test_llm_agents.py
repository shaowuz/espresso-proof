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
from espresso_proof.io import write_yaml
from espresso_proof.llm_agents import (
    OpenAIResponsesClient,
    StubLLMClient,
    collect_unsafe_language,
    make_prover_attempt,
    make_verifier_review,
    safe_text,
)
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.models import (
    LLMAgentAttempt,
    LLMAgentRunReport,
    LLMAgentVerifierReview,
    ProverLoopReport,
    ProverRoundIndex,
    ProverRoundReport,
)
from espresso_proof.validators import validate_run
from espresso_proof.validators import KernelValidationError


FIXTURES = Path("tests/fixtures")


def _mark_claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)


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
    _mark_claim_clear(run_dir)
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
    assert len(report.verifier_reviews) == 3
    assert {review.verifier_id for review in report.verifier_reviews} == {
        "strategy_reviewer_001",
        "evidence_reviewer_002",
        "line_proof_reviewer_003",
    }
    assert any(review.verdict == "needs_sources" for review in report.verifier_reviews)
    assert any(review.missing_hypotheses for review in report.verifier_reviews)
    assert report.provider_diagnostic is None
    assert report.human_gate.status == "pending"
    assert report.human_gate.human_confirmed is False
    loop = ProverLoopReport.model_validate(read_yaml(run_dir / "prover_loop.yaml"))
    assert loop.human_gate_status == "pending"
    assert loop.loop_decision == "ask_human"
    assert loop.budget.rounds_used == 1
    assert len(loop.attempts) >= 1
    assert loop.attempts[0].time_allocation == "ask_human"
    index = ProverRoundIndex.model_validate(read_yaml(run_dir / "prover_rounds/index.yaml"))
    assert manifest["optional_artifacts"]["prover_round_index"] == "prover_rounds/index.yaml"
    assert len(index.rounds) == len(loop.attempts)
    for entry in index.rounds:
        round_report = ProverRoundReport.model_validate(read_yaml(run_dir / entry.artifact_path))
        assert round_report.loop_attempt_id == entry.loop_attempt_id
        assert round_report.source == "initial_llm_agent_run"
        assert round_report.human_gate_status == "pending"
        assert round_report.safety_note


def test_inspect_reports_prover_loop_and_theorem_applicability(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    runner = CliRunner()
    applicability = runner.invoke(app, ["audit-theorem-applicability", "--run", str(run_dir)])
    assert applicability.exit_code == 0, applicability.output
    llm = runner.invoke(
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
    assert llm.exit_code == 0, llm.output
    validate_run(run_dir)

    inspected = runner.invoke(app, ["inspect", "--run", str(run_dir)])

    assert inspected.exit_code == 0, inspected.output
    assert "Theorem Applicability" in inspected.output
    assert "blocking usage" in inspected.output
    assert "condition unclear" in inspected.output
    assert "applicability audit is not proof status" in inspected.output
    assert "Prover Loop" in inspected.output
    assert "decision ask_human" in inspected.output
    assert "reviewer strategy_reviewer_001" in inspected.output
    assert "A sketch-only argument cannot become proof-complete evidence." in inspected.output
    assert "durable round artifacts:" in inspected.output
    assert "latest attempt:" in inspected.output
    assert "proved the theorem" not in inspected.output.lower()
    assert "proof complete" not in inspected.output.lower()


def test_inspect_focuses_selected_strategy_attempt(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    runner = CliRunner()
    chosen = runner.invoke(
        app,
        [
            "choose-strategy",
            "--run",
            str(run_dir),
            "--strategy",
            "strategy_pin_reference",
            "--effort",
            "quick_test",
            "--decision",
            "continue",
            "--reason",
            "Spend the next cheap check on pinning the exact theorem.",
        ],
    )
    assert chosen.exit_code == 0, chosen.output
    llm = runner.invoke(
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
    assert llm.exit_code == 0, llm.output

    inspected = runner.invoke(app, ["inspect", "--run", str(run_dir)])

    assert inspected.exit_code == 0, inspected.output
    assert "strategy strategy_pin_reference" in inspected.output
    assert "attempt strategy strategy_pin_reference" in inspected.output
    assert "attempt strategy strategy_stress_test_specials" not in inspected.output
    assert "Exact locator for every external theorem step." in inspected.output


def test_inspect_ignores_undeclared_optional_artifact_files(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    write_yaml(run_dir / "theorem_applicability.yaml", {"overall_status": "misapplied"})
    write_yaml(run_dir / "prover_loop.yaml", {"loop_decision": "continue"})
    validate_run(run_dir)

    inspected = CliRunner().invoke(app, ["inspect", "--run", str(run_dir)])

    assert inspected.exit_code == 0, inspected.output
    assert "Theorem Applicability" not in inspected.output
    assert "Prover Loop" not in inspected.output


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
    loop = ProverLoopReport.model_validate(read_yaml(run_dir / "prover_loop.yaml"))
    assert manifest["optional_artifacts"]["prover_loop"] == "prover_loop.yaml"
    assert {attempt.strategy_id for attempt in loop.attempts}.issubset(
        {strategy["id"] for strategy in read_yaml(run_dir / "strategy_brief.yaml")["top_strategies"]}
    )
    assert loop.loop_decision == "ask_human"


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


def test_bridge_snapshot_exposes_prover_loop_and_theorem_applicability(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    runner = CliRunner()
    applicability = runner.invoke(app, ["audit-theorem-applicability", "--run", str(run_dir)])
    assert applicability.exit_code == 0, applicability.output
    llm = runner.invoke(
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
    assert llm.exit_code == 0, llm.output

    result = subprocess.run(
        [
            sys.executable,
            "scripts/espresso_vscode_bridge.py",
            "snapshot",
            "--workspace",
            str(tmp_path),
            "--run",
            str(run_dir),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    current = payload["payload"]["current"]
    assert current["artifacts"]["prover_loop"]["loop_decision"] == "ask_human"
    assert len(current["artifacts"]["prover_round_index"]["rounds"]) >= 1
    assert current["artifacts"]["theorem_applicability"]["overall_status"] == "unclear"
    assert current["artifact_paths"]["prover_loop"].endswith("prover_loop.yaml")
    assert current["artifact_paths"]["prover_round_index"].endswith("prover_rounds/index.yaml")
    assert current["artifact_paths"]["theorem_applicability"].endswith("theorem_applicability.yaml")
    assert {
        item["key"]
        for item in current["progress"]
    } >= {"prover_loop", "prover_round_index", "theorem_applicability"}


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
    _mark_claim_clear(run_dir)
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
    loop = ProverLoopReport.model_validate(read_yaml(run_dir / "prover_loop.yaml"))
    assert loop.human_gate_status == "resolved"
    assert loop.loop_decision == "repair"
    assert loop.decision_reason
    assert read_yaml(run_dir / "idea_grade.yaml") == before_grade
    assert read_yaml(run_dir / "source_grounding.yaml") == before_grounding


def test_resolve_agent_gate_cannot_overwrite_resolved_gate(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    _mark_claim_clear(run_dir)
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
    first = runner.invoke(
        app,
        [
            "resolve-agent-gate",
            "--run",
            str(run_dir),
            "--decision",
            "revise",
            "--reason",
            "Record one deliberate human gate decision.",
        ],
    )
    assert first.exit_code == 0, first.output
    before_report = read_yaml(run_dir / "llm_agent_run.yaml")

    second = runner.invoke(
        app,
        [
            "resolve-agent-gate",
            "--run",
            str(run_dir),
            "--decision",
            "continue",
            "--reason",
            "This should not overwrite the first resolved gate.",
        ],
    )

    assert second.exit_code != 0
    assert second.exception is not None
    assert "already resolved" in str(second.exception)
    assert read_yaml(run_dir / "llm_agent_run.yaml") == before_report


def test_human_choice_routes_prover_loop_strategy_and_effort(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    runner = CliRunner()
    chosen = runner.invoke(
        app,
        [
            "choose-strategy",
            "--run",
            str(run_dir),
            "--strategy",
            "strategy_pin_reference",
            "--effort",
            "quick_test",
            "--decision",
            "continue",
            "--reason",
            "Pinning the reference is the mathematically cleanest cheap test.",
        ],
    )
    assert chosen.exit_code == 0, chosen.output
    before_grade = read_yaml(run_dir / "idea_grade.yaml")
    before_grounding = read_yaml(run_dir / "source_grounding.yaml")

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
    loop = ProverLoopReport.model_validate(read_yaml(run_dir / "prover_loop.yaml"))
    assert loop.selected_strategy_id == "strategy_pin_reference"
    assert loop.budget.approved_effort == "quick_test"
    assert {attempt.time_allocation for attempt in loop.attempts} == {"ask_human"}

    resolved = runner.invoke(
        app,
        [
            "resolve-agent-gate",
            "--run",
            str(run_dir),
            "--decision",
            "revise",
            "--reason",
            "Use the selected source-pinning route for the repair pass.",
        ],
    )
    assert resolved.exit_code == 0, resolved.output
    validate_run(run_dir)
    loop = ProverLoopReport.model_validate(read_yaml(run_dir / "prover_loop.yaml"))
    assert loop.selected_strategy_id == "strategy_pin_reference"
    selected_attempt = next(
        attempt for attempt in loop.attempts if attempt.strategy_id == "strategy_pin_reference"
    )
    assert selected_attempt.time_allocation == "repair"
    assert read_yaml(run_dir / "idea_grade.yaml") == before_grade
    assert read_yaml(run_dir / "source_grounding.yaml") == before_grounding


def test_prover_loop_pending_gate_cannot_continue(tmp_path: Path) -> None:
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
    loop = read_yaml(run_dir / "prover_loop.yaml")
    loop["loop_decision"] = "continue"
    write_yaml(run_dir / "prover_loop.yaml", loop)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "pending human gate" in str(exc)
    else:
        raise AssertionError("validate_run should reject continuing with a pending human gate")


def test_prover_loop_pending_gate_cannot_allocate_attempt_work(tmp_path: Path) -> None:
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
    loop = read_yaml(run_dir / "prover_loop.yaml")
    loop["attempts"][0]["time_allocation"] = "expand"
    loop["attempts"][0]["status"] = "candidate"
    write_yaml(run_dir / "prover_loop.yaml", loop)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "pending human gate" in str(exc)
    else:
        raise AssertionError("validate_run should reject attempt work with a pending gate")


def test_prover_loop_reviewer_pointers_must_match_llm_report(tmp_path: Path) -> None:
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
    loop = read_yaml(run_dir / "prover_loop.yaml")
    loop["attempts"][0]["reviewer_objections"][0]["verdict"] = "plausible_direction"
    write_yaml(run_dir / "prover_loop.yaml", loop)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "reviewer objection verdict" in str(exc)
    else:
        raise AssertionError("validate_run should reject tampered reviewer pointers")


def test_prover_loop_reviewer_pointers_must_be_unique(tmp_path: Path) -> None:
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
    loop = read_yaml(run_dir / "prover_loop.yaml")
    loop["attempts"][0]["reviewer_objections"][1]["verifier_id"] = loop["attempts"][0][
        "reviewer_objections"
    ][0]["verifier_id"]
    write_yaml(run_dir / "prover_loop.yaml", loop)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "reviewer_objections" in str(exc)
    else:
        raise AssertionError("validate_run should reject duplicate reviewer pointers")


def test_prover_loop_requires_round_index(tmp_path: Path) -> None:
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
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest["optional_artifacts"].pop("prover_round_index")
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "prover_loop requires prover_round_index" in str(exc)
    else:
        raise AssertionError("validate_run should require prover_round_index")


def test_prover_round_artifact_must_match_loop_attempt(tmp_path: Path) -> None:
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
    index = read_yaml(run_dir / "prover_rounds/index.yaml")
    first_path = run_dir / index["rounds"][0]["artifact_path"]
    round_report = read_yaml(first_path)
    round_report["strategy_id"] = "strategy_drifted"
    write_yaml(first_path, round_report)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "prover round strategy_id" in str(exc)
    else:
        raise AssertionError("validate_run should reject drifted prover round artifact")


def test_initial_prover_round_body_must_match_llm_agent_run(tmp_path: Path) -> None:
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
    index = read_yaml(run_dir / "prover_rounds/index.yaml")
    first_path = run_dir / index["rounds"][0]["artifact_path"]
    round_report = read_yaml(first_path)
    round_report["verifier_reviews"][0]["objections"] = ["Drifted reviewer body."]
    write_yaml(first_path, round_report)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "initial round verifier body" in str(exc)
    else:
        raise AssertionError("validate_run should reject drifted initial reviewer body")


def test_prover_round_rejects_proof_like_language(tmp_path: Path) -> None:
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
    index = read_yaml(run_dir / "prover_rounds/index.yaml")
    first_path = run_dir / index["rounds"][0]["artifact_path"]
    round_report = read_yaml(first_path)
    round_report["prover_attempt"]["output_summary"] = "This proof is complete and verified."
    write_yaml(first_path, round_report)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "forbidden proof language" in str(exc)
    else:
        raise AssertionError("validate_run should reject proof-like prover round language")


def test_prover_round_rejects_proof_like_language_variants(tmp_path: Path) -> None:
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
    index = read_yaml(run_dir / "prover_rounds/index.yaml")
    first_path = run_dir / index["rounds"][0]["artifact_path"]
    round_report = read_yaml(first_path)
    round_report["prover_attempt"]["output_summary"] = "The claim has been proven."
    round_report["prover_attempt"]["proposed_steps"] = ["The route proves the claim."]
    write_yaml(first_path, round_report)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "forbidden proof language" in str(exc)
    else:
        raise AssertionError("validate_run should reject proven/proves proof language")


def test_prover_round_input_artifacts_must_match_manifest(tmp_path: Path) -> None:
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
    index = read_yaml(run_dir / "prover_rounds/index.yaml")
    first_path = run_dir / index["rounds"][0]["artifact_path"]
    round_report = read_yaml(first_path)
    round_report["input_artifacts"] = ["locked_claim.yaml", "../outside.yaml", "missing.yaml"]
    write_yaml(first_path, round_report)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "prover round input_artifacts" in str(exc)
    else:
        raise AssertionError("validate_run should reject unknown or escaping round input artifacts")


def test_run_prover_loop_round_appends_child_attempt(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    runner = CliRunner()
    chosen = runner.invoke(
        app,
        [
            "choose-strategy",
            "--run",
            str(run_dir),
            "--strategy",
            "strategy_pin_reference",
            "--effort",
            "normal_pass",
            "--decision",
            "continue",
            "--reason",
            "Continue by repairing the source-pinning route.",
        ],
    )
    assert chosen.exit_code == 0, chosen.output
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
    resolved = runner.invoke(
        app,
        [
            "resolve-agent-gate",
            "--run",
            str(run_dir),
            "--decision",
            "revise",
            "--reason",
            "Use reviewer blockers to repair the selected source route.",
        ],
    )
    assert resolved.exit_code == 0, resolved.output
    before_loop = ProverLoopReport.model_validate(read_yaml(run_dir / "prover_loop.yaml"))
    assert before_loop.budget.rounds_used == 1

    appended = runner.invoke(
        app,
        [
            "run-prover-loop-round",
            "--run",
            str(run_dir),
            "--provider",
            "stub",
            "--reviewer-set",
            "espresso",
        ],
    )
    assert appended.exit_code == 0, appended.output
    validate_run(run_dir)
    loop = ProverLoopReport.model_validate(read_yaml(run_dir / "prover_loop.yaml"))
    assert loop.budget.rounds_used == 2
    assert len(loop.attempts) == len(before_loop.attempts) + 1
    child = loop.attempts[-1]
    assert child.round_index == 2
    assert child.parent_attempt_id in {attempt.attempt_id for attempt in before_loop.attempts}
    assert child.strategy_id == "strategy_pin_reference"
    assert child.prover_attempt_id == "llm_prover_002"
    assert len(set(child.reviewer_ids)) == 3
    index = ProverRoundIndex.model_validate(read_yaml(run_dir / "prover_rounds/index.yaml"))
    assert len(index.rounds) == len(loop.attempts)
    child_entry = next(entry for entry in index.rounds if entry.loop_attempt_id == child.attempt_id)
    child_round = ProverRoundReport.model_validate(read_yaml(run_dir / child_entry.artifact_path))
    assert child_round.source == "appended_prover_loop_round"
    assert child_round.loop_attempt_id == child.attempt_id
    assert child_round.parent_attempt_id == child.parent_attempt_id
    assert child_round.prover_attempt.agent_id == child.prover_attempt_id
    assert {review.verifier_id for review in child_round.verifier_reviews} == set(child.reviewer_ids)


def test_appended_prover_round_reviewers_must_match_loop_pointers(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    runner = CliRunner()
    chosen = runner.invoke(
        app,
        [
            "choose-strategy",
            "--run",
            str(run_dir),
            "--strategy",
            "strategy_pin_reference",
            "--effort",
            "normal_pass",
            "--decision",
            "continue",
            "--reason",
            "Continue by repairing the source-pinning route.",
        ],
    )
    assert chosen.exit_code == 0, chosen.output
    assert runner.invoke(app, ["run-llm-agents", "--run", str(run_dir), "--provider", "stub"]).exit_code == 0
    assert runner.invoke(
        app,
        ["resolve-agent-gate", "--run", str(run_dir), "--decision", "revise", "--reason", "repair"],
    ).exit_code == 0
    appended = runner.invoke(
        app,
        ["run-prover-loop-round", "--run", str(run_dir), "--provider", "stub"],
    )
    assert appended.exit_code == 0, appended.output
    index = read_yaml(run_dir / "prover_rounds/index.yaml")
    child_entry = next(entry for entry in index["rounds"] if entry["round_index"] == 2)
    child_path = run_dir / child_entry["artifact_path"]
    child_round = read_yaml(child_path)
    child_round["verifier_reviews"][0]["recommended_human_action"] = "continue"
    write_yaml(child_path, child_round)

    try:
        validate_run(run_dir)
    except KernelValidationError as exc:
        assert "prover round reviewer action" in str(exc)
    else:
        raise AssertionError("validate_run should reject appended reviewer pointer drift")


def test_prover_loop_round_prompt_contains_reviewer_blockers(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    log_path = tmp_path / "external_requests.jsonl"
    script = tmp_path / "recording_external_agent.py"
    script.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                f"log_path = {str(log_path)!r}",
                "request = json.loads(sys.stdin.read())",
                "with open(log_path, 'a', encoding='utf-8') as handle:",
                "    handle.write(json.dumps(request) + '\\n')",
                "json.dump({",
                "  'summary': 'External candidate route with recorded prompt.',",
                "  'steps': ['Repair the selected route against reviewer blockers.'],",
                "  'source_requests': ['Exact theorem locator.'],",
                "  'missing_hypotheses': ['Exact theorem applicability condition.'],",
                "  'risks': ['Reviewer blockers remain active.'],",
                "  'verdict': 'needs_sources',",
                "  'recommended_human_action': 'revise',",
                "}, sys.stdout)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    chosen = runner.invoke(
        app,
        [
            "choose-strategy",
            "--run",
            str(run_dir),
            "--strategy",
            "strategy_pin_reference",
            "--effort",
            "normal_pass",
            "--decision",
            "continue",
            "--reason",
            "Continue by repairing the source-pinning route.",
        ],
    )
    assert chosen.exit_code == 0, chosen.output
    initial = runner.invoke(
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
    assert initial.exit_code == 0, initial.output
    resolved = runner.invoke(
        app,
        [
            "resolve-agent-gate",
            "--run",
            str(run_dir),
            "--decision",
            "revise",
            "--reason",
            "Use reviewer blockers in the next prover prompt.",
        ],
    )
    assert resolved.exit_code == 0, resolved.output
    appended = runner.invoke(
        app,
        [
            "run-prover-loop-round",
            "--run",
            str(run_dir),
            "--provider",
            "external",
            "--model",
            "recording-agent",
            "--reviewer-set",
            "espresso",
        ],
        env={"ESPRESSO_EXTERNAL_AGENT_COMMAND": f"{sys.executable} {script}"},
    )
    assert appended.exit_code == 0, appended.output
    requests = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    prover_requests = [
        request for request in requests if '"role": "prover_loop_round"' in request["prompt"]
    ]
    assert prover_requests
    assert "reviewer_blockers_to_repair" in prover_requests[0]["prompt"]
    assert "strategy may prove only a narrower statement" in prover_requests[0]["prompt"]


def test_openai_provider_requires_api_key(tmp_path: Path, monkeypatch) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    _mark_claim_clear(run_dir)
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


def test_mock_agent_adapter_contains_unsafe_prover_prose() -> None:
    # Step 3 (deterministic mock agent artifact adapter): a prose-only,
    # proof-overclaiming agent answer must be CONTAINED -- never auto-upgraded
    # (status needs_human), proof-complete language withheld, and no structured
    # steps invented from prose.
    client = StubLLMClient(model="stub-espresso-agent")
    raw = {"summary": "This is obviously proved and verified. proof-close. The claim is true."}
    attempt = make_prover_attempt(raw, client=client, created_at="2026-06-03T00:00:00+00:00")

    assert attempt["status"] == "needs_human"  # adapter never grants a strong status
    assert attempt["output_summary"].count("[withheld proof-like phrase]") >= 3
    assert attempt["proposed_steps"] == []  # prose carried no structured steps
    LLMAgentAttempt.model_validate(attempt)  # the candidate artifact is schema-valid


def test_mock_agent_adapter_records_unsafe_verifier_language() -> None:
    # Unsafe agent language is recorded, not silently passed through as accepted.
    client = StubLLMClient(model="stub-espresso-agent")
    raw = {
        "summary": "proved and verified",
        "risks": ["the theorem is established"],
        "verdict": "plausible_direction",
    }
    review = make_verifier_review(
        raw, client=client, created_at="2026-06-03T00:00:00+00:00", index=1, reviewer_role="verifier"
    )
    for phrase in ("proved", "verified", "the theorem is established"):
        assert phrase in review["unsafe_language"]
    LLMAgentVerifierReview.model_validate(review)


def test_llm_agent_attempt_rejects_noncontract_status() -> None:
    # The candidate artifact contract forbids an out-of-enum status, so an agent
    # cannot smuggle an evidence/proof status (e.g. "checked") into the kernel.
    client = StubLLMClient(model="stub-espresso-agent")
    attempt = make_prover_attempt(
        {"summary": "x"}, client=client, created_at="2026-06-03T00:00:00+00:00"
    )
    attempt["status"] = "checked"
    try:
        LLMAgentAttempt.model_validate(attempt)
    except ValueError:
        pass
    else:
        raise AssertionError("candidate attempt must reject a non-contract status like 'checked'")


def test_safe_text_redaction_does_not_reintroduce_forbidden_word() -> None:
    # Regression: the redaction marker must NOT echo the forbidden phrase, or the
    # prover-round language guard would flag its own redaction (live agents tripped
    # this with words like "verified").
    from espresso_proof.validators import _first_forbidden_proof_language

    redacted = safe_text("This result is verified and the proof is complete.")
    assert "verified" not in redacted.lower()
    assert "[withheld proof-like phrase]" in redacted
    assert _first_forbidden_proof_language(redacted.lower()) is None


def test_forbidden_language_redaction_covers_validator() -> None:
    """Regression (Golden Dogfood Run): every proof-complete phrase the kernel's
    prover-round language guard detects must be redacted by safe_text. A live
    reviewer/prover sentence like "... is proven irrational" must be contained
    (redacted) rather than slip past safe_text and fail the run closed at the
    validator. Pins safe_text's FORBIDDEN_LANGUAGE as a superset of the validator
    forbidden set so the two layers cannot drift apart.
    """
    from espresso_proof.validators import _first_forbidden_proof_language

    # One natural sentence per validator-forbidden phrase (lowercased to match the
    # validator's scan of the joined artifact text).
    samples = [
        "the result is verified by the package",
        "with this the proof complete and final",
        "we proved the bound for every member",
        "this proved the theorem for all x",
        "a single cubic fourfold is proven irrational",
        "the obstruction proves the claim outright",
        "the argument proves the theorem in general",
        "the statement has been proven already",
        "this proof closes the remaining gap",
        "a complete proof is now available",
        "a full proof of irrationality follows",
        "the lemma establishes the theorem directly",
        "the witness establishes the claim for all x",
        "the proof establishes irrationality everywhere",
    ]
    for sentence in samples:
        # Precondition: the validator would flag the raw sentence.
        assert _first_forbidden_proof_language(sentence) is not None, sentence
        # After redaction the validator finds nothing forbidden.
        redacted = safe_text(sentence).lower()
        assert _first_forbidden_proof_language(redacted) is None, (sentence, redacted)
