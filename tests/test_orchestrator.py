"""S9 tests: the 00 Orchestrator driver (resumable, gate-stopping, honest)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.io import read_yaml
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.orchestrator import orchestrate_run, resume_run
from espresso_proof.validators import KernelValidationError, validate_run

# Three fresh, arbitrary claims: an open problem, a clearly-true textbook claim,
# and a clearly-false claim. None match a mock scenario, so each scaffolds to the
# honest "unclear/needs_human" skeleton and must run end to end.
CLAIMS = {
    "m23": "The Mathieu group M23 is realizable as a Galois group over Q.",
    "true": "Every prime greater than two is odd.",
    "false": "Every continuous function on the reals is differentiable everywhere.",
}

_PROOF_COMPLETE = ("proof-close", "proof complete", "we proved", "this establishes the theorem")


def _orchestrate(tmp_path: Path, claim_text: str, name: str) -> Path:
    claim_path = tmp_path / f"{name}.md"
    claim_path.write_text(claim_text, encoding="utf-8")
    result = orchestrate_run(
        project="agent_runtime_demo",
        input_path=claim_path,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
    )
    return result["run_dir"]


@pytest.mark.parametrize("name", list(CLAIMS))
def test_orchestrate_reaches_gate_for_arbitrary_claim(tmp_path: Path, name: str) -> None:
    claim_path = tmp_path / f"{name}.md"
    claim_path.write_text(CLAIMS[name], encoding="utf-8")
    result = orchestrate_run(
        project="agent_runtime_demo",
        input_path=claim_path,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
    )
    run_dir = result["run_dir"]
    assert result["state"] == "WAITING_LLM_AGENT_HUMAN_GATE"
    assert "status" in result["human_gate"]
    # the whole durable run is kernel-valid.
    validate_run(run_dir)


def test_orchestrate_resume_to_memo_is_honest(tmp_path: Path) -> None:
    run_dir = _orchestrate(tmp_path, CLAIMS["m23"], "m23")
    memo = resume_run(
        run_dir=run_dir, selected_strategy="strategy_lock_claim", effort="quick_test"
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["current_state"] == "COMPLETED_MEMO"
    validate_run(run_dir)
    lowered = memo.lower()
    # an open problem must never be described with proof-complete language.
    for banned in _PROOF_COMPLETE:
        assert banned not in lowered
    # the locked claim's own human_confirmed stays False (gate ≠ claim confirmation).
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    assert claim["human_confirmed"] is False


def test_orchestrate_resume_rejects_unknown_strategy(tmp_path: Path) -> None:
    run_dir = _orchestrate(tmp_path, CLAIMS["m23"], "m23")
    # a human choice for a strategy not in the brief must be blocked by the kernel.
    with pytest.raises(KernelValidationError):
        resume_run(
            run_dir=run_dir, selected_strategy="totally_made_up_route", effort="quick_test"
        )


def test_orchestrate_resume_before_gate_is_blocked(tmp_path: Path) -> None:
    # A scaffold-only run (no agent pass) is not at the human gate.
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(CLAIMS["m23"], encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    with pytest.raises(ValueError):
        resume_run(run_dir=run_dir, selected_strategy="none")


def test_orchestrate_is_resumable_and_idempotent(tmp_path: Path) -> None:
    claim_path = tmp_path / "m23.md"
    claim_path.write_text(CLAIMS["m23"], encoding="utf-8")
    first = orchestrate_run(
        project="agent_runtime_demo",
        input_path=claim_path,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
    )
    # Re-driving the SAME run skips completed stages and stays at the gate.
    second = orchestrate_run(
        project="agent_runtime_demo",
        input_path=claim_path,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        run_dir=first["run_dir"],
    )
    assert second["run_dir"] == first["run_dir"]
    assert second["state"] == "WAITING_LLM_AGENT_HUMAN_GATE"
    validate_run(first["run_dir"])


def test_orchestrate_blocks_on_invalid_artifact_and_does_not_advance(tmp_path: Path) -> None:
    # Fault injection: corrupt a front-half artifact after scaffolding, then drive
    # the orchestrator. It must fail closed (raise) and NOT advance the lifecycle.
    from espresso_proof.io import write_yaml

    claim_path = tmp_path / "m23.md"
    claim_path.write_text(CLAIMS["m23"], encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    # mark it scaffolded so the orchestrator resumes from an existing run.
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest["current_state"] = "SCAFFOLDED"
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    # corrupt the locked claim (unlock it -> Claim.validate_locked fails).
    locked = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    locked["locked"] = False
    write_yaml(run_dir / manifest["artifacts"]["locked_claim"], locked)

    with pytest.raises(Exception):  # noqa: B017 - any validation/parse error is fine
        orchestrate_run(
            project="agent_runtime_demo",
            input_path=claim_path,
            output_root=tmp_path,
            provider="stub",
            front_half="mock",  # mock front half so the corrupt artifact reaches validate_run
            run_dir=run_dir,
        )
    # lifecycle did not advance past SCAFFOLDED.
    after = read_yaml(run_dir / "run_manifest.yaml")
    assert after["current_state"] == "SCAFFOLDED"


def test_orchestrate_cli_fresh_then_resume(tmp_path: Path) -> None:
    claim_path = tmp_path / "m23.md"
    claim_path.write_text(CLAIMS["m23"], encoding="utf-8")
    runner = CliRunner()
    fresh = runner.invoke(
        app,
        [
            "orchestrate",
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
    assert fresh.exit_code == 0, fresh.output
    assert "WAITING_LLM_AGENT_HUMAN_GATE" in fresh.output
    run_dir = Path(fresh.output.splitlines()[0].split("Wrote run artifacts to ")[-1])

    resumed = runner.invoke(
        app,
        [
            "orchestrate",
            "--resume",
            str(run_dir),
            "--select-strategy",
            "strategy_lock_claim",
            "--effort",
            "quick_test",
        ],
    )
    assert resumed.exit_code == 0, resumed.output
    assert "Claim [locked]" in resumed.output
