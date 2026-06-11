"""Goal 25 (slice 2): fake sandbox executor.

A minimal, safe executor runs ONLY allowlisted deterministic fake tools, records
an audited run, and fails closed on timeout / nonzero exit / malformed output. It
passes a minimal environment (no secrets), confines cwd to the run dir, and never
earns a status (trusted=False); persisted records still validate under slice 1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.external_tool_runner import (
    UnknownToolError,
    append_external_tool_run,
    run_and_record_external_tool,
    run_external_tool,
)
from espresso_proof.io import read_yaml
from espresso_proof.memo import build_concise_memo
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.proof_language import contains_proof_language
from espresso_proof.validators import validate_run

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path) -> Path:
    return run_mock_critique("g25b", FIXTURES / "promising_but_unproved" / "input.md", tmp_path)


def _clean(rec) -> bool:
    return not (rec.timed_out or (rec.exit_code not in (0, None)))


# --- happy path: records only, earns nothing ------------------------------
def test_successful_fake_tool_records_scoped_evidence_only(tmp_path: Path) -> None:
    run = _run(tmp_path)
    pv_before = (run / "proof_verification.yaml").exists()
    grade_before = read_yaml(run / read_yaml(run / "run_manifest.yaml")["artifacts"]["idea_grade"])
    rec = run_and_record_external_tool(run, "fake_deterministic_check", entry_id="ok1",
                                       statement_checked="f(2)=2")
    assert _clean(rec) and rec.parsed_result == "ok deterministic-check"
    assert rec.trusted is False and rec.not_universal_proof is True
    # earns NOTHING: no proof_verification created, grade unchanged
    assert (run / "proof_verification.yaml").exists() == pv_before
    grade_after = read_yaml(run / read_yaml(run / "run_manifest.yaml")["artifacts"]["idea_grade"])
    assert grade_after == grade_before
    validate_run(run)  # persisted record validates (slice 1 gates)


# --- fail-closed: malformed / nonzero / timeout ---------------------------
def test_malformed_output_fails_closed(tmp_path: Path) -> None:
    rec = run_external_tool(_run(tmp_path), "fake_malformed_output", entry_id="m1")
    assert rec.exit_code == 0  # process succeeded...
    assert rec.parsed_result == "" and rec.conclusion == ""  # ...but output unparseable -> no evidence


def test_nonzero_exit_fails_closed(tmp_path: Path) -> None:
    rec = run_external_tool(_run(tmp_path), "fake_nonzero_exit", entry_id="n1")
    assert rec.exit_code not in (0, None)
    assert rec.parsed_result == "" and rec.conclusion == ""


def test_timeout_fails_closed(tmp_path: Path) -> None:
    rec = run_external_tool(_run(tmp_path), "fake_timeout", entry_id="t1", timeout_seconds=1.0)
    assert rec.timed_out is True
    assert rec.parsed_result == "" and rec.conclusion == ""


# --- only allowlisted tools run -------------------------------------------
def test_unknown_tool_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UnknownToolError, match="not allowlisted"):
        run_external_tool(_run(tmp_path), "github.com/evil/tool", entry_id="x")


# --- minimal env: no secrets handed to the tool ---------------------------
def test_executor_passes_minimal_env_without_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ESPRESSO_SECRET_TOKEN", "supersecret")
    captured: dict = {}
    import espresso_proof.external_tool_runner as runner

    real_run = runner.subprocess.run

    def spy(argv, **kwargs):
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        captured["timeout"] = kwargs.get("timeout")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(runner.subprocess, "run", spy)
    run = _run(tmp_path)
    run_external_tool(run, "fake_deterministic_check", entry_id="env1")
    env = captured["env"]
    assert "ESPRESSO_SECRET_TOKEN" not in env  # secret not handed to the tool
    assert set(env).issubset({"PATH", "LC_ALL"})  # only a minimal env
    assert captured["cwd"] == str(run)  # cwd confined to the run dir
    assert captured["timeout"] is not None  # a hard timeout is always set


# --- persistence: dedup + display -----------------------------------------
def test_duplicate_entry_id_rejected(tmp_path: Path) -> None:
    run = _run(tmp_path)
    run_and_record_external_tool(run, "fake_deterministic_check", entry_id="dup")
    with pytest.raises(ValueError, match="already exists"):
        run_and_record_external_tool(run, "fake_deterministic_check", entry_id="dup")


def test_failed_run_renders_fail_closed_no_proof_language(tmp_path: Path) -> None:
    run = _run(tmp_path)
    run_and_record_external_tool(run, "fake_nonzero_exit", entry_id="f1")
    memo = build_concise_memo(run)
    assert "External tool runs" in memo
    assert "fail-closed" in memo
    assert contains_proof_language(memo) is None


def test_overclaiming_tool_output_is_scrubbed_from_conclusion(tmp_path: Path, monkeypatch) -> None:
    # Goal 38: a tool that PRINTS proof-complete language still gets recorded
    # (raw stdout preserved as audited evidence), but the interpretive
    # conclusion is scrubbed so the artifact passes the model's proof-language
    # gate instead of crashing or persisting an overclaim.
    import sys as _sys

    from espresso_proof import external_tool_runner as runner_mod

    overclaim_script = 'print("RESULT: we proved the theorem. proof-close.")'
    monkeypatch.setitem(
        runner_mod.FAKE_TOOLS,
        "fake_overclaim",
        {
            "argv": [_sys.executable, "-I", "-c", overclaim_script],
            "parse": runner_mod.FAKE_TOOLS["fake_deterministic_check"]["parse"],
            "scope_note": "Deterministic fake tool printing an overclaim (scrub demo).",
        },
    )
    run_dir = _run(tmp_path)
    record = run_and_record_external_tool(run_dir, "fake_overclaim", entry_id="overclaim_001")
    assert "proof-close" in record.stdout_excerpt  # raw evidence preserved
    assert "proof-close" not in record.conclusion.lower()
    assert "proved the theorem" not in record.conclusion.lower()
    assert "[withheld proof-like phrase]" in record.conclusion
    validate_run(run_dir)
