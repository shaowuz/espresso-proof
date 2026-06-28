from __future__ import annotations

import os
from pathlib import Path

import pytest

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.formal.lean_bridge import (
    append_lean_bridge_result,
    run_lean_bridge,
    scan_lean_source,
)
from espresso_proof.formal.statement_lock import build_statement_lock
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.validators import KernelValidationError, validate_run
from espresso_proof.orchestration.mock_pipeline import run_mock_critique

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path) -> Path:
    run_dir = run_mock_critique(
        "lean_bridge",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)
    return run_dir


def _claim_statement(run_dir: Path) -> str:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    return read_yaml(run_dir / manifest["artifacts"]["locked_claim"])[
        "normalized_statement"
    ]


def _lock(run_dir: Path, *, equivalent: bool = True) -> dict:
    statement = _claim_statement(run_dir)
    if not equivalent:
        statement += " with a changed conclusion"
    return build_statement_lock(
        run_dir,
        formal_statement=statement,
        explicit_equivalence_confirmed=True,
        equivalence_evidence="test fixture lock",
    )


def _lean_file(tmp_path: Path, text: str = "theorem t : True := by\n  trivial\n") -> Path:
    path = tmp_path / "Proof.lean"
    path.write_text(text, encoding="utf-8")
    return path


def _entry(run_dir: Path, entry_id: str = "lean_bridge") -> dict:
    pv = read_yaml(run_dir / "proof_verification.yaml")
    return next(e for e in pv["entries"] if e["id"] == entry_id)


def test_lean_bridge_disabled_by_default_and_appends_only_unverified(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    result = append_lean_bridge_result(
        run_dir,
        lean_file=_lean_file(tmp_path),
        statement_lock=_lock(run_dir),
        entry_id="lean_disabled",
    )
    validate_run(run_dir)

    assert result["status"] == "disabled"
    entry = _entry(run_dir, "lean_disabled")
    assert entry["verifier_type"] == "lean_bridge"
    assert entry["earned_status"] == "unverified"
    assert entry["trusted"] is False
    assert entry["confirmed"] is False


def test_lean_bridge_unavailable_fails_closed(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    result = append_lean_bridge_result(
        run_dir,
        lean_file=_lean_file(tmp_path),
        statement_lock=_lock(run_dir),
        enabled=True,
        lean_executable="definitely-not-a-real-lean-binary",
        entry_id="lean_unavailable",
    )
    validate_run(run_dir)
    assert result["status"] == "lean_unavailable"
    assert _entry(run_dir, "lean_unavailable")["earned_status"] == "unverified"


@pytest.mark.parametrize("token", ["sorry", "admit", "axiom"])
def test_lean_bridge_rejects_fake_proof_tokens(tmp_path: Path, token: str) -> None:
    run_dir = _run(tmp_path)
    lean = _lean_file(tmp_path, f"{token} bad : True\n")
    result = append_lean_bridge_result(
        run_dir,
        lean_file=lean,
        statement_lock=_lock(run_dir),
        enabled=True,
        lean_executable="definitely-not-a-real-lean-binary",
        entry_id=f"lean_{token}",
    )
    validate_run(run_dir)
    assert result["status"] == "unsafe_source_rejected"
    assert any(token in finding for finding in result["unsafe_findings"])
    assert _entry(run_dir, f"lean_{token}")["earned_status"] == "unverified"


def test_lean_bridge_rejects_import_shadowing(tmp_path: Path) -> None:
    source = "import .LocalMathlib\nimport Mathlib.Data.Nat.Basic\n"
    assert any("shadowing" in finding for finding in scan_lean_source(source))

    run_dir = _run(tmp_path)
    (tmp_path / "Mathlib.lean").write_text("axiom fake : True\n", encoding="utf-8")
    lean = _lean_file(tmp_path, "import Mathlib\n\ntheorem t : True := by\n  trivial\n")
    result = append_lean_bridge_result(
        run_dir,
        lean_file=lean,
        statement_lock=_lock(run_dir),
        enabled=True,
        lean_executable="definitely-not-a-real-lean-binary",
        entry_id="lean_shadow",
    )
    validate_run(run_dir)
    assert result["status"] == "unsafe_source_rejected"
    assert any("shadowing" in finding for finding in result["unsafe_findings"])


def test_lean_bridge_rejects_local_dotted_import_shadowing_before_runner(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    local_module = tmp_path / "Algebra" / "Group"
    local_module.mkdir(parents=True)
    (local_module / "Basic.lean").write_text("axiom fake : True\n", encoding="utf-8")
    lean = _lean_file(
        tmp_path,
        "import Algebra.Group.Basic Mathlib.Data.Nat.Basic\n\n"
        "theorem t : True := by\n  trivial\n",
    )
    runner_called = False

    def runner(argv, cwd):  # noqa: ANN001
        nonlocal runner_called
        runner_called = True
        return 0, "compiled", ""

    result = append_lean_bridge_result(
        run_dir,
        lean_file=lean,
        statement_lock=_lock(run_dir),
        enabled=True,
        runner=runner,
        entry_id="lean_local_shadow",
    )
    validate_run(run_dir)

    assert result["status"] == "unsafe_source_rejected"
    assert runner_called is False
    assert any("local module path" in finding for finding in result["unsafe_findings"])
    assert _entry(run_dir, "lean_local_shadow")["earned_status"] == "unverified"


def test_lean_bridge_rejects_statement_drift_before_running(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    result = run_lean_bridge(
        run_dir,
        lean_file=_lean_file(tmp_path),
        statement_lock=_lock(run_dir, equivalent=False),
        enabled=True,
        runner=lambda argv, cwd: (0, "ok", ""),
    )
    assert result["status"] == "statement_lock_rejected"
    assert result["compiled"] is False
    assert result["proof_verification_entry"]["earned_status"] == "unverified"


def test_lean_bridge_rejects_stale_build_output(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    lean = _lean_file(tmp_path)
    output = tmp_path / "Proof.olean"
    output.write_text("old", encoding="utf-8")
    old = lean.stat().st_mtime - 10
    os.utime(output, (old, old))
    result = run_lean_bridge(
        run_dir,
        lean_file=lean,
        statement_lock=_lock(run_dir),
        enabled=True,
        build_output_path=output,
        runner=lambda argv, cwd: (0, "ok", ""),
    )
    assert result["status"] == "stale_build_output"
    assert result["compiled"] is False


def test_successful_fake_lean_compile_still_does_not_earn_lean_checked(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    result = append_lean_bridge_result(
        run_dir,
        lean_file=_lean_file(tmp_path),
        statement_lock=_lock(run_dir),
        enabled=True,
        runner=lambda argv, cwd: (0, "compiled", ""),
        entry_id="lean_compiled",
    )
    validate_run(run_dir)

    assert result["status"] == "compiled"
    entry = _entry(run_dir, "lean_compiled")
    assert entry["earned_status"] == "unverified"
    assert entry["confirmed"] is False

    entry["earned_status"] = "lean_checked"
    pv = read_yaml(run_dir / "proof_verification.yaml")
    pv["entries"] = [entry]
    write_yaml(run_dir / "proof_verification.yaml", pv)
    with pytest.raises(KernelValidationError, match="lean_checked is not earnable"):
        validate_run(run_dir)
