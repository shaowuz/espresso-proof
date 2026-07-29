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


def test_legacy_refs_without_hash_still_validate(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    for ref in sg["source_refs"]:
        ref.pop("statement_sha256", None)
    write_yaml(run_dir / "source_grounding.yaml", sg)
    # additive field: pre-Goal-41 artifacts keep validating — but the audit
    # binding now notices the grounding bytes changed, so refresh the audit.
    from espresso_proof.verification.theorem_applicability import audit_theorem_applicability

    audit_theorem_applicability(run_dir)
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
    from espresso_proof.verification.theorem_applicability import audit_theorem_applicability

    audit_theorem_applicability(run_dir)
    validate_run(run_dir)


# --- 3. Chinese parse-time gate ----------------------------------------------
def test_parse_time_gate_rejects_chinese_completion_markers() -> None:
    from espresso_proof.kernel.validators import _first_forbidden_proof_language

    for text in ("至此定理得证。", "证毕", "我们证明了该定理", "命题已证明"):
        assert _first_forbidden_proof_language(text.lower()) is not None, text
    for text in ("该引理已证明", "我们证明了引理2.1", "尚未证明该定理"):
        assert _first_forbidden_proof_language(text.lower()) is None, text


def test_final_report_with_chinese_overclaim_fails_validation(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "g41zh", FIXTURES / "promising_but_unproved" / "input.md", tmp_path
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    final_report = run_dir / manifest["artifacts"]["final_report"]
    final_report.write_text(
        final_report.read_text(encoding="utf-8") + "\n结论:定理得证,证毕。\n",
        encoding="utf-8",
    )
    with pytest.raises(KernelValidationError, match="forbidden proof language"):
        validate_run(run_dir)


def test_ingestion_redacts_chinese_completion_markers() -> None:
    import re

    from espresso_proof.agents.llm_agents import FORBIDDEN_LANGUAGE, forbidden_pattern

    text = "评审认为我们证明了该定理,证毕。"
    for phrase in FORBIDDEN_LANGUAGE:
        text = re.sub(forbidden_pattern(phrase), "[withheld proof-like phrase]", text)
    assert "证毕" not in text
    assert "我们证明了该定理" not in text
    assert "[withheld proof-like phrase]" in text


def test_forbidden_language_superset_of_validator_gate() -> None:
    # invariant: every phrase the parse-time gate rejects is also redacted at
    # agent ingestion (Chinese entries must keep this in lockstep).
    from espresso_proof.agents.llm_agents import FORBIDDEN_LANGUAGE

    for phrase in ("证毕", "证明完毕", "证明完成", "定理得证", "命题得证",
                   "猜想得证", "定理已证明", "命题已证明",
                   "我们证明了该定理", "我们证明了这个定理"):
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
