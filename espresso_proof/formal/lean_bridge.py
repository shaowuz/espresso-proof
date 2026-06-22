"""Fail-closed optional Lean bridge.

PR70J deliberately does not make Lean a default proof path. The bridge can be
called explicitly, records why it did or did not run, and appends only an
``unverified`` proof-verification entry. A future kernel slice may decide how to
earn ``lean_checked``; this bridge keeps the provenance and refusal checks ready
without weakening today's validator boundary.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from espresso_proof.formal.statement_lock import validate_statement_lock
from espresso_proof.kernel.io import path_exists, read_text
from espresso_proof.verification.proof_verification import append_verification_entries

LEAN_BRIDGE_ADAPTER_ID = "lean_bridge_pr70j_fail_closed"
FORBIDDEN_LEAN_TOKENS = ("sorry", "admit", "axiom")

LeanRunner = Callable[[list[str], Path], tuple[int, str, str]]


def scan_lean_source(source: str, *, source_path: Path | None = None) -> list[str]:
    """Return fail-closed Lean source hazards.

    The scanner is intentionally simple and conservative. It blocks the classic
    fake-proof tokens and import shapes that can shadow trusted Mathlib imports
    from local files.
    """

    findings: list[str] = []
    for token in FORBIDDEN_LEAN_TOKENS:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", source):
            findings.append(f"forbidden token: {token}")

    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("import "):
            continue
        for imported in stripped.split()[1:]:
            if imported.startswith(".") or "/" in imported or "\\" in imported:
                findings.append(f"import shadowing risk: {imported}")
            if source_path is not None:
                parent = source_path.parent
                local_module = parent / Path(*imported.split("."))
                if path_exists(local_module.with_suffix(".lean")) or path_exists(local_module):
                    findings.append(f"import shadowing risk: local module path for {imported}")
                if imported.startswith("Mathlib") and (
                    path_exists(parent / "Mathlib.lean") or path_exists(parent / "Mathlib")
                ):
                    findings.append("import shadowing risk: local Mathlib path")
    return findings


def run_lean_bridge(
    run_dir: Path,
    *,
    lean_file: Path,
    statement_lock: dict[str, Any],
    enabled: bool = False,
    entry_id: str = "lean_bridge",
    lean_executable: str = "lean",
    build_output_path: Path | None = None,
    runner: LeanRunner | None = None,
) -> dict[str, Any]:
    """Attempt an explicit Lean check and return a fail-closed bridge result."""

    run_dir = Path(run_dir)
    lean_file = Path(lean_file)
    lock_ok, lock_reason = validate_statement_lock(run_dir, statement_lock)
    source_text = read_text(lean_file) if path_exists(lean_file) else ""
    source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    findings = scan_lean_source(source_text, source_path=lean_file)

    status = "not_run"
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    compiled = False

    if not enabled:
        status = "disabled"
        reason = "Lean bridge requires an explicit enabled flag"
    elif not lock_ok:
        status = "statement_lock_rejected"
        reason = lock_reason
    elif not path_exists(lean_file):
        status = "missing_source"
        reason = "Lean source file is missing"
    elif findings:
        status = "unsafe_source_rejected"
        reason = "; ".join(findings)
    elif _stale_build_output(lean_file, build_output_path):
        status = "stale_build_output"
        reason = "Lean build output is older than the source file"
    else:
        executable = lean_executable if runner is not None else shutil.which(lean_executable)
        if not executable:
            status = "lean_unavailable"
            reason = "Lean executable is unavailable"
        else:
            argv = [str(executable), str(lean_file)]
            if runner is None:
                proc = subprocess.run(
                    argv,
                    cwd=str(lean_file.parent),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
            else:
                exit_code, stdout, stderr = runner(argv, lean_file.parent)
            compiled = exit_code == 0
            status = "compiled" if compiled else "lean_failed"
            reason = "Lean compiled, but PR70J records no formal status" if compiled else "Lean failed"

    output_hash = hashlib.sha256(f"{stdout}\n{stderr}".encode("utf-8")).hexdigest()
    entry = {
        "id": entry_id,
        "verifier_type": "lean_bridge",
        "claim_id": statement_lock.get("claim_id", ""),
        "target": f"Lean check for statement lock {statement_lock.get('lock_id', '')}",
        "input_summary": f"source_sha256={source_hash}; statement_lock={statement_lock.get('lock_id', '')}",
        "result": f"{status}: {reason}",
        "trusted": False,
        "confirmed": False,
        "earned_status": "unverified",
        "scope_note": (
            "Optional Lean bridge result only. PR70J does not earn lean_checked, "
            "does not prove the claim, and stays separate from human attestation."
        ),
        "adapter_id": LEAN_BRIDGE_ADAPTER_ID,
        "code_version": "pr70j",
        "input_hash": source_hash,
        "output_hash": output_hash,
        "replay_command": f"{lean_executable} {lean_file}",
    }
    return {
        "schema_version": 1,
        "status": status,
        "reason": reason,
        "enabled": bool(enabled),
        "statement_lock_valid": lock_ok,
        "statement_lock_reason": lock_reason,
        "unsafe_findings": findings,
        "lean_file": str(lean_file),
        "source_sha256": source_hash,
        "exit_code": exit_code,
        "compiled": compiled,
        "proof_verification_entry": entry,
        "notes": [
            "Lean bridge is optional and fail-closed.",
            "This bridge never earns lean_checked in PR70J.",
        ],
    }


def append_lean_bridge_result(
    run_dir: Path,
    *,
    lean_file: Path,
    statement_lock: dict[str, Any],
    enabled: bool = False,
    entry_id: str = "lean_bridge",
    lean_executable: str = "lean",
    build_output_path: Path | None = None,
    runner: LeanRunner | None = None,
) -> dict[str, Any]:
    """Run the bridge and append its unverified entry to proof_verification.yaml."""

    result = run_lean_bridge(
        run_dir,
        lean_file=lean_file,
        statement_lock=statement_lock,
        enabled=enabled,
        entry_id=entry_id,
        lean_executable=lean_executable,
        build_output_path=build_output_path,
        runner=runner,
    )
    append_verification_entries(run_dir, [result["proof_verification_entry"]])
    return result


def _stale_build_output(lean_file: Path, build_output_path: Path | None) -> bool:
    if build_output_path is None or not path_exists(build_output_path):
        return False
    return build_output_path.stat().st_mtime < lean_file.stat().st_mtime
