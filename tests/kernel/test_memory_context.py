"""Active, advisory memory influence.

build_memory_context matches prior project memory to the current claim and
records advisory recommended uses. It must never upgrade a trusted status,
override current artifacts, or write outside the run; influence is deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.memory.memory import write_memory_entry
from espresso_proof.memory.memory_context import build_memory_context
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.models import MemoryContext
from espresso_proof.kernel.validators import KernelValidationError, validate_run

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path) -> Path:
    return run_mock_critique("mem", FIXTURES / "vague_claim_unclear" / "input.md", tmp_path)


def _claim_terms(run_dir: Path) -> str:
    claim = read_yaml(run_dir / "locked_claim.yaml")
    return str(claim.get("normalized_statement", ""))


def _write_mem(run_dir: Path, *, mid: str, mtype: str, summary: str) -> None:
    claim = read_yaml(run_dir / "locked_claim.yaml")
    write_memory_entry(run_dir.parents[1], {
        "id": mid, "timestamp": "t", "project": "mem", "type": mtype,
        "claim_id": claim["id"],
        "summary": summary,
        "provenance": [f"projects/mem/runs/{run_dir.name}/idea_grade.yaml"],
        "reusable_lesson": "lesson",
    })


def test_matches_prior_failed_attempt_with_avoid(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_mem(run_dir, mid="m1", mtype="failed_attempt",
               summary=f"Route via {_claim_terms(run_dir)[:50]} failed: missing hypothesis.")
    ctx = build_memory_context(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / "locked_claim.yaml")
    assert ctx["claim_id"] == claim["id"]
    assert ctx["run_id"] == manifest["run_id"]
    assert ctx["advisory_only"] is True
    avoid = [m for m in ctx["matched_entries"] if m["id"] == "m1"]
    assert avoid and avoid[0]["type"] == "failed_attempt"
    assert avoid[0]["recommended_use"] == "avoid"
    validate_run(run_dir)


def test_source_gap_memory_recommends_request_source_not_support(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_mem(run_dir, mid="sg", mtype="source_gap",
               summary=f"No reference found for {_claim_terms(run_dir)[:50]}.")
    ctx = build_memory_context(run_dir)
    sg = next(m for m in ctx["matched_entries"] if m["id"] == "sg")
    assert sg["recommended_use"] == "request_source"
    # remembered source gap does NOT become source support.
    sgrd = read_yaml(run_dir / "source_grounding.yaml")
    assert all(s.get("status") != "source_backed" for s in sgrd.get("steps", []))


def test_memory_context_is_advisory_no_status_change(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_mem(run_dir, mid="m1", mtype="user_decision",
               summary=f"Human preferred route for {_claim_terms(run_dir)[:50]}.")
    grade_before = read_yaml(run_dir / "idea_grade.yaml")
    sg_before = read_yaml(run_dir / "source_grounding.yaml")
    build_memory_context(run_dir)
    validate_run(run_dir)
    assert read_yaml(run_dir / "idea_grade.yaml")["status"] == grade_before["status"]
    assert read_yaml(run_dir / "idea_grade.yaml")["proof_language_status"] == grade_before["proof_language_status"]
    assert [s.get("status") for s in read_yaml(run_dir / "source_grounding.yaml").get("steps", [])] == \
        [s.get("status") for s in sg_before.get("steps", [])]


def test_memory_context_is_deterministic(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_mem(run_dir, mid="m1", mtype="failed_attempt",
               summary=f"Route via {_claim_terms(run_dir)[:50]} failed.")
    a = build_memory_context(run_dir)["matched_entries"]
    b = build_memory_context(run_dir)["matched_entries"]
    assert a == b  # ranking + mapping deterministic for identical inputs


def test_only_relevant_memory_is_surfaced(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_mem(run_dir, mid="unrel", mtype="failed_attempt",
               summary="Zzqx wibble frobnitz unrelated lexicon tokens only.")
    ctx = build_memory_context(run_dir)
    assert all(m["id"] != "unrel" for m in ctx["matched_entries"])


def test_build_does_not_write_outside_run(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_mem(run_dir, mid="m1", mtype="counterexample",
               summary=f"Counterexample for {_claim_terms(run_dir)[:50]}.")
    index_path = run_dir.parents[1] / "memory" / "index.yaml"
    before = index_path.read_text(encoding="utf-8")
    build_memory_context(run_dir)
    assert index_path.read_text(encoding="utf-8") == before  # project memory untouched


def test_model_rejects_status_field() -> None:
    with pytest.raises(Exception):
        MemoryContext.model_validate({
            "claim_id": "c",
            "run_id": "r",
            "advisory_only": True,
            "matched_entries": [{"id": "x", "type": "failed_attempt", "relevance_reason": "r",
                                 "recommended_use": "avoid", "summary": "s", "source_backed": True}],
        })


def test_hand_edited_status_in_memory_context_rejected_by_validate_run(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_mem(run_dir, mid="m1", mtype="failed_attempt",
               summary=f"Route via {_claim_terms(run_dir)[:50]} failed.")
    build_memory_context(run_dir)
    mc = read_yaml(run_dir / "memory_context.yaml")
    mc["matched_entries"][0]["source_backed"] = True  # smuggle a trusted field
    write_yaml(run_dir / "memory_context.yaml", mc)
    with pytest.raises(KernelValidationError):
        validate_run(run_dir)


def test_hand_edited_memory_context_run_binding_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_mem(run_dir, mid="m1", mtype="failed_attempt",
               summary=f"Route via {_claim_terms(run_dir)[:50]} failed.")
    build_memory_context(run_dir)
    mc = read_yaml(run_dir / "memory_context.yaml")
    mc["run_id"] = "other_run"
    write_yaml(run_dir / "memory_context.yaml", mc)
    with pytest.raises(KernelValidationError, match="memory_context run_id mismatch"):
        validate_run(run_dir)


def test_memory_context_cli(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_mem(run_dir, mid="m1", mtype="failed_attempt",
               summary=f"Route via {_claim_terms(run_dir)[:50]} failed.")
    res = CliRunner().invoke(app, ["memory-context", "--run", str(run_dir)])
    assert res.exit_code == 0, res.output
    assert "Matched" in res.output
    assert (run_dir / "memory_context.yaml").exists()
    validate_run(run_dir)
