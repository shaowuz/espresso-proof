"""Retrieval -> Source Grounding tests.

Positive: a local source (corpus or explicit file) is resolved, extracted,
grounded, and a step earns source support. Negative/fail-closed: an unresolved
candidate, a network-disabled url, missing attribution, an unknown locator, and
mismatched hypotheses all leave the step candidate and the run valid; a
retrieval report can never carry a trusted status.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.models import RetrievalResolutionReport
from espresso_proof.literature.retrieval_grounding import retrieve_and_ground
from espresso_proof.kernel.validators import validate_run

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "all_matrices_diagonalizable" / "input.md"
PGROUP_THEOREM = (
    "Theorem 1.1. Let G be a group with |G| = p^2 where p is prime; then the "
    "center Z(G) is nontrivial and G is abelian."
)


def _hyp(name: str) -> dict:
    return {"name": name, "status": "explicit", "source": "user"}


def _run_needing_pgroup(tmp_path: Path, *, hypotheses: list[dict] | None = None) -> Path:
    """A run whose first source-grounding step needs the p^2 theorem."""
    run_dir = run_mock_critique("linear_algebra", FIXTURE, tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / "locked_claim.yaml")
    claim["hypotheses"] = hypotheses or [_hyp("group of order p^2"), _hyp("p is prime")]
    write_yaml(run_dir / "locked_claim.yaml", claim)
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    sg["steps"][0]["needed_theorem"] = "group order abelian center"
    sg["steps"][0]["status"] = "candidate"
    sg["steps"][0]["source_refs"] = []
    sg["steps"][0]["hypothesis_match"] = {"matches": "unclear", "mismatches": []}
    write_yaml(run_dir / "source_grounding.yaml", sg)
    return run_dir


def _step_status(run_dir: Path) -> str:
    return read_yaml(run_dir / "source_grounding.yaml")["steps"][0]["status"]


def _backed_count(run_dir: Path) -> int:
    sg = read_yaml(run_dir / "source_grounding.yaml")
    return sum(1 for r in sg["source_refs"] if r["status"] == "source_backed")


def test_local_corpus_resolves_extracts_grounds_and_earns_support(tmp_path: Path) -> None:
    run_dir = _run_needing_pgroup(tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "pgroups.txt").write_text(PGROUP_THEOREM, encoding="utf-8")

    report = retrieve_and_ground(
        run_dir=run_dir,
        local_corpus=corpus,
        source_id="src_group",
        title="A Note on p-groups",
        authors=["A. Mathematician"],
    )
    assert report["resolution_status"] == "resolved"
    assert report["source_kind"] == "local_corpus"
    assert report["grounding_attempted"] is True
    assert _step_status(run_dir) == "source_backed"
    assert _backed_count(run_dir) == 1
    validate_run(run_dir)


def test_explicit_local_source_earns_support(tmp_path: Path) -> None:
    run_dir = _run_needing_pgroup(tmp_path)
    src = tmp_path / "note.txt"
    src.write_text(PGROUP_THEOREM, encoding="utf-8")
    report = retrieve_and_ground(
        run_dir=run_dir, local_source=src, source_id="src_group",
        title="A Note on p-groups", authors=["A. Mathematician"],
    )
    assert report["resolution_status"] == "resolved"
    assert report["source_kind"] == "local_file"
    assert _step_status(run_dir) == "source_backed"
    validate_run(run_dir)


def test_unresolved_candidate_stays_candidate_and_run_valid(tmp_path: Path) -> None:
    run_dir = _run_needing_pgroup(tmp_path)
    empty = tmp_path / "empty_corpus"
    empty.mkdir()
    report = retrieve_and_ground(run_dir=run_dir, local_corpus=empty)
    assert report["resolution_status"] == "failed"
    assert report["grounding_attempted"] is False
    assert _step_status(run_dir) == "candidate"
    assert _backed_count(run_dir) == 0
    validate_run(run_dir)


def test_missing_local_source_fails_closed(tmp_path: Path) -> None:
    run_dir = _run_needing_pgroup(tmp_path)
    report = retrieve_and_ground(run_dir=run_dir, local_source=tmp_path / "nope.txt")
    assert report["resolution_status"] == "failed"
    assert _step_status(run_dir) == "candidate"
    validate_run(run_dir)


def test_url_without_network_is_skipped_not_fetched(tmp_path: Path) -> None:
    run_dir = _run_needing_pgroup(tmp_path)
    report = retrieve_and_ground(
        run_dir=run_dir, url="https://arxiv.org/abs/0808.3351", network_enabled=False
    )
    assert report["resolution_status"] == "skipped"
    assert report["source_kind"] == "arxiv"
    assert report["grounding_attempted"] is False
    assert _step_status(run_dir) == "candidate"
    validate_run(run_dir)


def test_source_without_authors_cannot_earn_support(tmp_path: Path) -> None:
    run_dir = _run_needing_pgroup(tmp_path)
    src = tmp_path / "note.txt"
    src.write_text(PGROUP_THEOREM, encoding="utf-8")
    # No authors -> attribution incomplete -> source_backed gate fails closed.
    report = retrieve_and_ground(run_dir=run_dir, local_source=src, authors=[], title="t")
    assert report["resolution_status"] == "resolved"
    assert _step_status(run_dir) == "candidate"
    assert _backed_count(run_dir) == 0
    validate_run(run_dir)


def test_source_without_exact_locator_cannot_earn_support(tmp_path: Path) -> None:
    run_dir = _run_needing_pgroup(tmp_path)
    src = tmp_path / "note.txt"
    # No "Theorem X.Y" label -> no exact locator -> stays candidate.
    src.write_text(
        "A group with order p squared where p is prime has nontrivial center and is abelian.",
        encoding="utf-8",
    )
    report = retrieve_and_ground(
        run_dir=run_dir, local_source=src, title="Untitled", authors=["A. Mathematician"]
    )
    assert report["resolution_status"] == "resolved"
    assert _step_status(run_dir) == "candidate"
    validate_run(run_dir)


def test_mismatched_hypotheses_stay_candidate(tmp_path: Path) -> None:
    # The hypothesis is relevant to the group step but contains conditions the
    # source theorem does not cover.
    run_dir = _run_needing_pgroup(
        tmp_path, hypotheses=[_hyp("finite nonabelian group")]
    )
    src = tmp_path / "note.txt"
    src.write_text(PGROUP_THEOREM, encoding="utf-8")
    report = retrieve_and_ground(
        run_dir=run_dir, local_source=src, title="A Note on p-groups", authors=["A. Mathematician"]
    )
    assert report["resolution_status"] == "resolved"
    assert _step_status(run_dir) == "candidate"
    assert _backed_count(run_dir) == 0
    validate_run(run_dir)


def test_retrieval_report_rejects_trusted_status_field() -> None:
    # StrictBaseModel (extra=forbid): a smuggled source_backed/proof field fails.
    with pytest.raises(Exception):
        RetrievalResolutionReport.model_validate(
            {
                "claim_id": "c",
                "resolution_status": "resolved",
                "source_backed": True,  # not a field -> rejected
            }
        )
