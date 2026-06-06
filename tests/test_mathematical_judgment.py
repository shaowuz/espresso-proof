"""Mathematical Judgment Layer V1 (Six-Hour goal, Phase 1).

The layer records conservative mathematical RISK judgments. It must NEVER upgrade
proof/source/evidence status: it can only warn, request more evidence, downgrade
confidence, or ask for human review. These tests pin that boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.mathematical_judgment import build_mathematical_judgment, judge_run
from espresso_proof.memo import build_concise_memo
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.models import MathematicalJudgmentReport
from espresso_proof.validators import KernelValidationError, validate_run

FIXTURES = Path("tests/fixtures")

# Assertive proof-complete phrases. (A bare "proved" is allowed because artifacts
# legitimately echo claim/objection text such as "not proved" / "proof sketch".)
_FORBIDDEN_PROOF_LANGUAGE = ["proof-close", "proof complete", "we proved the", "this establishes the theorem"]


def _build(tmp_path: Path, fixture: str) -> Path:
    return run_mock_critique("mj", FIXTURES / fixture / "input.md", tmp_path)


def _artifact(run_dir: Path, key: str) -> dict:
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return read_yaml(run_dir / artifacts[key])


def _grade(run_dir: Path) -> dict:
    return _artifact(run_dir, "idea_grade")


def test_report_model_rejects_status_upgrade_fields() -> None:
    """A judgment report carrying source/evidence/proof status is rejected.

    StrictBaseModel forbids extras, so smuggling a status field fails to validate.
    """
    base = {
        "schema_version": 1,
        "claim_id": "claim_001",
        "run_id": "run_001",
        "generated_at": "2026-06-04T00:00:00+00:00",
        "findings": [],
        "notes": [],
    }
    # valid report is accepted
    MathematicalJudgmentReport.model_validate(base)
    for bad_field in ("source_backed", "computation_checked", "proof_language_status", "proof_status"):
        with pytest.raises(ValidationError):
            MathematicalJudgmentReport.model_validate({**base, bad_field: True})
    # a finding may not carry a status field either
    with pytest.raises(ValidationError):
        MathematicalJudgmentReport.model_validate(
            {
                **base,
                "findings": [
                    {
                        "kind": "theorem_condition_risk",
                        "tier": "warn",
                        "subject": "S1",
                        "signal": "x",
                        "recommended_action": "request_source",
                        "explanation": "x",
                        "source_backed": True,
                    }
                ],
            }
        )


def test_judgment_artifact_with_status_field_rejected_by_validate_run(tmp_path: Path) -> None:
    """Writing a judgment artifact with a smuggled status field fails validate_run."""
    run_dir = _build(tmp_path, "vague_claim_unclear")
    judge_run(run_dir)
    validate_run(run_dir)  # clean artifact validates
    judgment_path = run_dir / "mathematical_judgment.yaml"
    doc = read_yaml(judgment_path)
    doc["source_backed"] = True  # smuggle a status upgrade
    write_yaml(judgment_path, doc)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_unsupported_confidence_when_no_checked_or_source_backed(tmp_path: Path) -> None:
    """Route lacks source_backed + checked evidence -> downgrade, never upgrade.

    Mirrors goal negative test: 'route sounds strong but lacks checked evidence,
    so confidence stays conservative.'
    """
    run_dir = _build(tmp_path, "vague_claim_unclear")
    before = _grade(run_dir)
    report = build_mathematical_judgment(run_dir)
    findings = report["findings"]
    unsupported = [f for f in findings if f["kind"] == "unsupported_confidence"]
    assert unsupported, "expected an unsupported_confidence finding"
    assert unsupported[0]["recommended_action"] == "downgrade_confidence"
    # judging does not change the grade or any proof status
    judge_run(run_dir)
    validate_run(run_dir)
    after = _grade(run_dir)
    assert after["status"] == before["status"]
    assert after["proof_language_status"] == before["proof_language_status"]
    # and no step was promoted to source_backed by the judgment
    sg = _artifact(run_dir, "source_grounding")
    assert all(step.get("status") != "source_backed" for step in sg.get("steps", []))


def test_theorem_condition_and_missing_hypothesis_risk_keep_step_candidate(tmp_path: Path) -> None:
    """Plausible-but-unconfirmed theorem use + missing hypothesis in prose flag risk,
    but the step stays candidate (never source_backed) and proof status is untouched.

    Mirrors goal negative tests 'plausible but invalid theorem use' and
    'missing hypothesis hidden in prose'.
    """
    run_dir = _build(tmp_path, "source_misapplication_very_general_to_all")
    before = _grade(run_dir)
    report = build_mathematical_judgment(run_dir)
    kinds = {f["kind"] for f in report["findings"]}
    assert "theorem_condition_risk" in kinds
    assert "missing_hypothesis_in_prose" in kinds
    # theorem_condition_risk is at most a block_candidate recommendation
    tcr = [f for f in report["findings"] if f["kind"] == "theorem_condition_risk"]
    assert all(f["tier"] in {"warn", "block_candidate"} for f in tcr)
    # building/judging the run does not flip any step to source_backed
    judge_run(run_dir)
    validate_run(run_dir)
    sg = _artifact(run_dir, "source_grounding")
    assert all(step.get("status") != "source_backed" for step in sg.get("steps", []))
    after = _grade(run_dir)
    assert after["proof_language_status"] == before["proof_language_status"]


def test_reviewer_objection_is_not_refutation_and_no_proof_upgrade(tmp_path: Path) -> None:
    """A reviewer objection routes to repair/human; it is never a claim refutation
    and never upgrades proof status. Memo stays free of proof-complete language.

    Mirrors goal negative test 'reviewer says seems fine but no verifier checked
    proof, so no proof status upgrade'.
    """
    run_dir = _build(tmp_path, "promising_but_unproved")
    before = _grade(run_dir)
    report = build_mathematical_judgment(run_dir)
    kinds = {f["kind"] for f in report["findings"]}
    assert "reviewer_objection_decisiveness" in kinds
    # the layer has no 'refuted' / status vocabulary at all
    for f in report["findings"]:
        assert f["recommended_action"] in {
            "request_source",
            "request_example",
            "request_computation",
            "request_human_review",
            "downgrade_confidence",
            "none",
        }
    judge_run(run_dir)
    validate_run(run_dir)
    after = _grade(run_dir)
    assert after["status"] == before["status"]
    assert after["proof_language_status"] == before["proof_language_status"]
    memo = build_concise_memo(run_dir).lower()
    assert "mathematical judgment (advisory" in memo
    for phrase in _FORBIDDEN_PROOF_LANGUAGE:
        assert phrase not in memo


def test_judgment_claim_id_mismatch_rejected(tmp_path: Path) -> None:
    """A judgment whose claim_id does not match the locked claim is rejected."""
    run_dir = _build(tmp_path, "vague_claim_unclear")
    judge_run(run_dir)
    validate_run(run_dir)
    judgment_path = run_dir / "mathematical_judgment.yaml"
    doc = read_yaml(judgment_path)
    doc["claim_id"] = "some_other_claim"
    write_yaml(judgment_path, doc)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_judgment_run_id_mismatch_rejected(tmp_path: Path) -> None:
    """A judgment whose run_id does not match the manifest is rejected."""
    run_dir = _build(tmp_path, "vague_claim_unclear")
    judge_run(run_dir)
    validate_run(run_dir)
    judgment_path = run_dir / "mathematical_judgment.yaml"
    doc = read_yaml(judgment_path)
    doc["run_id"] = "not-this-run"
    write_yaml(judgment_path, doc)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_reviewer_approval_without_verifier_is_flagged_not_proof(tmp_path: Path) -> None:
    """Goal 07 detector: reviewers PASS but no source/checker support -> advisory
    flag (reviewer approval is not proof verification), never a status upgrade."""
    run_dir = _build(tmp_path, "vague_claim_unclear")
    # Force all reviewers to pass with no objection (positive reviewer language).
    review = _artifact(run_dir, "review")
    for r in review["reviewers"]:
        r["verdict"] = "pass"
        r["objections"] = []
    review["attempt_status"] = "passed"
    review["strongest_objection"] = "No blocking objection recorded."
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    write_yaml(run_dir / artifacts["review"], review)

    before = _grade(run_dir)
    report = build_mathematical_judgment(run_dir)
    kinds = {f["kind"] for f in report["findings"]}
    assert "reviewer_approval_without_verifier" in kinds
    # advisory only: action stays in the conservative set, no status vocabulary.
    finding = next(f for f in report["findings"] if f["kind"] == "reviewer_approval_without_verifier")
    assert finding["recommended_action"] == "request_human_review"
    assert finding["tier"] in {"info", "warn", "block_candidate"}

    judge_run(run_dir)
    validate_run(run_dir)
    after = _grade(run_dir)
    assert after["status"] == before["status"]
    assert after["proof_language_status"] == before["proof_language_status"]
    # the run earned nothing: no source_backed, no checked computation.
    sg = _artifact(run_dir, "source_grounding")
    assert all(s.get("status") != "source_backed" for s in sg.get("steps", []))
