from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.models import (
    LiteratureReviewReport,
    SourceRef,
    TheoremApplicabilityUsage,
    LiteratureReviewAgentReport,
)
from espresso_proof.literature.pdf_ingestion import ingest_source_for_theorems
from espresso_proof.verification.source_verification import ground_run
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")
THM = (
    "Theorem 1.2. Exact literature statement supporting the spectral step "
    "over base field C for square matrices."
)


def _claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)


def _grounded_run(tmp_path: Path) -> Path:
    run_dir = run_mock_critique(
        "v2_source_theorem",
        FIXTURES / "all_matrices_diagonalizable" / "input.md",
        tmp_path,
    )
    _claim_clear(run_dir)

    src = tmp_path / "source.txt"
    src.write_text(THM, encoding="utf-8")
    claim = read_yaml(run_dir / "locked_claim.yaml")
    report = ingest_source_for_theorems(
        claim_id=claim["id"],
        source=str(src),
        source_id="v2_src",
        title="V2 source",
        authors=["A. Author"],
        source_type="paper",
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = (
        "theorem_extraction.yaml"
    )
    write_yaml(run_dir / "theorem_extraction.yaml", report)
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    ground_run(run_dir=run_dir, theorem_extraction_path=run_dir / "theorem_extraction.yaml")
    validate_run(run_dir)
    return run_dir


def _source_backed_usage_index(run_dir: Path) -> int:
    theorem = read_yaml(run_dir / "theorem_applicability.yaml")
    for index, usage in enumerate(theorem["usages"]):
        if usage["source_status"] == "source_backed":
            return index
    raise AssertionError("test fixture did not produce a source_backed theorem usage")


def _source_backed_step_id(run_dir: Path) -> str:
    grounding = read_yaml(run_dir / "source_grounding.yaml")
    for step in grounding["steps"]:
        if step["status"] == "source_backed":
            return step["proof_step_id"]
    raise AssertionError("test fixture did not produce a source_backed proof step")


def _valid_usage_payload() -> dict:
    return {
        "proof_step_id": "step_001",
        "source_ref_id": "src_001",
        "theorem_id": "thm_001",
        "theorem_label": "Theorem 1.2",
        "theorem_statement": THM,
        "locator": {"kind": "theorem", "value": "Theorem 1.2"},
        "source_status": "source_backed",
        "conclusion_status": "compatible",
        "conditions": [
            {
                "id": "cond_001",
                "text": "base field C",
                "condition_type": "base_field",
                "status": "satisfied",
                "evidence": "explicit in the cited theorem",
                "definition_locator": None,
            }
        ],
        "applicability_status": "applicable",
        "blocks_source_backed": False,
        "claim_scope": "restricted",
        "theorem_scope": "restricted",
        "scope_justification": "",
        "notes": [],
    }


def _literature_review_agent_payload() -> dict:
    return {
        "schema_version": 1,
        "claim_id": "claim_001",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "input_sources": [
            {
                "source_ref": {
                    "id": "src_001",
                    "type": "paper",
                    "title": "Paper",
                    "authors": ["A. Author"],
                    "locator": {"kind": "theorem", "value": "Theorem 1.2"},
                    "statement_or_summary": THM,
                    "hypothesis_match": {"matches": "unclear", "mismatches": []},
                    "confidence": "medium",
                    "status": "candidate",
                },
                "access": {
                    "status": "full_text_read",
                    "access_path": None,
                    "pages_or_sections_read": ["Theorem 1.2"],
                    "extraction_method": "plain_text",
                },
                "bibliographic_confidence": "high",
            }
        ],
        "extracted_theorems": [
            {
                "id": "thm_001",
                "source_ref_id": "src_001",
                "kind": "theorem",
                "label": "Theorem 1.2",
                "locator": {"kind": "theorem", "value": "Theorem 1.2"},
                "statement_exact": THM,
                "statement_is_verbatim": True,
                "statement_span": {
                    "page": None,
                    "section": "Theorem 1.2",
                    "char_start": None,
                    "char_end": None,
                },
                "theorem_hypotheses": [],
                "theorem_conclusion": "Supports a local spectral step.",
                "extraction_confidence": "high",
            }
        ],
        "theorem_comparisons": [
            {
                "theorem_id": "thm_001",
                "claim_id": "claim_001",
                "comparison_status": "unclear",
                "hypothesis_matches": [
                    {
                        "claim_hypothesis": "base field C",
                        "theorem_hypothesis_id": None,
                        "relation": "unclear",
                        "reason": "candidate relation only",
                        "support_locator": None,
                    }
                ],
                "conclusion_match": {
                    "relation": "unclear",
                    "reason": "candidate conclusion relation only",
                },
                "relevance": {
                    "role": "key_theorem",
                    "summary": "candidate source for a local proof step",
                },
                "uncertainty": {
                    "confidence": "medium",
                    "failure_modes": ["weak_term_match"],
                    "explanation": "candidate-only comparison",
                },
            }
        ],
        "relevance_summary": {
            "overall_status": "unclear",
            "strongest_sources": [
                {
                    "source_ref_id": "src_001",
                    "theorem_id": "thm_001",
                    "reason": "strong candidate, not source support",
                }
            ],
            "main_blockers": ["exact applicability not checked"],
        },
        "uncertainty": {
            "overall_confidence": "medium",
            "blocking_uncertainties": ["hypotheses not matched"],
            "needs_human_review": [],
        },
        "notes": ["candidate-only literature review agent output"],
    }


def test_literature_candidate_cannot_set_source_backed() -> None:
    with pytest.raises(ValidationError, match="Literature Review may only propose candidates"):
        LiteratureReviewReport.model_validate(
            {
                "claim_id": "claim_001",
                "query_summary": "candidate sources only",
                "candidate_references": [
                    {
                        "id": "lit_001",
                        "title": "Paper",
                        "authors": ["A. Author"],
                        "locator": {"kind": "theorem", "value": "Theorem 1.2"},
                        "relevance": "possible_theorem_source",
                        "status": "source_backed",
                        "why_relevant": "candidate only",
                        "found_by": "literature_review",
                    }
                ],
            }
        )


def test_literature_review_cannot_emit_literature_implies() -> None:
    with pytest.raises(ValidationError, match="Literature Review cannot emit literature_implies"):
        LiteratureReviewReport.model_validate(
            {
                "claim_id": "claim_001",
                "query_summary": "literature_implies is not candidate-only language",
                "candidate_references": [],
            }
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["notes"].append("literature_implies"),
        lambda payload: payload["relevance_summary"]["strongest_sources"][0].update(
            {"reason": "literature_implies from strongest source"}
        ),
        lambda payload: payload["relevance_summary"]["main_blockers"].append(
            "literature_implies blocker"
        ),
        lambda payload: payload["theorem_comparisons"][0]["hypothesis_matches"][0].update(
            {"reason": "literature_implies in hypothesis comparison"}
        ),
        lambda payload: payload["theorem_comparisons"][0]["relevance"].update(
            {"summary": "literature_implies in relevance summary"}
        ),
    ],
)
def test_literature_review_agent_cannot_emit_literature_implies(mutate) -> None:
    payload = deepcopy(_literature_review_agent_payload())
    mutate(payload)

    with pytest.raises(ValidationError, match="Literature Review Agent cannot emit"):
        LiteratureReviewAgentReport.model_validate(payload)


