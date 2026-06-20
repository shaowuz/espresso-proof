"""Goal 69: Line-Numbered Proof Writer V1 — the sealable proof artifact.

The agent-consensus mock check may ONLY seal a structured line_numbered_proof.yaml
(each line has a statement + justification + dependencies), reviewed line-by-line by
the three proof reviewers who must cite line_id. A route / proposed_steps /
advisory_proof_sketch is NOT sealable. Stays advisory (never machine-verified /
source-backed / checked / proved).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.agents.llm_agents import StubLLMClient
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import (
    AgentConsensusReview,
    LineNumberedProof,
    ProofLine,
)
from espresso_proof.kernel.validators import (
    KernelValidationError,
    _first_forbidden_proof_language,
    validate_run,
)
from espresso_proof.orchestration.agent_consensus_seal import (
    NoLineNumberedProofError,
    seal_agent_consensus,
)
from espresso_proof.orchestration.proof_writer import write_line_numbered_proof
from espresso_proof.ui.descriptions import VERIFIED_PROOF_STATUSES, describe_proof_language
from espresso_proof.ui.evidence_trust import EARNED_LEVELS, is_green, trust_tone
from tests.test_proof_verification_bridge_v2 import _build

_AMBER = "agent_consensus_mock_checked"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _artifacts(run_dir: Path) -> dict:
    return read_yaml(run_dir / "run_manifest.yaml")["artifacts"]


def _grade(run_dir: Path) -> dict:
    return read_yaml(run_dir / _artifacts(run_dir)["idea_grade"])


def _admissible_run(tmp_path: Path) -> Path:
    """A claim-cleared run whose kernel review passes cleanly (so a consensus over a
    line-numbered proof is admissible)."""
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
    validate_run(run_dir)
    return run_dir


def _run_with_proof(tmp_path: Path) -> Path:
    run_dir = _admissible_run(tmp_path)
    write_line_numbered_proof(run_dir, client=StubLLMClient())  # stub parity proof
    return run_dir


class _ProofReviewStub(StubLLMClient):
    """Proof-consensus reviewer stub with per-role verdicts; cites the proof's line_ids
    (parsed from the prompt) unless ``cite=False``."""

    def __init__(self, verdicts: dict | None = None, cite: bool = True) -> None:
        super().__init__()
        self._verdicts = verdicts or {}
        self._cite = cite

    def complete_json(self, *, system: str, prompt: str) -> dict:
        lo = system.lower()
        if "proof-consensus review" not in lo:
            return super().complete_json(system=system, prompt=prompt)
        if "line-proof reviewer" in lo:
            role = "line_proof_reviewer"
        elif "applicability reviewer" in lo:
            role = "theorem_lemma_applicability_reviewer"
        else:
            role = "adversarial_gap_reviewer"
        import re

        ids = re.findall(r"['\"]?line_id['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]", prompt)
        return {
            "verdict": self._verdicts.get(role, "no_fatal_flaw_found"),
            "looked_for": "non-sequiturs, misapplied lemmas, missing cases",
            "finding": "stub proof review",
            "cited_line_ids": (sorted(set(ids)) if self._cite else []),
            "residual_objections": [],
        }


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #
def test_proof_line_and_proof_models() -> None:
    proof = LineNumberedProof(
        claim_id="c1", strategy_id="s1", proof_kind="parity",
        lines=[
            ProofLine(line_id="L1", statement="n^2-n=n(n-1)", justification="distribute"),
            ProofLine(line_id="L2", statement="2 | n(n-1)", justification="one factor even", depends_on=["L1"]),
        ],
    )
    assert proof.status == "advisory"  # pinned advisory


def test_proof_rejects_route_only_and_bad_lines() -> None:
    with pytest.raises(ValidationError):  # no lines == a route, not a proof
        LineNumberedProof(claim_id="c1", strategy_id="s1", proof_kind="x", lines=[])
    with pytest.raises(ValidationError):  # line without justification == a route step
        LineNumberedProof(claim_id="c1", strategy_id="s1", proof_kind="x",
                          lines=[ProofLine(line_id="L1", statement="s", justification="")])
    with pytest.raises(ValidationError):  # forward/unknown dependency
        LineNumberedProof(claim_id="c1", strategy_id="s1", proof_kind="x",
                          lines=[ProofLine(line_id="L1", statement="s", justification="j", depends_on=["L9"])])


def test_proof_has_no_status_field() -> None:
    with pytest.raises(ValidationError):  # StrictBaseModel forbids smuggling a status
        LineNumberedProof(
            claim_id="c1", strategy_id="s1", proof_kind="x",
            lines=[ProofLine(line_id="L1", statement="s", justification="j")],
            proof_language_status="lean_checked",
        )


# --------------------------------------------------------------------------- #
# the writer
# --------------------------------------------------------------------------- #
def test_writer_produces_structured_proof(tmp_path: Path) -> None:
    run_dir = _admissible_run(tmp_path)
    record = write_line_numbered_proof(run_dir, client=StubLLMClient())
    validate_run(run_dir)
    assert record["status"] == "advisory"
    assert len(record["lines"]) >= 3
    for line in record["lines"]:  # every line is a real proof line, not a route step
        assert line["statement"].strip() and line["justification"].strip()
    assert "line_numbered_proof" in read_yaml(run_dir / "run_manifest.yaml")["optional_artifacts"]


# --------------------------------------------------------------------------- #
# the 6 required acceptance cases
# --------------------------------------------------------------------------- #
def test_positive_parity_proof_seals(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    record = seal_agent_consensus(run_dir, client=_ProofReviewStub())
    validate_run(run_dir)
    assert record["consensus"] is True
    assert {e["reviewer_id"] for e in record["entries"]} == {
        "line_proof_reviewer", "theorem_lemma_applicability_reviewer", "adversarial_gap_reviewer"
    }
    assert all(e["cited_line_ids"] for e in record["entries"])  # every PASS cites lines
    assert _grade(run_dir)["proof_language_status"] == _AMBER


def test_negative_route_only_cannot_seal(tmp_path: Path) -> None:
    run_dir = _admissible_run(tmp_path)  # NO line_numbered_proof written (route-only)
    with pytest.raises(NoLineNumberedProofError):
        seal_agent_consensus(run_dir, client=_ProofReviewStub())
    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_negative_line_without_justification_cannot_seal(tmp_path: Path) -> None:
    run_dir = _admissible_run(tmp_path)
    a = _artifacts(run_dir)
    claim_id = read_yaml(run_dir / a["locked_claim"])["id"]
    # write a route-shaped proof (a line with NO justification), bypassing the model
    write_yaml(run_dir / "line_numbered_proof.yaml", {
        "schema_version": 1, "claim_id": claim_id, "strategy_id": "s1", "proof_kind": "x",
        "status": "advisory", "generated_at": "t",
        "lines": [{"line_id": "L1", "statement": "n^2-n is even", "justification": "", "depends_on": []}],
    })
    man = read_yaml(run_dir / "run_manifest.yaml")
    man["optional_artifacts"]["line_numbered_proof"] = "line_numbered_proof.yaml"
    write_yaml(run_dir / "run_manifest.yaml", man)
    with pytest.raises(KernelValidationError):  # malformed proof -> run invalid -> not sealable
        validate_run(run_dir)


def test_negative_reviewer_pass_without_line_id_cannot_seal(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    with pytest.raises(KernelValidationError):  # PASS citing no line -> gate fails -> rollback + raise
        seal_agent_consensus(run_dir, client=_ProofReviewStub(cite=False))
    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_negative_modified_proof_after_review_invalidates_consensus(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    seal_agent_consensus(run_dir, client=_ProofReviewStub())
    assert _grade(run_dir)["proof_language_status"] == _AMBER
    validate_run(run_dir)
    # tamper with the proof AFTER it was reviewed+sealed
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    proof["lines"].append({"line_id": "LX", "statement": "sneaky added line", "justification": "none", "depends_on": []})
    write_yaml(run_dir / "line_numbered_proof.yaml", proof)
    with pytest.raises(KernelValidationError):  # bound_line_numbered_proof_hash mismatch
        validate_run(run_dir)


def test_negative_consensus_cannot_upgrade_to_machine_verified(tmp_path: Path) -> None:
    # the sealed amber status is never a machine-verified / checked / proved status
    run_dir = _run_with_proof(tmp_path)
    seal_agent_consensus(run_dir, client=_ProofReviewStub())
    validate_run(run_dir)
    assert _grade(run_dir)["proof_language_status"] == _AMBER
    assert _AMBER not in VERIFIED_PROOF_STATUSES
    assert _AMBER not in {"source_checked_argument", "computation_checked", "lean_checked", "human_confirmed"}
    assert trust_tone("agent_consensus") == "candidate" and "agent_consensus" not in EARNED_LEVELS
    assert is_green("agent_consensus") is False
    assert "verified" not in describe_proof_language(_AMBER).lower()
    # the proof's source_refs are candidate references, never earned source_backed
    proof = read_yaml(run_dir / "line_numbered_proof.yaml")
    assert proof["status"] == "advisory"
    # score 100 is impossible with this amber status
    g = _grade(run_dir)
    g["score"] = 100
    write_yaml(run_dir / _artifacts(run_dir)["idea_grade"], g)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


# --------------------------------------------------------------------------- #
# carried-over guards (still enforced under the new contract)
# --------------------------------------------------------------------------- #
def test_fatal_flaw_does_not_seal(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    record = seal_agent_consensus(run_dir, client=_ProofReviewStub({"adversarial_gap_reviewer": "fatal_flaw_found"}))
    validate_run(run_dir)
    assert record["consensus"] is False
    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_unclear_does_not_seal(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    record = seal_agent_consensus(run_dir, client=_ProofReviewStub({"line_proof_reviewer": "unclear"}))
    validate_run(run_dir)
    assert record["consensus"] is False


def test_reseal_losing_consensus_downgrades(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    seal_agent_consensus(run_dir, client=_ProofReviewStub())
    assert _grade(run_dir)["proof_language_status"] == _AMBER
    seal_agent_consensus(run_dir, client=_ProofReviewStub({"line_proof_reviewer": "fatal_flaw_found"}))
    validate_run(run_dir)
    assert _grade(run_dir)["proof_language_status"] == "proof_candidate"  # honest downgrade


def test_model_requires_three_proof_reviewers() -> None:
    base = dict(claim_id="c1", run_id="r1", generated_at="t", bound_claim_hash="h",
                consensus=False, disclaimer="d")
    with pytest.raises(ValidationError):  # wrong / old reviewer set
        AgentConsensusReview(**base, entries=[
            {"reviewer_id": "strategy_reviewer", "verdict": "unclear", "looked_for": "", "finding": ""},
        ])


def test_unparseable_reviewer_verdict_fails_safe(tmp_path: Path) -> None:
    # a malformed reviewer verdict normalizes to 'unclear' (blocks) -> not sealed
    run_dir = _run_with_proof(tmp_path)
    record = seal_agent_consensus(run_dir, client=_ProofReviewStub({"adversarial_gap_reviewer": "TOTALLY_BOGUS"}))
    assert record["consensus"] is False
    assert any(e["verdict"] == "unclear" for e in record["entries"])
    assert _grade(run_dir)["proof_language_status"] != _AMBER


def test_forged_consensus_without_proof_rejected_by_validator(tmp_path: Path) -> None:
    # The validator gate is the backstop: even a hand-forged unanimous consensus with
    # the grade set to amber is rejected when there is NO line_numbered_proof (bypasses
    # the seal helper entirely). Exercises validators._validate_agent_consensus_review.
    from espresso_proof.kernel.pressure_status import artifact_revision_hash

    run_dir = _admissible_run(tmp_path)  # NO proof written
    a = _artifacts(run_dir)
    man = read_yaml(run_dir / "run_manifest.yaml")
    forged = {
        "schema_version": 1,
        "claim_id": read_yaml(run_dir / a["locked_claim"])["id"],
        "run_id": man["run_id"],
        "generated_at": "t",
        "bound_claim_hash": artifact_revision_hash(run_dir, a["locked_claim"]),
        "bound_line_numbered_proof_hash": "",
        "bound_source_grounding_hash": artifact_revision_hash(run_dir, a["source_grounding"]),
        "bound_review_hash": artifact_revision_hash(run_dir, a["review"]),
        "bound_prover_loop_hash": "",
        "entries": [
            {"reviewer_id": rid, "verdict": "no_fatal_flaw_found", "looked_for": "x",
             "finding": "f", "cited_line_ids": ["L1"], "residual_objections": []}
            for rid in ("line_proof_reviewer", "theorem_lemma_applicability_reviewer", "adversarial_gap_reviewer")
        ],
        "consensus": True,
        "disclaimer": "forged",
    }
    write_yaml(run_dir / "agent_consensus_review.yaml", forged)
    g = read_yaml(run_dir / a["idea_grade"])
    g["proof_language_status"] = _AMBER
    write_yaml(run_dir / a["idea_grade"], g)
    man.setdefault("optional_artifacts", {})["agent_consensus_review"] = "agent_consensus_review.yaml"
    write_yaml(run_dir / "run_manifest.yaml", man)
    with pytest.raises(KernelValidationError):  # no line_numbered_proof -> backstop blocks
        validate_run(run_dir)


def test_refuted_claim_cannot_carry_mock_check(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    seal_agent_consensus(run_dir, client=_ProofReviewStub())
    g = _grade(run_dir)
    g["status"] = "refuted"
    write_yaml(run_dir / _artifacts(run_dir)["idea_grade"], g)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_mock_check_phrase_is_a_smuggling_field() -> None:
    from espresso_proof.kernel.validators import _prover_status_smuggling_field

    assert _prover_status_smuggling_field("the agent_consensus_mock_checked: true now") is not None
    assert _prover_status_smuggling_field("mock_checked = true") is not None


def test_sealed_memo_is_scan_clean_and_discloses(tmp_path: Path) -> None:
    run_dir = _run_with_proof(tmp_path)
    seal_agent_consensus(run_dir, client=_ProofReviewStub())
    validate_run(run_dir)
    fr = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["final_report"]
    memo = (run_dir / fr).read_text()
    assert "Agent-consensus mock check" in memo and "not a machine check" in memo
    assert _first_forbidden_proof_language(memo.lower()) is None
