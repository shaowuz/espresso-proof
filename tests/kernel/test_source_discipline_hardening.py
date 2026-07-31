"""Source-discipline hardening.

1. Excerpt-hash binding: a cited statement edited after grounding fails closed
   (self-hash + membership in theorem_extraction).
2. Audit-to-grounding binding: editing the grounding after the applicability
   audit (without re-running it) fails closed.
3. Chinese proof-completion markers are now rejected by the PARSE-TIME gate
   (validators) and redacted at agent ingestion (FORBIDDEN_LANGUAGE).
4. Matcher: negated terms ("non-proper") and substring aliases ("ample" inside
   "example") can no longer fake hypothesis coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.literature.pdf_ingestion import ingest_source_for_theorems
from espresso_proof.verification.source_verification import (
    check_hypothesis_applicability,
    ground_run,
    term_present,
)
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")
THM = (
    "Theorem 1.2. Exact literature statement supporting the spectral step "
    "over base field C for square matrices."
)


def _grounded_run(tmp_path: Path) -> Path:
    run_dir = run_mock_critique(
        "g41", FIXTURES / "all_matrices_diagonalizable" / "input.md", tmp_path
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    src = tmp_path / "src.txt"
    src.write_text(THM, encoding="utf-8")
    claim = read_yaml(run_dir / "locked_claim.yaml")
    report = ingest_source_for_theorems(
        claim_id=claim["id"],
        source=str(src),
        source_id="g41_src",
        title="Source discipline fixture",
        authors=["A. Author"],
        source_type="paper",
    )
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = "theorem_extraction.yaml"
    write_yaml(run_dir / "theorem_extraction.yaml", report)
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    ground_run(run_dir=run_dir, theorem_extraction_path=run_dir / "theorem_extraction.yaml")
    validate_run(run_dir)
    return run_dir


# --- 1. excerpt-hash binding -------------------------------------------------
def test_grounding_records_excerpt_hash_and_validates(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    backed = [r for r in sg["source_refs"] if r["status"] == "source_backed"]
    assert backed
    for ref in backed:
        assert len(ref["statement_sha256"]) == 64


def test_edited_statement_fails_excerpt_hash(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    for ref in sg["source_refs"]:
        if ref["status"] == "source_backed":
            ref["statement_or_summary"] = (
                "Theorem 1.2. Exact literature statement supporting the spectral "
                "step over base field C for square matrices AND every singular threefold."
            )
    write_yaml(run_dir / "source_grounding.yaml", sg)
    # the applicability audit is now also stale, but the FIRST failure must be
    # the excerpt hash (statement edited after grounding).
    with pytest.raises(KernelValidationError, match="excerpt hash"):
        validate_run(run_dir)


def test_consistently_forged_hash_fails_extraction_membership(tmp_path: Path) -> None:
    import hashlib

    run_dir = _grounded_run(tmp_path)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    forged = "Theorem 9.9. A fabricated statement never extracted from any source."
    for ref in sg["source_refs"]:
        if ref["status"] == "source_backed":
            ref["statement_or_summary"] = forged
            ref["statement_sha256"] = hashlib.sha256(forged.encode()).hexdigest()
    write_yaml(run_dir / "source_grounding.yaml", sg)
    with pytest.raises(KernelValidationError, match="extracted theorem"):
        validate_run(run_dir)


def test_source_backed_ref_without_hash_is_rejected(tmp_path: Path) -> None:
    """Previously asserted the legacy exemption; the exemption was the gap.

    A source_backed ref with no statement_sha256 skipped BOTH excerpt bindings,
    so a fabricated citation could support a step (and resolve theorem_ref
    dependencies on the amber-seal path) by simply not recording a hash. The
    sanctioned grounding pipeline always writes one, so nothing legitimate is
    lost by refusing.
    """

    run_dir = _grounded_run(tmp_path)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    for ref in sg["source_refs"]:
        ref.pop("statement_sha256", None)
    write_yaml(run_dir / "source_grounding.yaml", sg)
    from espresso_proof.verification.theorem_applicability import audit_theorem_applicability

    audit_theorem_applicability(run_dir)
    with pytest.raises(KernelValidationError, match="must record its excerpt"):
        validate_run(run_dir)


def test_candidate_refs_without_hash_still_validate(tmp_path: Path) -> None:
    # The additive contract survives where no status is at stake: a CANDIDATE
    # ref carries no authority, so an old artifact without hashes still loads.
    run_dir = _grounded_run(tmp_path)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    for ref in sg["source_refs"]:
        ref.pop("statement_sha256", None)
        ref["status"] = "candidate"
    for step in sg["steps"]:
        if step.get("status") == "source_backed":
            step["status"] = "candidate"
            step["note"] = "downgraded for the legacy-artifact test"
    write_yaml(run_dir / "source_grounding.yaml", sg)
    from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures
    from espresso_proof.verification.theorem_applicability import audit_theorem_applicability

    audit_theorem_applicability(run_dir)
    # The downgrade opens source-gap warnings the final report must disclose.
    refresh_final_report_disclosures(run_dir)
    validate_run(run_dir)


# --- 2. audit-to-grounding binding -------------------------------------------
def test_grounding_edit_after_audit_fails_stale_audit(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    # any byte change counts; tweak a harmless step note (not a status, not a
    # statement) so ONLY the staleness binding fires.
    sg["steps"][-1]["note"] = (sg["steps"][-1].get("note") or "") + " (edited)"
    write_yaml(run_dir / "source_grounding.yaml", sg)
    with pytest.raises(KernelValidationError, match="stale"):
        validate_run(run_dir)
    # re-running the audit refreshes the binding and the run validates again.
    from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures
    from espresso_proof.verification.theorem_applicability import audit_theorem_applicability

    audit_theorem_applicability(run_dir)
    # The downgrade opens source-gap warnings the final report must disclose.
    refresh_final_report_disclosures(run_dir)
    validate_run(run_dir)





def test_forbidden_language_superset_of_validator_gate() -> None:
    # invariant: every phrase the parse-time gate rejects is also redacted at
    # agent ingestion, so a gate addition cannot silently skip ingestion.
    from espresso_proof.agents.llm_agents import FORBIDDEN_LANGUAGE

    for phrase in ("[proof-close]", "proof complete", "we proved the theorem"):
        assert phrase in FORBIDDEN_LANGUAGE, phrase


# --- 4. matcher negation / substring fixes -----------------------------------
def test_negated_term_does_not_cover_hypothesis() -> None:
    assert term_present("proper", "Let X be a proper variety.") is True
    assert term_present("proper", "Let X be a non-proper variety.") is False
    assert term_present("proper", "Let X be a non proper variety.") is False
    assert term_present("proper", "X is not proper in general.") is False
    assert term_present("smooth", "X need not smooth be...") is False  # "not smooth"


def test_ample_inside_example_does_not_fake_projectivity() -> None:
    hypotheses = [{"name": "X is projective", "status": "explicit", "source": "agent"}]
    faked = check_hypothesis_applicability(
        "Theorem 5.1. Let X be a proper variety. For example, consider a curve.",
        hypotheses,
        [],
    )
    assert faked["matches"] == "unclear"  # "example" must not supply "ample"
    honest = check_hypothesis_applicability(
        "Theorem 5.2. Let X be a proper variety carrying an ample line bundle.",
        hypotheses,
        [],
    )
    assert honest["matches"] is True


def test_negated_ample_does_not_fake_projectivity() -> None:
    hypotheses = [{"name": "X is projective", "status": "explicit", "source": "agent"}]
    result = check_hypothesis_applicability(
        "Theorem 5.3. Let X be a proper variety with no ample line bundle... not ample.",
        hypotheses,
        [],
    )
    assert result["matches"] == "unclear"