def test_validate_run_rejects_literature_review_agent_literature_implies(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    payload = _literature_review_agent_payload()
    payload["claim_id"] = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])["id"]
    payload["notes"].append("literature_implies from whole-route literature summary")
    manifest.setdefault("optional_artifacts", {})["literature_review_agent"] = (
        "literature_review_agent.yaml"
    )
    write_yaml(run_dir / "literature_review_agent.yaml", payload)
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    with pytest.raises(KernelValidationError, match="schema validation failed"):
        validate_run(run_dir)


def test_source_candidate_without_exact_locator_is_not_support() -> None:
    with pytest.raises(ValidationError, match="source_backed requires an exact locator"):
        SourceRef.model_validate(
            {
                "id": "src_001",
                "type": "paper",
                "title": "Paper",
                "authors": ["A. Author"],
                "locator": {"kind": "unknown", "value": ""},
                "statement_or_summary": THM,
                "hypothesis_match": {"matches": True, "mismatches": []},
                "confidence": "high",
                "status": "source_backed",
            }
        )


def test_source_backed_without_hypothesis_match_is_rejected() -> None:
    with pytest.raises(ValidationError, match="source_backed requires matching hypotheses"):
        SourceRef.model_validate(
            {
                "id": "src_001",
                "type": "paper",
                "title": "Paper",
                "authors": ["A. Author"],
                "locator": {"kind": "theorem", "value": "Theorem 1.2"},
                "statement_or_summary": THM,
                "hypothesis_match": {
                    "matches": False,
                    "mismatches": ["the source only handles generic square matrices"],
                },
                "confidence": "high",
                "status": "source_backed",
            }
        )


