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
    registered_stages,
    run_front_half_agent_pass,
)
from espresso_proof.io import read_yaml
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


def test_front_half_pass_runs_registered_skips_rest_and_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)

    report = run_front_half_agent_pass(run_dir=run_dir, client=StubLLMClient())

    assert [s["stage"] for s in report["stages"]] == FRONT_HALF_STAGES
    by_stage = {s["stage"]: s["status"] for s in report["stages"]}
    registered = set(registered_stages())
    for stage, status in by_stage.items():
        if stage in registered:
            assert status == "ok", report
        else:
            assert status == "skipped", report
    validate_run(run_dir)


class _FakeClient:
    """A client returning a fixed payload, to probe normalizer safety."""

    provider = "fake"
    model = "fake-model"

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def complete_json(self, *, system: str, prompt: str) -> dict:
        return dict(self._payload)


def test_understanding_claim_strips_unsafe_model_fields(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    original = read_yaml(run_dir / artifacts["locked_claim"])

    # A hostile model: tries to self-confirm, claim source-attributed hypotheses,
    # use an out-of-enum status, and overwrite id/raw_text.
    malicious = _FakeClient(
        {
            "id": "attacker_claim",
            "raw_text": "TOTALLY DIFFERENT TEXT",
            "normalized_statement": "M23 is realizable as a Galois group over Q.",
            "objects": ["the Mathieu group M23", "the field Q"],
            "hypotheses": [
                {"name": "G = M23", "status": "explicit", "source": "source"},
                {"name": "base field is Q", "status": "totally-bogus", "source": "user"},
            ],
            "conclusion": "There exists a Galois extension of Q with group M23.",
            "ambiguous_terms": ["realizable"],
            "missing_or_unclear": ["explicit polynomial or construction"],
            "human_confirmed": True,
            "locked": False,
            "proof_status": "proved",
        }
    )

    run_front_half_agent_pass(
        run_dir=run_dir, client=malicious, stages=["understanding_claim"]
    )
    validate_run(run_dir)

    claim = read_yaml(run_dir / artifacts["locked_claim"])
    # id + raw_text preserved (downstream refs stable; user text not overwritten).
    assert claim["id"] == original["id"]
    assert claim["raw_text"] == original["raw_text"]
    # agent can never self-confirm; claim stays locked.
    assert claim["human_confirmed"] is False
    assert claim["locked"] is True
    # every hypothesis is agent-attributed; out-of-enum status coerced to unclear.
    assert claim["hypotheses"], claim
    for hyp in claim["hypotheses"]:
        assert hyp["source"] == "agent"
        assert hyp["status"] in {"explicit", "inferred", "missing", "unclear"}
    statuses = [h["status"] for h in claim["hypotheses"]]
    assert "unclear" in statuses  # the "totally-bogus" status landed here
    # no stray model fields leaked into the artifact.
    assert "proof_status" not in claim
    # the refined statement is surfaced and ambiguity recorded.
    assert "M23" in claim["normalized_statement"]
    assert claim["ambiguous_terms"]


def test_understanding_claim_with_stub_keeps_vague_claim_honest(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text("Prove the thing about the nice mirror object.", encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["understanding_claim"]
    )
    validate_run(run_dir)

    claim = read_yaml(run_dir / artifacts["locked_claim"])
    assert claim["human_confirmed"] is False
    assert claim["locked"] is True


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
