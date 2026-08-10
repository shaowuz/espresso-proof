"""Phase 01 — single runtime entry parity.

``run-flow`` is a compatibility wrapper around the same ``orchestrate_run``
lifecycle as ``orchestrate``. These tests pin that the two entrypoints cannot
drift: on the same stub input they reach the same lifecycle state and write the
same required + optional artifact set, both pass ``validate_run``, and neither
upgrades proof/source/evidence status or manufactures human_confirmed.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.cli import app, _resolve_front_half_mode
from espresso_proof.kernel.io import read_yaml
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")

# An arbitrary claim that matches no mock scenario, so it scaffolds to the generic
# unclear/needs_human run and stops consistently at the claim-clear gate.
CLAIM_TEXT = "No odd perfect number exists.\n"

# A precise seeded scenario used when a test needs to reach downstream stages.
CLEAR_CLAIM_TEXT = "Every group of order p^2, where p is prime, is abelian.\n"


def test_auto_front_half_uses_real_claim_agent_for_real_providers() -> None:
    assert (
        _resolve_front_half_mode(
            provider="external", front_half="auto", command_name="test"
        )
        == "external"
    )
    assert (
        _resolve_front_half_mode(provider="openai", front_half="auto", command_name="test")
        == "openai"
    )
    assert (
        _resolve_front_half_mode(provider="stub", front_half="auto", command_name="orchestrate")
        == "stub"
    )
    assert (
        _resolve_front_half_mode(provider="stub", front_half="auto", command_name="run-flow")
        == "mock"
    )


def test_claim_understanding_skill_pins_generation_contract() -> None:
    text = Path("espresso_proof/agent_skills/claim_understanding.md").read_text(
        encoding="utf-8"
    )

    for phrase in (
        "Required Output Shape",
        "normalized_statement",
        "Separate objects from hypotheses",
        "Separate the conclusion from the route",
        "Use `missing_or_unclear` for blockers",
        "Never mark the claim human-confirmed",
    ):
        assert phrase in text


def _claim(tmp_path: Path) -> str:
    path = tmp_path / "claim.md"
    path.write_text(CLAIM_TEXT, encoding="utf-8")
    return str(path)


def _clear_claim(tmp_path: Path) -> str:
    path = tmp_path / "clear_claim.md"
    path.write_text(CLEAR_CLAIM_TEXT, encoding="utf-8")
    return str(path)


def _run(args: list[str]) -> Path:
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    first = result.output.splitlines()[0]
    assert first.startswith("Wrote run artifacts to "), result.output
    return Path(first.split("Wrote run artifacts to ", 1)[1].strip())


def _artifact_shape(run_dir: Path) -> tuple[str, frozenset[str], frozenset[str]]:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    return (
        manifest["current_state"],
        frozenset(manifest.get("artifacts", {})),
        frozenset(manifest.get("optional_artifacts", {})),
    )


def test_orchestrate_and_run_flow_reach_same_state_and_artifacts(tmp_path: Path) -> None:
    claim = _claim(tmp_path)
    orch = _run(
        [
            "orchestrate",
            "--input", claim,
            "--project", "parity",
            "--provider", "stub",
            "--front-half", "stub",
            "--output-root", str(tmp_path / "orch"),
        ]
    )
    flow = _run(
        [
            "run-flow",
            "--input", claim,
            "--project", "parity",
            "--provider", "stub",
            "--front-half", "stub",
            "--output-root", str(tmp_path / "flow"),
        ]
    )

    validate_run(orch)
    validate_run(flow)

    orch_state, orch_req, orch_opt = _artifact_shape(orch)
    flow_state, flow_req, flow_opt = _artifact_shape(flow)

    assert orch_state == flow_state == "WAITING_CLAIM_CLARIFICATION"
    assert orch_req == flow_req
    assert orch_opt == flow_opt


def test_run_flow_does_not_manufacture_human_confirmation(tmp_path: Path) -> None:
    flow = _run(
        [
            "run-flow",
            "--input", _claim(tmp_path),
            "--project", "parity",
            "--provider", "stub",
            "--front-half", "stub",
            "--output-root", str(tmp_path),
        ]
    )
    validate_run(flow)
    manifest = read_yaml(flow / "run_manifest.yaml")
    claim = read_yaml(flow / manifest["artifacts"]["locked_claim"])
    # the wrapper stops for claim clarification without manufacturing user
    # confirmation.
    assert claim.get("human_confirmed") is False
    human_choice = read_yaml(flow / manifest["artifacts"]["human_choice"])
    assert human_choice.get("status") == "pending"


def test_run_flow_fails_closed_on_invalid_artifact(tmp_path: Path, monkeypatch) -> None:
    # If a stage produces an artifact that fails validation, the wrapper (like
    # orchestrate) must raise rather than advance past the gate.
    import espresso_proof.orchestration.orchestrator as orch

    def boom(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise AssertionError("literature stage exploded")

    monkeypatch.setattr(orch, "run_literature_discovery", boom)
    result = CliRunner().invoke(
        app,
        [
            "run-flow",
            "--input", _clear_claim(tmp_path),
            "--project", "parity",
            "--provider", "stub",
            "--front-half", "mock",
            "--output-root", str(tmp_path),
        ],
    )
    assert result.exit_code != 0
