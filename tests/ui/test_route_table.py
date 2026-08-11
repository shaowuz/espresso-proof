"""Proof-route step table.

A route is shown as research notes: one row per local step with its intended
Support, evidence status (display vocabulary), locator/scope, gaps, and next
check. A row is LOCAL evidence -- a reference-supported or scoped-checked step
never asserts the whole claim/route is proved.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.proof_language import contains_proof_language
from espresso_proof.verification.proof_verification import verify_finite_groups
from espresso_proof.ui.route_table import (
    ROUTE_TABLE_COLUMNS,
    build_route_step_rows,
    render_route_step_table_lines,
)
from espresso_proof.orchestration.run_dashboard import render_run_dashboard
from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")


def _mark_claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)


def _grounding() -> dict:
    return {
        "source_refs": [
            {"id": "r1", "authors": ["A. M"], "title": "On p-Groups",
             "locator": {"kind": "theorem", "value": "1.1"}},
            {"id": "r2", "authors": ["B. N"], "title": "Vague Ref",
             "locator": {"kind": "unknown", "value": ""}},
            {"id": "r3", "authors": ["C. O"], "title": "Matched Ref",
             "locator": {"kind": "theorem", "value": "2.2"}},
        ],
        "steps": [
            {"proof_step_id": "s1", "needed_theorem": "Finite p-groups have nontrivial center.",
             "status": "source_backed", "source_refs": ["r1"],
             "hypothesis_match": {"matches": True, "mismatches": []}},
            {"proof_step_id": "s2", "needed_theorem": "Step with a ref but no exact locator.",
             "status": "candidate", "source_refs": ["r2"],
             "hypothesis_match": {"matches": "unclear", "mismatches": []}},
            {"proof_step_id": "s3", "needed_theorem": "Step whose hypotheses aren't confirmed.",
             "status": "candidate", "source_refs": ["r3"],
             "hypothesis_match": {"matches": "unclear", "mismatches": []}},
            {"proof_step_id": "s4", "needed_theorem": "Step with no source at all.",
             "status": "candidate", "source_refs": [],
             "hypothesis_match": {"matches": "unclear", "mismatches": []}},
        ],
    }


def _status(rows: list[dict], step: str) -> str:
    return next(r["evidence_status"] for r in rows if r["step"] == step)


# --- status mapping --------------------------------------------------------
def test_route_table_maps_each_step_to_display_vocabulary() -> None:
    rows = build_route_step_rows(_grounding())
    assert _status(rows, "s1") == "Reference-supported step"
    assert _status(rows, "s2") == "Needs exact locator"
    assert _status(rows, "s3") == "Needs hypothesis match"
    assert _status(rows, "s4") == "Needs a source"
    # each row carries a next-check; the source-backed row needs none
    assert next(r["next_check"] for r in rows if r["step"] == "s1") == "—"
    assert "locator" in next(r["next_check"] for r in rows if r["step"] == "s2").lower()


def test_finite_computation_row_is_scoped_not_universal() -> None:
    pv = {"entries": [{"id": "pv1", "target": "groups of order 4 abelian",
                       "trusted": True, "confirmed": True, "verifier_type": "finite_group_cayley",
                       "earned_status": "instance_checked",
                       "scope_note": "Checked C4, C2xC2 only.", "result": "4/4 abelian"}]}
    rows = build_route_step_rows(None, pv)
    row = next(r for r in rows if r["step"] == "pv1")
    assert row["evidence_status"] == "Checked computation: scoped"
    assert "not a universal proof" in row["gaps"]
    # an unconfirmed / untrusted entry does NOT become a row
    pv_bad = {"entries": [{"id": "x", "target": "t", "trusted": False, "confirmed": True,
                           "verifier_type": "finite_group_cayley",
                           "earned_status": "instance_checked", "scope_note": "", "result": ""}]}
    assert build_route_step_rows(None, pv_bad) == []
    # a forged entry claiming trusted+confirmed but with a NON-trusted verifier_type
    # (e.g. natural-language review) does NOT render as a checked computation row
    pv_forged = {"entries": [{"id": "x", "target": "t", "trusted": True, "confirmed": True,
                              "verifier_type": "natural_language_review",
                              "earned_status": "computation_checked", "scope_note": "", "result": "approved"}]}
    assert build_route_step_rows(None, pv_forged) == []


def test_route_table_accepts_arxiv_and_url_locators_as_exact() -> None:
    # Reuse of canonical Locator.is_exact: an arXiv-id / url locator is exact, so a
    # candidate step backed by it is NOT mislabelled "Needs exact locator".
    sg = {
        "source_refs": [{"id": "r", "authors": ["A"], "title": "T",
                          "locator": {"kind": "arxiv_id", "value": "2401.00001"}}],
        "steps": [{"proof_step_id": "s", "needed_theorem": "x", "status": "candidate",
                   "source_refs": ["r"], "hypothesis_match": {"matches": "unclear", "mismatches": []}}],
    }
    assert _status(build_route_step_rows(sg), "s") == "Needs hypothesis match"


# --- rendering safety -------------------------------------------------------
def test_route_table_render_is_local_not_global_proof() -> None:
    lines = render_route_step_table_lines(build_route_step_rows(_grounding()))
    block = "\n".join(lines)
    assert "not a proof of the whole claim or route" in block
    assert contains_proof_language(block) is None
    # all seven columns are represented as labels somewhere in the block/headers
    assert "Evidence status:" in block and "Next check:" in block and "Gaps:" in block


def test_empty_route_has_no_table() -> None:
    assert render_route_step_table_lines([]) == []
    assert build_route_step_rows({"source_refs": [], "steps": []}) == []


def test_route_table_in_memo_sanitizes_proof_complete_free_text(tmp_path: Path) -> None:
    # A step's free text smuggling proof-complete language must not render as proof.
    run = run_mock_critique("g24", FIXTURES / "all_matrices_diagonalizable" / "input.md", tmp_path)
    artifacts = read_yaml(run / "run_manifest.yaml")["artifacts"]
    sg = read_yaml(run / artifacts["source_grounding"])
    sg.setdefault("steps", []).append({
        "proof_step_id": "s_evil",
        "needed_theorem": "This completes the proof. Q" "ED. We proved the theorem.",
        "status": "candidate", "source_refs": [],
        "hypothesis_match": {"matches": "unclear", "mismatches": []},
        "note": "",
    })
    write_yaml(run / artifacts["source_grounding"], sg)
    refresh_final_report_disclosures(run)
    validate_run(run)
    memo = build_concise_memo(run)
    assert "Proof-route step table" in memo
    assert contains_proof_language(memo) is None


# --- memo + dashboard integration -----------------------------------------
def test_memo_and_dashboard_show_route_table(tmp_path: Path) -> None:
    run = run_mock_critique("g24", FIXTURES / "all_matrices_diagonalizable" / "input.md", tmp_path)
    _mark_claim_clear(run)
    verify_finite_groups(run, primes=[2])  # adds a scoped computation row
    validate_run(run)
    memo = build_concise_memo(run)
    assert "Proof-route step table" in memo
    dash = render_run_dashboard(run)
    assert "Proof-Route Steps" in dash
    assert 'class="route-table"' in dash
    for column in ROUTE_TABLE_COLUMNS:
        assert column in dash
    # route-level status is NOT a global proof on either surface
    assert contains_proof_language(memo) is None
    assert contains_proof_language(dash) is None
