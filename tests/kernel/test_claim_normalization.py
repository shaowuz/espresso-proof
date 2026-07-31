"""Math claim normalization + fast lock, from SHAPE not from subject matter.

The claim-understanding layer can mark a standard theorem as "unclear",
tripping the claim-clear gate before any proof-search work. A conservative
normalizer locks a PRECISE claim for statements whose logical shape it can
recover — without weakening the gate for vague claims and without upgrading any
proof/source/evidence/refutation/checked status.

These tests used to pin one theorem's hand-authored output ("G is a group",
"|G| = p"). That was the defect, not the coverage: the normalizer carried an
answer key for a single sentence, so every paraphrase of every other theorem
fell through to "unclear". What is under test now is the property that must
hold for ANY statement — the parts are recovered from the user's own wording,
and hedged or shapeless text still gets nothing.
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
# Shapeless or hedged text: a well-formed sentence is not automatically a claim.
HEDGED = [
    "every group is probably interesting",
    "some objects are useful",
    "prove the Riemann hypothesis",
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

def test_parts_come_from_the_users_own_wording():
    norm = normalize_simple_claim("prove that a group of order p is cyclic when p is a prime number")
    assert norm is not None
    assert norm["claim_precision"] == "precise"
    assert norm["missing_or_unclear"] == []
    # The predicate is the user's, not a rewrite the module invented.
    assert norm["conclusion"].startswith("cyclic")
    assert "group of order p" in norm["objects"][0]
    assert "group of order p" in norm["hypotheses"][0]["name"]
    # every hypothesis status is a valid kernel status (no 'assumed')
    assert all(h["status"] in {"explicit", "inferred", "missing", "unclear"} for h in norm["hypotheses"])


@pytest.mark.parametrize("claim,shape", [
    ("every group of prime order is cyclic", "universal"),
    ("prove that a group of order 3 is cyclic", "indefinite"),
    ("if K/Q is a Galois extension, then Gal(K/Q) is finite", "conditional"),
    ("each continuous function on a compact set is bounded", "universal"),
    ("every graph with minimum degree at least n/2 is Hamiltonian", "universal"),
])
def test_shapes_are_recognized_without_any_subject_vocabulary(claim: str, shape: str):
    """The point of the rewrite: no theorem gets treatment no other theorem gets."""
    norm = normalize_simple_claim(claim)
    assert norm is not None and norm["claim_precision"] == "precise"
    assert norm["recognized"] == shape


def test_the_module_knows_no_mathematics():
    # A guard against the answer key coming back: no subject-matter vocabulary
    # may appear in the module's source.
    import inspect

    from espresso_proof.claim import claim_normalization

    body = "".join(
        line for line in inspect.getsource(claim_normalization).splitlines(keepends=True)
        if not line.strip().startswith("#")
    )
    code = body.split('"""')[0] + '"""'.join(body.split('"""')[2:])
    for term in ("cyclic", "prime", "abelian", "galois", "|G|"):
        assert term not in code.lower(), term


@pytest.mark.parametrize("claim", HARD_NEGATIVES + HEDGED)
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
    assert locked["conclusion"].strip()


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
