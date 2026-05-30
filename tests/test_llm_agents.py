from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.io import read_yaml
from espresso_proof.llm_agents import collect_unsafe_language, safe_text
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.models import LLMAgentRunReport
from espresso_proof.validators import validate_run


FIXTURES = Path("tests/fixtures")


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
    assert report.human_gate.status == "pending"
    assert report.human_gate.human_confirmed is False


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


def test_safe_language_filter_preserves_unverified() -> None:
    text = safe_text("This remains unverified; do not say proof-close.")

    assert "unverified" in text
    assert "proof-close" not in text
    assert "proof-close" in collect_unsafe_language({"summary": "proof-close", "steps": [], "risks": []})
    assert "verified" not in collect_unsafe_language(
        {"summary": "This remains unverified.", "steps": [], "risks": []}
    )
