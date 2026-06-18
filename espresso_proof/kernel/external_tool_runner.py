"""Goal 25 (slice 2): fake sandbox executor for external tools.

A minimal, safe executor that runs ONLY allowlisted DETERMINISTIC FAKE tools and
records an audited :class:`ExternalToolRun`. It never runs arbitrary tools, never
uses the network, and -- crucially -- never writes a proof/source/evidence/grade
status: a run record is raw evidence only (slice 1 guarantees this by
construction). Earning a SCOPED status from a confirming run is reserved for the
allowlisted-adapter + proof-verification bridge in slice 3; this slice's runner
always records ``trusted=False``.

Safety properties of every run:
- only a name in ``FAKE_TOOLS`` may execute (unknown / GitHub / PyPI tools are
  refused -- they stay candidate-only);
- a MINIMAL environment is passed (PATH only) -- no secrets / no full os.environ;
- the process runs with cwd = the run directory and a hard timeout;
- stdout/stderr are captured and truncated to excerpts;
- FAIL CLOSED: timeout, nonzero exit, or a parser that cannot parse the output
  yields no parsed result and ``trusted=False`` (no evidence).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import ExternalToolRun, ExternalToolRunsReport
from espresso_proof.kernel.validators import validate_run

_EXCERPT_LIMIT = 2000
DEFAULT_TIMEOUT_SECONDS = 10.0


def _excerpt(text: str) -> str:
    text = text or ""
    if len(text) > _EXCERPT_LIMIT:
        return text[:_EXCERPT_LIMIT].rstrip() + " ...[truncated]"
    return text


def run_completed_cleanly(timed_out: bool, exit_code: int | None) -> bool:
    """Single source of truth for "did this tool run complete cleanly?": not timed
    out AND a zero exit. Used by the executor, the CLI, and the memo so the
    fail-closed decision can never drift between them."""
    return not (bool(timed_out) or (exit_code not in (0, None)))


def _path_is_confined(raw: str) -> bool:
    p = Path(str(raw))
    return not (p.is_absolute() or str(raw).startswith("/") or ".." in p.parts)


def _parse_result_line(stdout: str) -> str:
    """Parser for the deterministic fake tools: expect a single ``RESULT: <x>``
    line. Anything else is malformed (raises -> fail closed)."""
    for line in stdout.splitlines():
        if line.startswith("RESULT:"):
            return line[len("RESULT:") :].strip()
    raise ValueError("no RESULT: line in tool output")


# Allowlisted deterministic FAKE tools. Each runs a small, network-free,
# deterministic Python program. argv is recorded verbatim (the script is the
# "tool"). These are NOT trusted backends: the runner records trusted=False, so a
# fake run earns nothing -- it only exercises the executor + record + fail-closed.
_OK_SCRIPT = "print('RESULT: ok deterministic-check'); "
_MALFORMED_SCRIPT = "print('no parseable result here')"
_FAIL_SCRIPT = "import sys; print('RESULT: partial'); sys.exit(3)"
_TIMEOUT_SCRIPT = "import time; time.sleep(3600)"
# Real deterministic adapter tool (slice 3): exact modular exponentiation. Reads
# three INTEGER operands from argv (data, never code) and prints the exact result.
# A non-integer operand raises -> nonzero exit -> fail closed. No network, no I/O.
_EXACT_MODEXP_SCRIPT = (
    "import sys; b, e, m = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]); "
    "print(f'RESULT: {pow(b, e, m)}')"
)

FAKE_TOOLS: dict[str, dict] = {
    "fake_deterministic_check": {
        "argv": [sys.executable, "-I", "-c", _OK_SCRIPT],
        "parse": _parse_result_line,
        "scope_note": "Deterministic fake check of the stated finite/computational case only.",
    },
    "fake_malformed_output": {
        "argv": [sys.executable, "-I", "-c", _MALFORMED_SCRIPT],
        "parse": _parse_result_line,
        "scope_note": "Deterministic fake tool producing unparseable output (fail-closed demo).",
    },
    "fake_nonzero_exit": {
        "argv": [sys.executable, "-I", "-c", _FAIL_SCRIPT],
        "parse": _parse_result_line,
        "scope_note": "Deterministic fake tool exiting nonzero (fail-closed demo).",
    },
    "fake_timeout": {
        "argv": [sys.executable, "-I", "-c", _TIMEOUT_SCRIPT],
        "parse": _parse_result_line,
        "scope_note": "Deterministic fake tool that hangs (timeout fail-closed demo).",
    },
    # Real, stable, deterministic adapter tool: exact integer modular exponentiation.
    # Operands arrive as argv DATA (parsed as int; never interpolated as code).
    # trusted=True marks it as an allowlisted deterministic backend (the RUNNER sets
    # this from the allowlist; it is never read from the artifact author).
    "exact_modexp": {
        "argv": [sys.executable, "-I", "-c", _EXACT_MODEXP_SCRIPT],
        "parse": _parse_result_line,
        "scope_note": "Exact integer modular exponentiation for the given operands only.",
        "trusted": True,
        "version": "python-int-pow",
        "real": True,
    },
}


class UnknownToolError(ValueError):
    """Raised when a tool name is not in the allowlist (cannot be executed)."""


def run_external_tool(
    run_dir: Path,
    tool_name: str,
    *,
    entry_id: str,
    statement_checked: str = "",
    extra_args: list[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ExternalToolRun:
    """Execute one allowlisted deterministic tool and return its run record.

    Pure executor: does not persist anything. Refuses any tool not in
    ``FAKE_TOOLS``. Fails closed (no parsed result) on timeout / nonzero exit /
    malformed output. Always records ``trusted=False`` -- a RUN record is raw
    evidence; earning a scoped status is the adapter's job via the kernel bridge.

    ``extra_args`` are appended to the fixed allowlisted argv as DATA only (each is
    coerced to ``str``); they are never interpreted as code (the tool script reads
    them via ``sys.argv`` and validates them), so they cannot inject a command.
    """
    run_dir = Path(run_dir)
    if tool_name not in FAKE_TOOLS:
        raise UnknownToolError(
            f"tool {tool_name!r} is not allowlisted; unknown tools cannot be executed "
            "and stay candidate-only"
        )
    spec = FAKE_TOOLS[tool_name]
    argv: list[str] = list(spec["argv"]) + [str(a) for a in (extra_args or [])]
    parse: Callable[[str], str] = spec["parse"]
    # Minimal environment (PATH for the interpreter + LC_ALL for stable output):
    # no secrets, no full os.environ handed to the tool.
    env = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C"}

    timed_out = False
    exit_code: int | None = None
    stdout = ""
    stderr = ""
    start = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 - fixed allowlisted argv, minimal env, cwd-confined, timeout
            argv,
            cwd=str(run_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        exit_code = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    duration = round(time.monotonic() - start, 6)

    # Fail closed: only a clean exit may yield a parsed result.
    clean = run_completed_cleanly(timed_out, exit_code)
    parsed_result = ""
    conclusion = ""
    if clean:
        try:
            parsed_result = parse(stdout)
            kind = "deterministic check" if spec.get("real") else "deterministic fake check"
            # Goal 38: the conclusion embeds tool output; scrub it so a tool
            # printing "proof-close" / "we proved the theorem" cannot smuggle proof-
            # complete language into the interpretive field (the model validator
            # rejects unscrubbed text; raw stdout stays auditable in
            # stdout_excerpt).
            from espresso_proof.kernel.proof_language import scrub_proof_language

            conclusion = scrub_proof_language(f"{kind} produced: {parsed_result}")
        except Exception:  # noqa: BLE001 - any parse failure is malformed -> fail closed
            clean = False
    if not clean:
        conclusion = ""  # never present a positive conclusion for a non-clean run

    return ExternalToolRun(
        id=entry_id,
        tool_name=tool_name,
        source_url="",
        version=str(spec.get("version", "fake-1")),
        install_command="",
        argv=argv,
        working_dir=".",
        input_files=[],
        output_files=[],
        stdout_excerpt=_excerpt(stdout),
        stderr_excerpt=_excerpt(stderr),
        exit_code=exit_code,
        duration_seconds=duration,
        timeout_seconds=timeout_seconds,
        timed_out=timed_out,
        parsed_result=parsed_result,
        ai_interpretation="",
        conclusion=conclusion,
        statement_checked=statement_checked,
        scope_note=spec["scope_note"],
        not_universal_proof=True,
        network_used=False,
        # The RUNNER sets trusted from the allowlist spec (never from artifact
        # input): an allowlisted deterministic backend is trusted-as-PROVENANCE.
        # This is display only -- a RUN record still earns no status; only the
        # adapter, on a confirming result, earns a SCOPED proof-verification entry
        # through the kernel bridge.
        trusted=bool(spec.get("trusted", False)),
    )


def append_external_tool_run(run_dir: Path, run: ExternalToolRun) -> ExternalToolRunsReport:
    """Append a run to external_tool_runs.yaml (creating + registering it if
    absent), validate the full report + run, and write it back. Fails closed:
    the report is validated by validate_run before it is trusted."""
    run_dir = Path(run_dir)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    etr_path = run_dir / "external_tool_runs.yaml"
    if etr_path.exists():
        report = read_yaml(etr_path)
    else:
        report = {
            "schema_version": 1,
            "claim_id": claim["id"],
            "run_id": manifest["run_id"],
            "generated_at": datetime.now(UTC).isoformat(),
            "runs": [],
            "notes": [
                "External tool runs are raw, audited evidence. They never set a "
                "proof/source/evidence/grade status; only a trusted deterministic "
                "backend via the proof-verification bridge earns a scoped status.",
            ],
        }
    existing_ids = {r.get("id") for r in report.get("runs", [])}
    if run.id in existing_ids:
        raise ValueError(f"external tool run id {run.id!r} already exists in this run")
    # Confine paths BEFORE writing anything, so a non-confined record never lands
    # on disk (the kernel validate_run below re-checks as defense in depth, but we
    # fail closed here to keep the append atomic / avoid leaving a poisoned file).
    for where, paths in (
        ("working_dir", [run.working_dir]),
        ("input_files", run.input_files),
        ("output_files", run.output_files),
    ):
        for raw in paths:
            if not _path_is_confined(raw):
                raise ValueError(
                    f"external tool run {where} path must be relative to the run "
                    f"directory (no absolute path, no '..'): {raw!r}"
                )
    report.setdefault("runs", []).append(run.model_dump(mode="json"))
    # Validate the assembled report model before writing (fail closed).
    ExternalToolRunsReport.model_validate(report)
    write_yaml(etr_path, report)
    manifest.setdefault("optional_artifacts", {})["external_tool_runs"] = "external_tool_runs.yaml"
    write_yaml(manifest_path, manifest)
    validate_run(run_dir)  # fail closed: path confinement + claim binding re-checked
    return ExternalToolRunsReport.model_validate(read_yaml(etr_path))


def run_and_record_external_tool(
    run_dir: Path,
    tool_name: str,
    *,
    entry_id: str,
    statement_checked: str = "",
    extra_args: list[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ExternalToolRun:
    """Execute an allowlisted tool and persist its record. Convenience for the
    CLI. Earns nothing (records only)."""
    run = run_external_tool(
        run_dir,
        tool_name,
        entry_id=entry_id,
        statement_checked=statement_checked,
        extra_args=extra_args,
        timeout_seconds=timeout_seconds,
    )
    append_external_tool_run(run_dir, run)
    return run


__all__ = [
    "FAKE_TOOLS",
    "DEFAULT_TIMEOUT_SECONDS",
    "UnknownToolError",
    "run_completed_cleanly",
    "run_external_tool",
    "append_external_tool_run",
    "run_and_record_external_tool",
]