def test_source_ref_statement_step_binding_mismatch_is_rejected(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    grounding = read_yaml(run_dir / "source_grounding.yaml")
    grounding["source_refs"].append(
        {
            **grounding["source_refs"][0],
            "id": "src_not_used_by_step",
        }
    )
    theorem = read_yaml(run_dir / "theorem_applicability.yaml")
    theorem["usages"][_source_backed_usage_index(run_dir)]["source_ref_id"] = (
        "src_not_used_by_step"
    )
    write_yaml(run_dir / "source_grounding.yaml", grounding)
    theorem["source_grounding_sha256"] = hashlib.sha256(
        (run_dir / "source_grounding.yaml").read_bytes()
    ).hexdigest()
    write_yaml(run_dir / "theorem_applicability.yaml", theorem)

    with pytest.raises(KernelValidationError, match="source_ref_id is not used by proof step"):
        validate_run(run_dir)


def test_theorem_applicability_statement_mismatch_is_rejected(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    theorem = read_yaml(run_dir / "theorem_applicability.yaml")
    theorem["usages"][_source_backed_usage_index(run_dir)]["theorem_statement"] = (
        "Fabricated stronger theorem statement proving a different result."
    )
    write_yaml(run_dir / "theorem_applicability.yaml", theorem)

    with pytest.raises(KernelValidationError, match="theorem_statement does not match"):
        validate_run(run_dir)


def test_theorem_applicability_locator_mismatch_is_rejected(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    theorem = read_yaml(run_dir / "theorem_applicability.yaml")
    theorem["usages"][_source_backed_usage_index(run_dir)]["locator"] = {
        "kind": "theorem",
        "value": "Theorem 999.999",
    }
    write_yaml(run_dir / "theorem_applicability.yaml", theorem)

    with pytest.raises(KernelValidationError, match="locator does not match"):
        validate_run(run_dir)


def test_source_backed_usage_missing_source_ref_is_rejected(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    theorem = read_yaml(run_dir / "theorem_applicability.yaml")
    usage = theorem["usages"][_source_backed_usage_index(run_dir)]
    usage["source_ref_id"] = "missing_source_ref"
    usage["theorem_statement"] = "Fabricated stronger theorem statement."
    usage["locator"] = {"kind": "theorem", "value": "Theorem 999.999"}
    write_yaml(run_dir / "theorem_applicability.yaml", theorem)

    with pytest.raises(KernelValidationError, match="concrete cited source_ref_id"):
        validate_run(run_dir)


def test_definition_gap_blocks_support(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    theorem = read_yaml(run_dir / "theorem_applicability.yaml")
    usage = theorem["usages"][_source_backed_usage_index(run_dir)]
    usage["conditions"][0] = {
        "id": "cond_definition_gap",
        "text": "definition of the object class agrees with the claim",
        "condition_type": "definition",
        "status": "missing",
        "evidence": "definition not located",
        "definition_locator": None,
    }
    usage["applicability_status"] = "blocked"
    usage["blocks_source_backed"] = True
    theorem["overall_status"] = "blocked"
    write_yaml(run_dir / "theorem_applicability.yaml", theorem)

    with pytest.raises(KernelValidationError, match="blocked by theorem applicability"):
        validate_run(run_dir)


def test_empty_theorem_condition_ledger_is_blocked_or_unclear() -> None:
    payload = _valid_usage_payload()
    payload["conditions"] = []

    with pytest.raises(ValidationError, match="non-empty condition coverage"):
        TheoremApplicabilityUsage.model_validate(payload)


def test_source_matching_alone_cannot_create_theorem_applicability(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest["optional_artifacts"].pop("theorem_applicability", None)
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    (run_dir / "theorem_applicability.yaml").unlink()

    with pytest.raises(KernelValidationError, match="source_backed theorem steps require"):
        validate_run(run_dir)


def test_very_general_theorem_used_for_all_objects_is_blocked_without_justification(
    tmp_path: Path,
) -> None:
    run_dir = _grounded_run(tmp_path)
    theorem = read_yaml(run_dir / "theorem_applicability.yaml")
    usage = theorem["usages"][_source_backed_usage_index(run_dir)]
    usage["claim_scope"] = "universal"
    usage["theorem_scope"] = "very_general"
    usage["scope_justification"] = ""
    write_yaml(run_dir / "theorem_applicability.yaml", theorem)

    with pytest.raises(KernelValidationError, match="cannot back a universal"):
        validate_run(run_dir)


def test_conclusion_drift_is_rejected(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    theorem = read_yaml(run_dir / "theorem_applicability.yaml")
    usage = theorem["usages"][_source_backed_usage_index(run_dir)]
    usage["conclusion_status"] = "orthogonal"
    usage["applicability_status"] = "unclear"
    usage["blocks_source_backed"] = True
    theorem["overall_status"] = "unclear"
    write_yaml(run_dir / "theorem_applicability.yaml", theorem)

    with pytest.raises(KernelValidationError, match="blocked by theorem applicability"):
        validate_run(run_dir)


def test_weaker_conclusion_cannot_be_applicable_for_source_backed_step(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    theorem = read_yaml(run_dir / "theorem_applicability.yaml")
    usage = theorem["usages"][_source_backed_usage_index(run_dir)]
    usage["conclusion_status"] = "weaker"
    usage["applicability_status"] = "applicable"
    usage["blocks_source_backed"] = False
    write_yaml(run_dir / "theorem_applicability.yaml", theorem)

    with pytest.raises(KernelValidationError, match="compatible conclusion"):
        validate_run(run_dir)


def test_supported_step_cannot_prove_route_final_status_or_grade_10(tmp_path: Path) -> None:
    run_dir = _grounded_run(tmp_path)
    assert _source_backed_step_id(run_dir)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    grade_path = run_dir / manifest["artifacts"]["idea_grade"]
    grade = read_yaml(grade_path)
    original_proof_language_status = grade["proof_language_status"]
    grade["score"] = 100
    grade["proof_language_status"] = "source_checked_argument"
    write_yaml(grade_path, grade)

    with pytest.raises(KernelValidationError, match="score=100"):
        validate_run(run_dir)

    grade["score"] = 80
    grade["proof_language_status"] = original_proof_language_status
    write_yaml(grade_path, grade)
    final_path = run_dir / manifest["artifacts"]["final_report"]
    final_path.write_text(
        final_path.read_text(encoding="utf-8") + "\nWe proved the theorem.\n",
        encoding="utf-8",
    )

    with pytest.raises(KernelValidationError, match="forbidden proof language"):
        validate_run(run_dir)


def test_source_checked_argument_requires_earned_source_grounding_step(
    tmp_path: Path,
) -> None:
    run_dir = run_mock_critique(
        "v2_source_theorem_no_grounding",
        FIXTURES / "all_matrices_diagonalizable" / "input.md",
        tmp_path,
    )
    _claim_clear(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    grade_path = run_dir / manifest["artifacts"]["idea_grade"]
    grade = read_yaml(grade_path)
    grade["proof_language_status"] = "source_checked_argument"
    write_yaml(grade_path, grade)

    with pytest.raises(KernelValidationError, match="source_checked_argument requires"):
        validate_run(run_dir)
