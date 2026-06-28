"""Goal 66: simple math claim normalization + fast lock.

The claim-understanding layer used to mark standard elementary theorems (a
prime-order group is cyclic) as "unclear", tripping the claim-clear gate before
any proof-search work. A narrow, conservative normalizer now locks a PRECISE
claim for recognized phrasings — without weakening the gate for vague claims and
without upgrading any proof/source/evidence/refutation/checked status.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from espresso_proof.claim.claim_clear import evaluate_claim_clear
from espresso_proof.claim.claim_normalization import normalize_simple_claim, strip_command_wrapper
from espresso_proof.kernel.io import read_yaml
from espresso_proof.orchestration.orchestrator import orchestrate_run

TARGET_CLAIMS = [
    "prove that a group of order p is cyclic when p is a prime number",
    "prove that a group of order 3 is cyclic",
    "every group of prime order is cyclic",
]
HARD_NEGATIVES = [
    "prove that this group is nice",
    "prove that the object is regular",
    "show that the mirror object does something",
]


# --- helper: command-wrapper stripping --------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("prove that a group of order 3 is cyclic", "a group of order 3 is cyclic"),
    ("Show that X.", "X"),
    ("Is it true that every group of prime order is cyclic?", "every group of prime order is cyclic"),
    ("a group of order 3 is cyclic", "a group of order 3 is cyclic"),  # no wrapper
])
def test_strip_command_wrapper(raw: str, expected: str):
    assert strip_command_wrapper(raw) == expected


# --- normalizer recognizes the target patterns ------------------------------

def test_normalize_symbolic_prime_order():
    norm = normalize_simple_claim("prove that a group of order p is cyclic when p is a prime number")
    assert norm is not None
    assert norm["claim_precision"] == "precise"
    assert norm["conclusion"] == "G is cyclic."
    assert norm["missing_or_unclear"] == []
    assert "G: group" in norm["objects"] and "p: prime number" in norm["objects"]
    names = {h["name"] for h in norm["hypotheses"]}
    assert {"G is a group", "|G| = p", "p is prime"} <= names
    # every hypothesis status is a valid kernel status (no 'assumed')
    assert all(h["status"] in {"explicit", "inferred", "missing", "unclear"} for h in norm["hypotheses"])


def test_normalize_concrete_order():
    norm = normalize_simple_claim("prove that a group of order 3 is cyclic")
    assert norm is not None and norm["claim_precision"] == "precise"
    assert norm["objects"] == ["G: group"]  # no symbolic p for a concrete order
    names = {h["name"] for h in norm["hypotheses"]}
    assert {"G is a group", "|G| = 3"} <= names


def test_normalize_every_group_of_prime_order():
    norm = normalize_simple_claim("every group of prime order is cyclic")
    assert norm is not None and norm["claim_precision"] == "precise"


@pytest.mark.parametrize("claim", HARD_NEGATIVES)
def test_normalizer_does_not_over_normalize(claim: str):
    assert normalize_simple_claim(claim) is None


def test_normalizer_carries_no_status_fields():
    """Safety: the normalizer output proposes only a precise claim — it never
    carries a proof/source/evidence/refutation/checked/human_confirmed field."""
    norm = normalize_simple_claim("every group of prime order is cyclic")
    forbidden = {
        "proved", "proof_status", "source_backed", "refuted", "checked",
        "computation_checked", "human_confirmed", "evidence_status",
    }
    assert forbidden.isdisjoint(norm.keys())


# --- artifact-level regression (the failing run class) ----------------------

def _stub_run(claim: str) -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "c.md").write_text(claim + "\n", encoding="utf-8")
    res = orchestrate_run(
        project="workbench", input_path=d / "c.md", output_root=d,
        provider="stub", front_half="stub",
    )
    return Path(res["run_dir"])


@pytest.mark.parametrize("claim", TARGET_CLAIMS)
def test_target_claims_become_precise_and_clear(claim: str):
    run_dir = _stub_run(claim)
    ledger = read_yaml(run_dir / "hypothesis_ledger.yaml")
    locked = read_yaml(run_dir / "locked_claim.yaml")
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert ledger["claim_precision"] == "precise"
    assert evaluate_claim_clear(run_dir).clear is True
    assert manifest["current_state"] != "WAITING_CLAIM_CLARIFICATION"
    # claim only — no proof status upgrade
    assert locked["human_confirmed"] is False
    assert locked["conclusion"] == "G is cyclic."


@pytest.mark.parametrize("claim", HARD_NEGATIVES[:2])
def test_hard_negative_claims_still_blocked(claim: str):
    run_dir = _stub_run(claim)
    ledger = read_yaml(run_dir / "hypothesis_ledger.yaml")
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert ledger["claim_precision"] != "precise"
    assert evaluate_claim_clear(run_dir).clear is False
    assert manifest["current_state"] == "WAITING_CLAIM_CLARIFICATION"


def test_normalized_run_does_not_upgrade_math_status():
    """A normalized precise claim must not produce any earned math status."""
    run_dir = _stub_run("prove that a group of order p is cyclic when p is a prime number")
    locked = read_yaml(run_dir / "locked_claim.yaml")
    assert locked["human_confirmed"] is False
    idea = read_yaml(run_dir / "idea_grade.yaml")
    # idea_grade is conservative — never 'proved'
    assert idea.get("status") != "proved"
