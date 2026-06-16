"""Goal 02: LLM Literature Scout tests.

The scout proposes CANDIDATE sources tied to proof steps and can never grant
trusted source status. Positive: it runs in stub mode and ties candidates to
known steps. Negative: a model-supplied trusted status is downgraded/rejected,
missing metadata keeps a candidate candidate, a candidate for an unknown step is
dropped, the scout never upgrades source-grounding status, and a hand-edited
source_backed status fails validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.claim_clear import ClaimClearBlockedError
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.literature_scout import run_literature_scout
from espresso_proof.llm_agents import StubLLMClient
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.validators import KernelValidationError, validate_run

# Claim text that triggers the hodge_atoms mock scenario, whose scaffold seeds a
# source_grounding with one proof step (step_001) — a real step to scout against.
# (Phrased WITHOUT "irrational": since Goal 42E cycle 5, universal
# irrationality claims route to the source-misapplication scenario instead.)
HODGE_CLAIM = (
    "For all cubic fourfolds, the Hodge atom obstruction controls the rationality "
    "question. The very general case is treated as all smooth cubic fourfolds."
)


def _run_with_step(tmp_path: Path) -> Path:
    run_dir = _unclear_run_with_step(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    return run_dir


def _unclear_run_with_step(tmp_path: Path) -> Path:
    claim = tmp_path / "claim.md"
    claim.write_text(HODGE_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("hodge_atoms", claim, tmp_path)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    assert sg.get("steps"), "expected the hodge mock to seed a proof step"
    return run_dir


class _FakeClient:
    provider = "fake"
    model = "fake-model"

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def complete_json(self, *, system: str, prompt: str) -> dict:
        return dict(self._payload)


def _candidate_payload(step_id: str, **overrides) -> dict:
    candidate = {
        "title": "Derived categories of cubic fourfolds",
        "authors": ["A. Kuznetsov"],
        "source_type": "paper",
        "locator_hint": "Section 4",
        "statement_hint": "structure of the Kuznetsov component",
        "url_or_identifier": "arXiv:0808.3351",
        "why_relevant": "may support the obstruction step",
    }
    candidate.update(overrides)
    return {"proof_step_candidates": [{"proof_step_id": step_id, "candidates": [candidate]}]}


def test_scout_stub_runs_and_writes_valid_candidate_artifact(tmp_path: Path) -> None:
    run_dir = _run_with_step(tmp_path)
    report = run_literature_scout(run_dir=run_dir, client=StubLLMClient())

    # Durable, kernel-valid artifact registered in the manifest.
    assert (run_dir / "literature_candidates.yaml").exists()
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["literature_candidates"] == "literature_candidates.yaml"
    validate_run(run_dir)
    # Every candidate (if any) is candidate-only.
    for step in report["proof_step_candidates"]:
        for cand in step["candidates"]:
            assert cand["status"] == "candidate"


def test_scout_blocks_unclear_claim_before_model_call(tmp_path: Path) -> None:
    run_dir = _unclear_run_with_step(tmp_path)
    called = False

    class _TrackingClient(_FakeClient):
        def complete_json(self, *, system: str, prompt: str) -> dict:
            nonlocal called
            called = True
            return super().complete_json(system=system, prompt=prompt)

    with pytest.raises(ClaimClearBlockedError, match="claim-clear gate"):
        run_literature_scout(
            run_dir=run_dir,
            client=_TrackingClient(_candidate_payload("step_001")),
        )

    assert called is False
    assert not (run_dir / "literature_candidates.yaml").exists()
    validate_run(run_dir)


def test_scout_ties_candidates_to_known_step(tmp_path: Path) -> None:
    run_dir = _run_with_step(tmp_path)
    report = run_literature_scout(
        run_dir=run_dir, client=_FakeClient(_candidate_payload("step_001"))
    )
    step = next(s for s in report["proof_step_candidates"] if s["proof_step_id"] == "step_001")
    assert len(step["candidates"]) == 1
    cand = step["candidates"][0]
    assert cand["title"] == "Derived categories of cubic fourfolds"
    assert cand["status"] == "candidate"
    # exact locator is never supplied by the scout.
    assert "exact_locator" in cand["missing_metadata"]
    validate_run(run_dir)


def test_scout_downgrades_model_supplied_source_backed(tmp_path: Path) -> None:
    run_dir = _run_with_step(tmp_path)
    # The model tries to self-certify the candidate as source_backed.
    report = run_literature_scout(
        run_dir=run_dir,
        client=_FakeClient(_candidate_payload("step_001", status="source_backed")),
    )
    cands = [c for s in report["proof_step_candidates"] for c in s["candidates"]]
    assert cands, "expected a candidate"
    for c in cands:
        assert c["status"] == "candidate"  # forced candidate, never source_backed
    validate_run(run_dir)


def test_scout_title_without_authors_stays_candidate_and_flags_missing(tmp_path: Path) -> None:
    run_dir = _run_with_step(tmp_path)
    report = run_literature_scout(
        run_dir=run_dir, client=_FakeClient(_candidate_payload("step_001", authors=[]))
    )
    cand = next(c for s in report["proof_step_candidates"] for c in s["candidates"])
    assert cand["status"] == "candidate"
    assert "authors" in cand["missing_metadata"]


def test_scout_locator_hint_without_exact_locator_flags_missing(tmp_path: Path) -> None:
    run_dir = _run_with_step(tmp_path)
    report = run_literature_scout(
        run_dir=run_dir,
        client=_FakeClient(_candidate_payload("step_001", locator_hint="around Thm 1")),
    )
    cand = next(c for s in report["proof_step_candidates"] for c in s["candidates"])
    # A hint is not an exact locator: it remains candidate and still needs grounding.
    assert cand["status"] == "candidate"
    assert "exact_locator" in cand["missing_metadata"]


def test_scout_candidate_for_unknown_step_is_dropped(tmp_path: Path) -> None:
    run_dir = _run_with_step(tmp_path)
    report = run_literature_scout(
        run_dir=run_dir, client=_FakeClient(_candidate_payload("totally_unknown_step"))
    )
    # The unknown step never appears, and its candidate does not leak onto step_001.
    ids = {s["proof_step_id"] for s in report["proof_step_candidates"]}
    assert "totally_unknown_step" not in ids
    assert all(len(s["candidates"]) == 0 for s in report["proof_step_candidates"])


def test_scout_never_invents_title(tmp_path: Path) -> None:
    run_dir = _run_with_step(tmp_path)
    report = run_literature_scout(
        run_dir=run_dir, client=_FakeClient(_candidate_payload("step_001", title=""))
    )
    # A candidate with no usable title is dropped, not fabricated.
    assert all(len(s["candidates"]) == 0 for s in report["proof_step_candidates"])


def test_scout_does_not_upgrade_source_grounding_status(tmp_path: Path) -> None:
    run_dir = _run_with_step(tmp_path)
    before = read_yaml(run_dir / "source_grounding.yaml")
    before_statuses = [s.get("status") for s in before.get("steps", [])]
    run_literature_scout(run_dir=run_dir, client=_FakeClient(_candidate_payload("step_001")))
    after = read_yaml(run_dir / "source_grounding.yaml")
    after_statuses = [s.get("status") for s in after.get("steps", [])]
    # The scout proposes candidates only; it cannot upgrade any source status.
    assert before_statuses == after_statuses
    assert "source_backed" not in after_statuses


def test_hand_edited_source_backed_candidate_is_rejected(tmp_path: Path) -> None:
    run_dir = _run_with_step(tmp_path)
    run_literature_scout(run_dir=run_dir, client=_FakeClient(_candidate_payload("step_001")))
    # Smuggle a source_backed status directly into the artifact and re-validate.
    art = read_yaml(run_dir / "literature_candidates.yaml")
    art["proof_step_candidates"][0]["candidates"][0]["status"] = "source_backed"
    write_yaml(run_dir / "literature_candidates.yaml", art)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_literature_scout_cli_stub(tmp_path: Path) -> None:
    run_dir = _run_with_step(tmp_path)
    result = CliRunner().invoke(
        app, ["literature-scout", "--run", str(run_dir), "--provider", "stub"]
    )
    assert result.exit_code == 0, result.output
    assert "Literature Scout wrote" in result.output
    assert (run_dir / "literature_candidates.yaml").exists()
    validate_run(run_dir)


def test_reviewer_source_requests_reads_canonical_review_fields() -> None:
    from espresso_proof.literature_scout import _reviewer_source_requests

    review = {
        "source_risks": ["No source cited for the center lemma"],
        "missing_hypotheses": ["finite p-group hypothesis not pinned"],
        "reviewers": [{"objections": ["needs a reference for G/Z(G) cyclic => abelian"]}],
    }
    got = _reviewer_source_requests(review)
    assert "No source cited for the center lemma" in got
    assert "finite p-group hypothesis not pinned" in got
    assert "needs a reference for G/Z(G) cyclic => abelian" in got
