"""Persisted claim/ledger normalization (conservative, claim-clear-neutral).

Proves the helper: consolidates expanded group-axiom hypotheses on an
already-persisted run; is idempotent (a second call is a no-op); preserves the
protected fields; and NEVER flips claim_precision / unblocks a blocked claim.
"""
from __future__ import annotations

from pathlib import Path

from espresso_proof.claim.claim_clear import evaluate_claim_clear
from espresso_proof.claim.persisted_claim_normalization import (
    normalize_persisted_claim_ledger,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.orchestrator import orchestrate_run

CLEAR_CLAIM = "Every group of prime order is cyclic."
VAGUE_CLAIM = "Prove the thing about the nice mirror object."
_EXPANDED = "G is a group (closure, associativity, identity, inverses)"


def _run(tmp_path: Path, claim: str, name: str) -> Path:
    p = tmp_path / f"{name}.md"
    p.write_text(claim, encoding="utf-8")
    result = orchestrate_run(
        project=name, input_path=p, output_root=tmp_path,
        provider="stub", front_half="stub", fast=True,
    )
    return Path(result["run_dir"])


def _paths(run_dir: Path) -> tuple[Path, Path]:
    m = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return run_dir / m["locked_claim"], run_dir / m["hypothesis_ledger"]


def _expand_group_hyp(run_dir: Path) -> None:
    """Denormalize: put the axiom-parenthetical form on the claim and the ledger,
    keeping the run kernel-valid.

    The handle used to be the literal hypothesis name "G is a group", which only
    existed because the claim normalizer carried a hand-authored answer key for
    this one theorem. The normalizer now reports the statement's own wording, so
    the fixture writes the expanded form onto the FIRST hypothesis instead of
    looking for a name the system no longer invents. What is under test is the
    axiom consolidator, not how the claim happened to be phrased.
    """
    claim_path, ledger_path = _paths(run_dir)
    for path in (claim_path, ledger_path):
        doc = read_yaml(path)
        hypotheses = doc.get("hypotheses") or []
        for h in hypotheses:
            if isinstance(h, dict):
                h["name"] = _EXPANDED
                break
        write_yaml(path, doc)
    validate_run(run_dir)


def test_consolidates_expanded_axioms(tmp_path: Path) -> None:
    run_dir = _run(tmp_path, CLEAR_CLAIM, "clear")
    assert evaluate_claim_clear(run_dir).clear  # reached the gate, precise

    _expand_group_hyp(run_dir)
    assert any(
        h["name"] == _EXPANDED for h in read_yaml(_paths(run_dir)[1])["hypotheses"]
    )

    changed = normalize_persisted_claim_ledger(run_dir)
    assert changed is True

    _, ledger_path = _paths(run_dir)
    names = [h["name"] for h in read_yaml(ledger_path)["hypotheses"]]
    assert "G is a group" in names
    assert _EXPANDED not in names
    # claim-clear verdict unchanged + run still valid
    assert evaluate_claim_clear(run_dir).clear
    validate_run(run_dir)
    notes = read_yaml(ledger_path).get("notes") or []
    assert any("persisted-claim normalization" in n for n in notes)


def test_idempotent_noop(tmp_path: Path) -> None:
    run_dir = _run(tmp_path, CLEAR_CLAIM, "idem")
    # a freshly-generated run is already normalized → no-op
    assert normalize_persisted_claim_ledger(run_dir) is False

    _expand_group_hyp(run_dir)
    assert normalize_persisted_claim_ledger(run_dir) is True

    claim_path, ledger_path = _paths(run_dir)
    before = (claim_path.read_bytes(), ledger_path.read_bytes())
    # second call changes nothing
    assert normalize_persisted_claim_ledger(run_dir) is False
    assert (claim_path.read_bytes(), ledger_path.read_bytes()) == before


def test_does_not_unblock_imprecise_claim(tmp_path: Path) -> None:
    run_dir = _run(tmp_path, VAGUE_CLAIM, "vague")
    assert not evaluate_claim_clear(run_dir).clear  # blocked by imprecision

    claim_path, ledger_path = _paths(run_dir)
    prec_before = read_yaml(ledger_path).get("claim_precision")
    missing_before = read_yaml(ledger_path).get("missing_or_unclear")

    # give it an expanded-axiom hypothesis to consolidate (keep it blocked)
    for path in (claim_path, ledger_path):
        doc = read_yaml(path)
        doc["hypotheses"] = [{"name": _EXPANDED, "status": "explicit", "source": "user"}]
        write_yaml(path, doc)
    validate_run(run_dir)

    normalize_persisted_claim_ledger(run_dir)

    ledger = read_yaml(ledger_path)
    assert [h["name"] for h in ledger["hypotheses"]] == ["G is a group"]  # consolidated
    assert ledger.get("claim_precision") == prec_before        # NOT flipped
    assert ledger.get("missing_or_unclear") == missing_before  # preserved
    assert not evaluate_claim_clear(run_dir).clear             # still blocked


def test_preserves_protected_fields(tmp_path: Path) -> None:
    run_dir = _run(tmp_path, CLEAR_CLAIM, "preserve")
    claim_path, _ = _paths(run_dir)
    claim_before = read_yaml(claim_path)

    _expand_group_hyp(run_dir)
    normalize_persisted_claim_ledger(run_dir)

    claim = read_yaml(claim_path)
    assert claim["raw_text"] == claim_before["raw_text"]
    assert claim.get("human_confirmed") is False
    # only the hypotheses content changed — no new (status) keys added to the claim
    assert set(claim.keys()) == set(claim_before.keys())
