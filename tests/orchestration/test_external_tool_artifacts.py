"""Goal 25 (slice 1): external tool candidate/run artifacts.

External-tool discovery + run records are RAW, audited evidence. They carry no
source/evidence/proof/grade status field (StrictBaseModel forbids extras), so a
tool can never write a trusted status by construction; only the kernel-gated
proof-verification bridge may later earn a SCOPED status from a confirming run.
Paths are confined to the run directory; failures/timeouts are fail-closed; the
memo renders them as scoped raw evidence, never a proof.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.kernel.models import ExternalToolRun, ExternalToolRunsReport
from espresso_proof.kernel.proof_language import contains_proof_language
from espresso_proof.kernel.validators import KernelValidationError, validate_run

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path) -> tuple[Path, str, str]:
    run = run_mock_critique("g25", FIXTURES / "promising_but_unproved" / "input.md", tmp_path)
    manifest = read_yaml(run / "run_manifest.yaml")
    claim_id = read_yaml(run / manifest["artifacts"]["locked_claim"])["id"]
    return run, manifest["run_id"], claim_id


def _attach_runs(run: Path, runs: list[dict]) -> None:
    manifest = read_yaml(run / "run_manifest.yaml")
    doc = {
        "schema_version": 1,
        "claim_id": read_yaml(run / manifest["artifacts"]["locked_claim"])["id"],
        "run_id": manifest["run_id"],
        "generated_at": "2026-06-10T00:00:00+00:00",
        "runs": runs,
        "notes": [],
    }
    write_yaml(run / "external_tool_runs.yaml", doc)
    manifest.setdefault("optional_artifacts", {})["external_tool_runs"] = "external_tool_runs.yaml"
    write_yaml(run / "run_manifest.yaml", manifest)


def _base_run(**over) -> dict:
    data = {"id": "r1", "tool_name": "fake-cas", "scope_note": "scoped finite check",
            "working_dir": ".", "input_files": ["in.txt"], "output_files": ["out.txt"],
            "conclusion": "checked the finite case", "statement_checked": "f(2)=2"}
    data.update(over)
    return data


# --- status-free by construction ------------------------------------------
def test_external_tool_models_have_no_status_field() -> None:
    from espresso_proof.kernel.models import ExternalToolCandidate
    forbidden = {"source_backed", "proof_language_status", "earned_status",
                 "refutes_claim", "idea_status", "proof_status"}
    assert not (set(ExternalToolRun.model_fields) & forbidden)
    assert not (set(ExternalToolCandidate.model_fields) & forbidden)
    # a smuggled status field is rejected (extra=forbid)
    with pytest.raises(ValidationError):
        ExternalToolRun(id="x", tool_name="t", scope_note="s", earned_status="computation_checked")
    # not_universal_proof is pinned True
    with pytest.raises(ValidationError):
        ExternalToolRun(id="x", tool_name="t", scope_note="s", not_universal_proof=False)


# --- claim binding + path confinement -------------------------------------
def test_valid_relative_path_run_validates(tmp_path: Path) -> None:
    run, _, _ = _run(tmp_path)
    _attach_runs(run, [_base_run()])
    validate_run(run)  # raw evidence with relative paths is accepted


def test_absolute_path_rejected(tmp_path: Path) -> None:
    run, _, _ = _run(tmp_path)
    _attach_runs(run, [_base_run(working_dir="/etc")])
    with pytest.raises(KernelValidationError, match="must be relative"):
        validate_run(run)


def test_parent_escape_path_rejected(tmp_path: Path) -> None:
    run, _, _ = _run(tmp_path)
    _attach_runs(run, [_base_run(output_files=["../escape.txt"])])
    with pytest.raises(KernelValidationError, match="cannot contain"):
        validate_run(run)


def test_claim_id_mismatch_rejected(tmp_path: Path) -> None:
    run, _, _ = _run(tmp_path)
    _attach_runs(run, [_base_run()])
    doc = read_yaml(run / "external_tool_runs.yaml")
    doc["claim_id"] = "claim_wrong"
    write_yaml(run / "external_tool_runs.yaml", doc)
    with pytest.raises(KernelValidationError, match="claim_id does not match"):
        validate_run(run)


# --- does not upgrade status; memo renders scoped, no proof language -------
def test_external_tool_run_does_not_upgrade_grade_and_renders_scoped(tmp_path: Path) -> None:
    run, _, _ = _run(tmp_path)
    grade_before = read_yaml(run / read_yaml(run / "run_manifest.yaml")["artifacts"]["idea_grade"])
    # Goal 38 tightened the gate: an interpretive conclusion carrying proof
    # prose is now REJECTED at parse time (see the gate tests below), so the
    # smuggling vector moved to the RAW stdout excerpt, which stays auditable
    # and must still be sanitized in display.
    _attach_runs(
        run,
        [_base_run(stdout_excerpt="RESULT: ok\nWe proved the theorem. proof-close.")],
    )
    validate_run(run)
    grade_after = read_yaml(run / read_yaml(run / "run_manifest.yaml")["artifacts"]["idea_grade"])
    assert grade_after == grade_before  # no status upgrade from a tool run
    memo = build_concise_memo(run)
    assert "External tool runs" in memo
    assert "scoped, not a proof" in memo
    assert contains_proof_language(memo) is None  # smuggled "proof-close" is sanitized in display


def test_failed_or_timed_out_run_is_fail_closed_in_display(tmp_path: Path) -> None:
    run, _, _ = _run(tmp_path)
    _attach_runs(run, [_base_run(id="rT", exit_code=1, timed_out=True)])
    validate_run(run)
    memo = build_concise_memo(run)
    assert "did not complete cleanly" in memo
    assert contains_proof_language(memo) is None


# --- Goal 38: proof-complete language gate on interpretive fields ------------
def test_tool_run_model_rejects_proof_complete_interpretation() -> None:
    import pytest as _pytest

    from espresso_proof.kernel.models import ExternalToolRun

    base = {
        "id": "t1",
        "tool_name": "fake_deterministic_check",
        "scope_note": "scoped check only",
    }
    for field, text in (
        ("ai_interpretation", "This shows we proved the theorem. proof-close."),
        ("conclusion", "proof-close — the proof is complete."),
        ("statement_checked", "the theorem is settled: 证毕"),
        ("scope_note", "no gaps remain"),
    ):
        payload = {**base, field: text} if field != "scope_note" else {**base, "scope_note": text}
        with _pytest.raises(ValueError, match="proof-complete"):
            ExternalToolRun.model_validate(payload)


def test_tool_run_raw_stdout_may_carry_overclaim_but_display_scrubs() -> None:
    # stdout_excerpt is RAW audited evidence: it may contain the tool's own
    # overclaim text; the artifact stays valid and the display layer scrubs.
    from espresso_proof.kernel.models import ExternalToolRun
    from espresso_proof.kernel.proof_language import scrub_proof_language

    run = ExternalToolRun.model_validate(
        {
            "id": "t2",
            "tool_name": "fake_deterministic_check",
            "stdout_excerpt": "RESULT: ok\nproof-close, we proved the theorem!",
            "scope_note": "scoped check only",
        }
    )
    assert "proof-close" in run.stdout_excerpt  # raw evidence preserved
    assert "proof-close" not in scrub_proof_language(run.stdout_excerpt)
