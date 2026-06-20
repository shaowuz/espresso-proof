"""Phase 6 Slice C: honest surfacing of the agent-consensus mock check.

The amber status must read as "agent consensus, not a machine check" everywhere and
NEVER as a real/green/checked proof, and its memo wording must never trip the
final-report forbidden-proof-language gate (the status is scanned like the other
weak statuses).
"""
from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.io import read_text, read_yaml
from espresso_proof.kernel.validators import (
    _first_forbidden_proof_language,
    validate_run,
)
from espresso_proof.orchestration.agent_consensus_seal import seal_agent_consensus
from espresso_proof.orchestration.run_dashboard import _proof_label
from espresso_proof.ui.descriptions import (
    VERIFIED_PROOF_STATUSES,
    describe_grade,
    describe_proof_language,
)
from espresso_proof.ui.evidence_trust import EARNED_LEVELS, is_green, trust_label, trust_tone

_AMBER = "agent_consensus_mock_checked"


def test_description_is_advisory_not_a_checked_proof() -> None:
    desc = describe_proof_language(_AMBER)
    assert "mock check" in desc
    assert "not a machine check" in desc
    assert "verified" not in desc.lower()  # never claims verification
    # never routed through the "a proof has been checked" sentence
    assert _AMBER not in VERIFIED_PROOF_STATUSES
    assert "no proof has been checked" in describe_grade(status="promising", proof_language_status=_AMBER)


def test_trust_level_is_amber_never_green() -> None:
    assert trust_tone("agent_consensus") == "candidate"  # amber, not "earned"
    assert "agent_consensus" not in EARNED_LEVELS
    assert is_green("agent_consensus") is False
    assert "machine check" in trust_label("agent_consensus")


def test_dashboard_label_is_mock_check_not_checked() -> None:
    label = _proof_label({"proof_language_status": _AMBER}, repair_needed=False, template_fallback=True)
    assert "Mock-checked" in label
    assert label not in {"Checked", "Scoped check"}  # never the verified labels


def test_surface_wordings_are_scan_clean() -> None:
    # regression guard: no surfaced wording may contain forbidden proof language
    # (a future edit adding "verified"/"proved"/"proof-close" would break a sealed run's memo).
    for text in (
        describe_proof_language(_AMBER),
        trust_label("agent_consensus"),
        _proof_label({"proof_language_status": _AMBER}, False, True),
    ):
        assert _first_forbidden_proof_language(text.lower()) is None, text


def test_sealed_memo_discloses_mock_check_and_validates(tmp_path: Path) -> None:
    from tests.test_phase6_agent_consensus_seal import _ConsensusStub, _sealable_run

    run_dir = _sealable_run(tmp_path)
    seal_agent_consensus(run_dir, client=_ConsensusStub())
    validate_run(run_dir)  # the rebuilt memo must NOT trip the forbidden-language gate
    fr_rel = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["final_report"]
    memo = read_text(run_dir / fr_rel)
    assert "Agent-consensus mock check" in memo
    assert "not a machine check" in memo
    assert "residual (non-fatal) gap" in memo
    assert "a mock check" in memo  # the Proof state line wording
    assert "3 residual (non-fatal) gap" in memo  # 3 reviewers x 1 residual each
    # honesty: the memo never claims a real/checked proof
    assert _first_forbidden_proof_language(memo.lower()) is None


def test_sealed_memo_never_renders_raw_reviewer_text(tmp_path: Path) -> None:
    # Safety invariant: only the residual COUNT reaches the scanned final_report --
    # never the raw reviewer free text (which could carry forbidden language).
    from tests.test_phase6_agent_consensus_seal import _ConsensusStub, _sealable_run

    run_dir = _sealable_run(tmp_path)
    seal_agent_consensus(run_dir, client=_ConsensusStub())
    fr_rel = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["final_report"]
    memo = read_text(run_dir / fr_rel)
    # the stub's raw reviewer text must NOT appear in the memo
    assert "pending exact locator" not in memo  # the residual_objections raw text
    assert "stub finding" not in memo  # the entry.finding raw text
    # but the raw text IS preserved in the (un-scanned) sidecar for inspection
    acr = read_yaml(run_dir / "agent_consensus_review.yaml")
    assert any("pending exact locator" in o for e in acr["entries"] for o in e["residual_objections"])


def test_downgrade_drops_the_mock_check_disclosure(tmp_path: Path) -> None:
    # When a re-seal loses consensus (Slice B downgrades amber -> proof_candidate),
    # the rebuilt memo must STOP disclosing the mock check.
    from tests.test_phase6_agent_consensus_seal import _ConsensusStub, _sealable_run

    run_dir = _sealable_run(tmp_path)
    seal_agent_consensus(run_dir, client=_ConsensusStub())
    fr_rel = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["final_report"]
    assert "Agent-consensus mock check" in read_text(run_dir / fr_rel)
    seal_agent_consensus(run_dir, client=_ConsensusStub({"line_proof_reviewer": "fatal_flaw_found"}))
    validate_run(run_dir)
    memo = read_text(run_dir / fr_rel)
    assert "Agent-consensus mock check" not in memo  # disclosure dropped on downgrade
