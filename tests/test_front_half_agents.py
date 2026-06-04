"""S0 tests: generic scaffolder + front-half live-agent plumbing.

These cover the harness only (no stage normalizers yet): an arbitrary claim
scaffolds into a kernel-valid run, the all-skipped front-half pass leaves a run
valid, and ``run-flow --front-half stub`` stays green.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.front_half_agents import (
    FRONT_HALF_STAGES,
    run_front_half_agent_pass,
)
from espresso_proof.llm_agents import StubLLMClient
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.validators import validate_run

ARBITRARY_CLAIM = (
    "The Mathieu group M23 is realizable as a Galois group over Q.\n\n"
    "Please attempt to prove this."
)


def test_new_run_scaffolds_valid_run_for_arbitrary_claim(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "new-run",
            "--input",
            str(claim_path),
            "--project",
            "agent_runtime_demo",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    run_dir = Path(result.output.strip().split("Wrote run artifacts to ")[-1])
    assert run_dir.exists()
    # Scaffolder fell back to the generic "unclear" builder and is kernel-valid.
    validate_run(run_dir)


def test_front_half_pass_all_skipped_keeps_run_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)

    report = run_front_half_agent_pass(run_dir=run_dir, client=StubLLMClient())

    # No normalizers registered yet -> every stage skipped, run still valid.
    assert [s["stage"] for s in report["stages"]] == FRONT_HALF_STAGES
    assert all(s["status"] == "skipped" for s in report["stages"])
    validate_run(run_dir)


def test_run_flow_front_half_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "run-flow",
            "--input",
            str(claim_path),
            "--project",
            "agent_runtime_demo",
            "--provider",
            "stub",
            "--front-half",
            "stub",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Front-half agents: mode=stub" in result.output
    run_dir = Path(result.output.splitlines()[0].split("Wrote run artifacts to ")[-1])
    validate_run(run_dir)


def test_run_flow_front_half_rejects_bad_mode(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "run-flow",
            "--input",
            str(claim_path),
            "--front-half",
            "bogus",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code != 0
