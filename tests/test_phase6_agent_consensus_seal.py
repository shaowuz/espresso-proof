"""Phase 6 Slice B: adversarial-consensus runner + seal action.

The seal is the ONLY producer of proof_language_status=agent_consensus_mock_checked.
It runs the three reviewers adversarially; iff all three find no fatal flaw it seals
(amber/advisory, NOT machine-verified). It is transactional: an inadmissible result
(failed standard reviewer / refuted) rolls back and never half-writes a status.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.agents.llm_agents import StubLLMClient
from espresso_proof.cli import app
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.validators import KernelValidationError, validate_run
from espresso_proof.orchestration.agent_consensus_seal import seal_agent_consensus
from tests.test_proof_verification_bridge_v2 import _build


def _artifacts(run_dir: Path) -> dict:
    return read_yaml(run_dir / "run_manifest.yaml")["artifacts"]


def _grade(run_dir: Path) -> dict:
    return read_yaml(run_dir / _artifacts(run_dir)["idea_grade"])


def _sealable_run(tmp_path: Path) -> Path:
    """A run whose standard review passes cleanly (so the consensus is admissible)."""
    run_dir = _build(tmp_path)
    a = _artifacts(run_dir)
    rev = read_yaml(run_dir / a["review"])
    for r in rev["reviewers"]:
        r["verdict"] = "pass"
        r["objections"] = []
        r["required_reruns"] = []
    rev.update(
        attempt_status="passed", verdict="continue", strongest_objection="none",
        theorem_drift=[], missing_hypotheses=[], source_risks=[], example_risks=[],
        downgrade_reasons=[],
    )
    write_yaml(run_dir / a["review"], rev)
    g = read_yaml(run_dir / a["idea_grade"])
    g.update(
        status="promising", score=80, human_review_required=False,
        missing_hypotheses=[], source_gaps=[], counterexample_candidates=[], failed_tests=[],
    )
    write_yaml(run_dir / a["idea_grade"], g)
    validate_run(run_dir)  # baseline is valid before sealing
    return run_dir


class _ConsensusStub(StubLLMClient):
    """Adversarial-reviewer stub with per-role verdicts (default no_fatal_flaw_found)."""

    def __init__(self, verdicts: dict | None = None) -> None:
        super().__init__()
        self._verdicts = verdicts or {}

    def complete_json(self, *, system: str, prompt: str) -> dict:
        lo = system.lower()
        if "adversarial fatal-flaw review" not in lo:
            return super().complete_json(system=system, prompt=prompt)
        if "line-by-line" in lo:
            role = "line_proof_reviewer"
        elif "the evidence" in lo:
            role = "evidence_reviewer"
        else:
            role = "strategy_reviewer"
        return {
            "verdict": self._verdicts.get(role, "no_fatal_flaw_found"),
            "looked_for": "logical gaps / false steps / misapplied theorems",
            "finding": "stub finding",
            "residual_objections": ["pending exact locator (residual)"],
        }


def test_seal_unanimous_sets_amber_status(tmp_path: Path) -> None:
    run_dir = _sealable_run(tmp_path)
    record = seal_agent_consensus(run_dir, client=_ConsensusStub())
    validate_run(run_dir)
    assert record["consensus"] is True
    assert _grade(run_dir)["proof_language_status"] == "agent_consensus_mock_checked"
    rec = read_yaml(run_dir / "agent_consensus_review.yaml")
    assert rec["consensus"] is True
    assert "not machine-verified" in rec["disclaimer"].lower()
    # registered as an optional artifact
    assert "agent_consensus_review" in read_yaml(run_dir / "run_manifest.yaml")["optional_artifacts"]


def test_seal_fatal_flaw_does_not_seal(tmp_path: Path) -> None:
    run_dir = _sealable_run(tmp_path)
    record = seal_agent_consensus(run_dir, client=_ConsensusStub({"line_proof_reviewer": "fatal_flaw_found"}))
    validate_run(run_dir)
    assert record["consensus"] is False
    assert _grade(run_dir)["proof_language_status"] == "idea_sketch"  # status unchanged


def test_seal_unclear_does_not_seal(tmp_path: Path) -> None:
    run_dir = _sealable_run(tmp_path)
    record = seal_agent_consensus(run_dir, client=_ConsensusStub({"evidence_reviewer": "unclear"}))
    validate_run(run_dir)
    assert record["consensus"] is False
    assert _grade(run_dir)["proof_language_status"] == "idea_sketch"


def test_seal_unparseable_verdict_fails_safe_to_unclear(tmp_path: Path) -> None:
    run_dir = _sealable_run(tmp_path)
    record = seal_agent_consensus(run_dir, client=_ConsensusStub({"strategy_reviewer": "TOTALLY_BOGUS"}))
    assert record["consensus"] is False
    assert any(e["verdict"] == "unclear" for e in record["entries"])  # bogus -> unclear
    assert _grade(run_dir)["proof_language_status"] == "idea_sketch"


def test_seal_rolls_back_on_inadmissible_consensus(tmp_path: Path) -> None:
    # The baseline run has a FAILED standard reviewer; a unanimous adversarial result
    # is inadmissible (the kernel blocks it). The seal must roll back and re-raise,
    # leaving the run valid and the status unchanged.
    run_dir = _build(tmp_path)
    before = _grade(run_dir)["proof_language_status"]
    with pytest.raises(KernelValidationError):
        seal_agent_consensus(run_dir, client=_ConsensusStub())
    assert _grade(run_dir)["proof_language_status"] == before  # unchanged
    assert "agent_consensus_review" not in read_yaml(run_dir / "run_manifest.yaml").get("optional_artifacts", {})
    assert not (run_dir / "agent_consensus_review.yaml").exists()
    validate_run(run_dir)  # run is still valid (fully restored)


def test_seal_is_idempotent(tmp_path: Path) -> None:
    run_dir = _sealable_run(tmp_path)
    seal_agent_consensus(run_dir, client=_ConsensusStub())
    first = read_yaml(run_dir / "agent_consensus_review.yaml")
    seal_agent_consensus(run_dir, client=_ConsensusStub())
    second = read_yaml(run_dir / "agent_consensus_review.yaml")
    assert first == second  # generated_at preserved when nothing else changed


def test_seal_reviews_latest_prover_round_not_stale(tmp_path: Path) -> None:
    # When a prover_loop has iterated, the reviewers must see the LATEST round, not
    # the stale initial attempt (the consensus is hash-bound to prover_loop).
    from espresso_proof.orchestration.agent_consensus_seal import run_adversarial_reviewers

    run_dir = _build(tmp_path)
    rounds = run_dir / "prover_rounds"
    rounds.mkdir()
    write_yaml(rounds / "round_001.yaml", {"round_index": 1, "prover_attempt": {"output_summary": "ROUND1_STALE_PROOF", "proposed_steps": []}, "verifier_reviews": []})
    write_yaml(rounds / "round_002.yaml", {"round_index": 2, "prover_attempt": {"output_summary": "ROUND2_LATEST_PROOF", "proposed_steps": []}, "verifier_reviews": []})

    class _Spy(_ConsensusStub):
        def __init__(self) -> None:
            super().__init__()
            self.prompts: list[str] = []

        def complete_json(self, *, system: str, prompt: str) -> dict:
            self.prompts.append(prompt)
            return super().complete_json(system=system, prompt=prompt)

    spy = _Spy()
    run_adversarial_reviewers(run_dir, client=spy)
    blob = " ".join(spy.prompts)
    assert "ROUND2_LATEST_PROOF" in blob  # reviewers saw the latest round
    assert "ROUND1_STALE_PROOF" not in blob  # not the stale initial attempt


def test_seal_reseal_losing_consensus_downgrades(tmp_path: Path) -> None:
    # Honesty: a re-seal that no longer reaches consensus must not leave a stale amber
    # status -- it downgrades to proof_candidate.
    run_dir = _sealable_run(tmp_path)
    seal_agent_consensus(run_dir, client=_ConsensusStub())
    assert _grade(run_dir)["proof_language_status"] == "agent_consensus_mock_checked"
    record = seal_agent_consensus(run_dir, client=_ConsensusStub({"line_proof_reviewer": "fatal_flaw_found"}))
    validate_run(run_dir)
    assert record["consensus"] is False
    assert _grade(run_dir)["proof_language_status"] == "proof_candidate"  # downgraded
    assert read_yaml(run_dir / "agent_consensus_review.yaml")["consensus"] is False


def test_seal_consensus_cli_stub_seals(tmp_path: Path) -> None:
    run_dir = _sealable_run(tmp_path)
    result = CliRunner().invoke(app, ["seal-consensus", "--run", str(run_dir), "--provider", "stub"])
    assert result.exit_code == 0, result.output
    assert "Sealed: agent_consensus_mock_checked" in result.output
    assert _grade(run_dir)["proof_language_status"] == "agent_consensus_mock_checked"
    validate_run(run_dir)
