"""Goal 67A: claim-clear must pass a precise claim whose only missing_or_unclear
entries are advisory clarifications (the external-mode failure), while still
blocking genuine gaps. Default-deny: unknown / core-gap items always block.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from espresso_proof.claim_clarity import is_advisory_missing_item, is_blocking_missing_item
from espresso_proof.claim_clear import evaluate_claim_clear
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.orchestrator import orchestrate_run

# the two real external-mode pedantic notes that wrongly blocked precise claims
ADVISORY_NOTES = [
    "Whether the intended quantifier is explicitly universal: every group of order 3 is cyclic.",
    "Whether p is explicitly intended to range over prime numbers should be confirmed if strict literal parsing is required.",
]
# genuine gaps (must always block)
BLOCKING_NOTES = [
    "conclusion",
    "hypotheses",
    "main objects",
    "The conclusion is not stated.",          # full sentence naming a core gap
    "Whether the conclusion holds is unclear.",  # hedged BUT names a core gap -> blocks
    "What field are we working over?",         # unknown non-core -> default-deny blocks
]


@pytest.mark.parametrize("note", ADVISORY_NOTES)
def test_advisory_notes_are_not_blocking(note: str):
    assert is_advisory_missing_item(note) is True
    assert is_blocking_missing_item(note) is False


@pytest.mark.parametrize("note", BLOCKING_NOTES)
def test_genuine_gaps_block(note: str):
    assert is_advisory_missing_item(note) is False
    assert is_blocking_missing_item(note) is True


def test_empty_item_is_not_blocking():
    assert is_blocking_missing_item("") is False
    assert is_blocking_missing_item("   ") is False


# --- integration: evaluate_claim_clear on a precise claim with advisory notes --

def _precise_run(tmp_path: Path) -> Path:
    claim = tmp_path / "c.md"
    claim.write_text("prove that a group of order 3 is cyclic\n", encoding="utf-8")
    res = orchestrate_run(project="workbench", input_path=claim, output_root=tmp_path,
                          provider="stub", front_half="stub")
    return Path(res["run_dir"])


def _set_missing(run_dir: Path, items: list[str]) -> None:
    ledger = read_yaml(run_dir / "hypothesis_ledger.yaml")
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = items
    write_yaml(run_dir / "hypothesis_ledger.yaml", ledger)


def test_precise_claim_with_only_advisory_notes_clears(tmp_path: Path):
    run_dir = _precise_run(tmp_path)
    _set_missing(run_dir, [ADVISORY_NOTES[0]])
    assert evaluate_claim_clear(run_dir).clear is True


def test_precise_claim_with_a_real_gap_still_blocks(tmp_path: Path):
    run_dir = _precise_run(tmp_path)
    _set_missing(run_dir, [ADVISORY_NOTES[0], "The conclusion is not stated."])
    assert evaluate_claim_clear(run_dir).clear is False


def test_goal66_hard_negative_still_blocks(tmp_path: Path):
    claim = tmp_path / "c.md"
    claim.write_text("prove that this group is nice\n", encoding="utf-8")
    res = orchestrate_run(project="workbench", input_path=claim, output_root=tmp_path,
                          provider="stub", front_half="stub")
    run_dir = Path(res["run_dir"])
    assert evaluate_claim_clear(run_dir).clear is False
    assert read_yaml(run_dir / "run_manifest.yaml")["current_state"] == "WAITING_CLAIM_CLARIFICATION"


# --- 67C/67A interaction: an earned scoped/checked status beats template label --

from espresso_proof.run_dashboard import _proof_label


def test_earned_checked_status_shows_even_in_template_run():
    # a machine-checked scoped result is honest evidence independent of the prover
    assert _proof_label({"proof_language_status": "computation_checked"},
                        repair_needed=False, template_fallback=True) == "Scoped check"
    assert _proof_label({"proof_language_status": "human_confirmed"},
                        repair_needed=True, template_fallback=True) == "Checked"


def test_template_run_without_earned_status_says_template_only():
    assert _proof_label({"proof_language_status": "proof_sketch"},
                        repair_needed=True, template_fallback=True) == "Template only — not a real attempt"
    assert _proof_label({}, repair_needed=True, template_fallback=True) == "Template only — not a real attempt"
