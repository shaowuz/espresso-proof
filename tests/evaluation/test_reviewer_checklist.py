"""Reviewer quality calibration V1.

Artifact-grounded, deterministic reviewer helpers: findings cite an artifact and
declare blocker/non-blocker, proof-complete language is flagged, invalid
source_backed is flagged, and non-blocking suggestions never block a merge.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.evaluation import reviewer_checklist as reviewer_checklist_module
from espresso_proof.kernel.io import read_text
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.evaluation.reviewer_checklist import (
    detect_proof_complete_language,
    find_invalid_source_backed,
    lint_reviewer_finding,
    merge_allowed,
    review_checklist,
)

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path) -> Path:
    return run_mock_critique(
        "hodge_atoms", FIXTURES / "promising_but_unproved" / "input.md", tmp_path
    )


# ---------------------------------------------------------------------------
# 1) Reviewer spec requires artifact citation (+ the other calibration rules).
# ---------------------------------------------------------------------------


def test_reviewer_checklist_documents_artifact_citation_and_rules() -> None:
    text = (
        (reviewer_checklist_module.__doc__ or "")
        + "\n"
        + (review_checklist.__doc__ or "")
    ).lower()
    assert "artifact-grounded" in text
    assert "blocker" in text and "non-blocker" in text
    assert "proof-complete language" in text
    assert "exact locator" in text and "attribution" in text and "hypothesis match" in text


# ---------------------------------------------------------------------------
# 2) Finding linter distinguishes blocker / non-blocker and requires a citation.
# ---------------------------------------------------------------------------


def test_lint_finding_severity_and_citation() -> None:
    blk = lint_reviewer_finding({"severity": "blocker", "text": "idea_grade.yaml over-claims"})
    assert blk["severity"] == "blocker" and blk["cites_artifact"] and blk["well_formed"]
    # unmarked severity defaults to non-blocker (never blocks by accident).
    nit = lint_reviewer_finding({"text": "rename helper in research_bank.py"})
    assert nit["severity"] == "non_blocker"
    # a finding citing no artifact is not well-formed.
    vague = lint_reviewer_finding({"severity": "blocker", "text": "this looks wrong"})
    assert vague["cites_artifact"] is False and vague["well_formed"] is False


# ---------------------------------------------------------------------------
# 3) Proof-complete language is flagged (and conservative phrasings are not).
# ---------------------------------------------------------------------------


def test_detect_proof_complete_language() -> None:
    assert detect_proof_complete_language("Therefore we proved the claim. proof-close.")
    assert "proof complete" in detect_proof_complete_language("The proof complete; done.")
    # legitimate conservative phrasings must NOT be flagged.
    assert detect_proof_complete_language("The claim is unproved and not verified.") == []
    assert detect_proof_complete_language("No checked counterexample has been verified.") == []


# ---------------------------------------------------------------------------
# 4) Invalid source-backed is flagged.
# ---------------------------------------------------------------------------


def test_find_invalid_source_backed() -> None:
    sg = {
        "source_refs": [
            {  # missing exact locator + attribution
                "id": "bad_ref",
                "status": "source_backed",
                "locator": {"kind": "unknown", "value": ""},
                "authors": [],
                "title": "",
                "hypothesis_match": {"matches": "unclear"},
            },
            {  # a candidate ref is not flagged
                "id": "ok_candidate",
                "status": "candidate",
                "locator": {"kind": "unknown", "value": ""},
                "authors": [],
                "title": "",
                "hypothesis_match": {"matches": "unclear"},
            },
        ],
        "steps": [],
    }
    problems = find_invalid_source_backed(sg)
    assert len(problems) == 1 and problems[0]["ref_id"] == "bad_ref"
    assert "missing exact locator" in problems[0]["reasons"]
    # a fully-valid source_backed ref is not flagged.
    sg["source_refs"][0] = {
        "id": "good_ref", "status": "source_backed",
        "locator": {"kind": "theorem", "value": "Theorem 1.1"},
        "authors": ["A. Mathematician"], "title": "A Source",
        "hypothesis_match": {"matches": True},
    }
    assert find_invalid_source_backed(sg) == []


# ---------------------------------------------------------------------------
# 5) Non-blocking suggestions do not block a merge.
# ---------------------------------------------------------------------------


def test_non_blocking_suggestions_do_not_block_merge() -> None:
    nits = [
        {"severity": "non-blocker", "text": "cli.py: tidy import order"},
        {"text": "memo.py: consider a comment"},  # unmarked -> non-blocker
    ]
    assert merge_allowed(nits) is True
    with_blocker = nits + [{"severity": "blocker", "text": "source_grounding.yaml: invalid source_backed"}]
    assert merge_allowed(with_blocker) is False


# ---------------------------------------------------------------------------
# End-to-end checklist over a run + CLI.
# ---------------------------------------------------------------------------


def test_review_checklist_clean_run_allows_merge(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    result = review_checklist(run_dir)
    assert result["findings"] == []
    assert result["merge_allowed"] is True


def test_review_checklist_flags_injected_proof_complete_language(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    fr = run_dir / "final_report.md"
    fr.write_text(read_text(fr) + "\n\nConclusion: we proved the claim. proof-close.\n", encoding="utf-8")
    result = review_checklist(run_dir)
    assert result["merge_allowed"] is False
    assert any(f["severity"] == "blocker" and "final_report" in f["artifact"] for f in result["findings"])


def test_cli_review_checklist_clean_run(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    out = CliRunner().invoke(app, ["review-checklist", "--run", str(run_dir)])
    assert out.exit_code == 0, out.output
    assert "merge_allowed: True" in out.output
