#!/usr/bin/env python3
"""Espresso external-agent adapter using the local Codex CLI.

Wire it up:

    export ESPRESSO_EXTERNAL_AGENT_COMMAND="uv run espresso-codex-agent"
    uv run espresso serve --provider external

Contract (espresso_proof.agents.llm_agents.ExternalCommandClient):
- read one JSON request on stdin: {system, prompt, provider, model,
  required_json_schema, timeout_seconds?} — timeout_seconds is optional and
  additive (the client's actual outer timeout; absent for older callers).
- on success (or a safely-fallbackable failure) print one JSON object on
  stdout and exit 0; when the role's required_json_schema cannot be satisfied
  by the conservative fallback (back-half roles), a failure exits NON-ZERO
  with a FATAL stderr line instead of emitting schema-invalid JSON.

This adapter pins Codex to gpt-5.5 with medium reasoning by default, injects stage-specific Espresso skill text and any explicit active goal, and writes local replay logs. The
Python kernel still owns every source/evidence/proof/grade status transition.
"""

from __future__ import annotations

import json
import os
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from espresso_proof.agents.adapter_runtime import (
    DEFAULT_CODEX_EXTERNAL_TIMEOUT_SECONDS,
    fallback_is_schema_valid,
    resolve_inner_timeout,
)
from espresso_proof.agents.agent_logging import (
    log_event,
    log_prompt_record,
    resolve_agent_log_dir,
    shell_preview,
    utc_timestamp,
)
from espresso_proof.agents.agent_skill_catalog import select_agent_skills


CODEX_MODEL = "gpt-5.5"
CODEX_REASONING_EFFORT = os.environ.get("ESPRESSO_CODEX_REASONING_EFFORT", "medium")

_FALLBACK = {
    "summary": "No usable Codex agent output was produced.",
    "steps": [],
    "source_requests": [],
    "missing_hypotheses": [],
    "risks": [],
    "verdict": "needs_revision",
    "recommended_human_action": "revise",
}

_INSTRUCTION = (
    "Return ONLY a single JSON object. Do not use markdown fences. This is a "
    "CANDIDATE planning output only: do not claim anything is proved, verified, "
    "proof-close, or proof-complete, and do not assert internal source/check statuses. "
    "The Espresso Python kernel decides those statuses."
)
_DIAGNOSTIC_META_KEY = "_espresso_codex_adapter"


def main() -> int:
    started = time.monotonic()
    request_id = str(uuid.uuid4())
    cwd = Path.cwd()
    log_dir = resolve_agent_log_dir(cwd=cwd)

    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        request = {}

    system = str(request.get("system", ""))
    prompt = str(request.get("prompt", ""))
    active_goal_file = os.environ.get("ESPRESSO_ACTIVE_GOAL_FILE")
    # The caller DECLARES the request type (product_math by default and
    # for any unknown value). Only a maintenance/goal-running request may
    # auto-inject repository maintenance context; a product mathematical
    # run must not (a past injection bug).
    request_type = os.environ.get("ESPRESSO_REQUEST_TYPE")
    selection = select_agent_skills(
        system=system,
        prompt=prompt,
        cwd=cwd,
        active_goal_file=active_goal_file,
        request_type=request_type,
    )
    full_prompt = _build_full_prompt(
        system=system,
        prompt=prompt,
        goal_text=selection.goal.text,
        skill_names=selection.skills,
        skill_text=selection.skill_text,
    )

    inner_timeout = resolve_inner_timeout(
        request=request, default_outer=DEFAULT_CODEX_EXTERNAL_TIMEOUT_SECONDS
    )
    with tempfile.TemporaryDirectory(prefix="espresso-codex-") as tmp:
        last_message = Path(tmp) / "last_message.txt"
        output_schema = _write_output_schema_if_needed(
            tmp_dir=Path(tmp),
            request=request,
            system=system,
            prompt=prompt,
        )
        command = _codex_command(
            last_message=last_message,
            output_schema=output_schema,
            cwd=cwd,
        )
        _emit_status(selection=selection)
        _log_start(
            log_dir=log_dir,
            request_id=request_id,
            selection=selection,
            command=command,
            cwd=cwd,
            system=system,
            prompt=prompt,
        )

        return_code: int | None = None
        fallback_reason: str | None = None
        parse_status = "not_run"
        result: dict
        _raw_codex_stdout = ""
        try:
            output, return_code, _raw_codex_stdout = _run_codex(
                command,
                full_prompt=full_prompt,
                last_message=last_message,
                timeout=inner_timeout,
            )
            result = _extract_json(output)
            if not isinstance(result, dict):
                raise ValueError("Codex output was not a JSON object")
            _validate_required_keys_if_needed(
                data=result,
                request=request,
                system=system,
                prompt=prompt,
            )
            parse_status = "ok"
        except Exception as exc:  # noqa: BLE001 - fallback where safe, else fail closed
            fallback_reason = str(exc)[:500]
            if not fallback_is_schema_valid(request, _FALLBACK):
                # Back-half authoritative-schema role: the prover-shaped
                # fallback would be schema-invalid downstream. Exit non-zero
                # so the caller's transactional rollback takes over.
                parse_status = "error_propagated"
                duration = round(time.monotonic() - started, 3)
                _log_finish(
                    log_dir=log_dir,
                    request_id=request_id,
                    selection=selection,
                    return_code=return_code,
                    duration=duration,
                    parse_status=parse_status,
                    fallback_reason=fallback_reason,
                )
                reason = f"codex adapter could not produce usable JSON: {fallback_reason}"[:300]
                print(f"[espresso-agent] FATAL: {reason}", file=sys.stderr)
                return 1
            parse_status = "fallback"
            result = dict(_FALLBACK)
            result["summary"] = f"codex adapter could not produce usable JSON: {fallback_reason}"[:300]

        duration = round(time.monotonic() - started, 3)
        _log_finish(
            log_dir=log_dir,
            request_id=request_id,
            selection=selection,
            return_code=return_code,
            duration=duration,
            parse_status=parse_status,
            fallback_reason=fallback_reason,
        )

    usage = _usage_from_json_events(_raw_codex_stdout)
    result[_DIAGNOSTIC_META_KEY] = {
        "adapter": "espresso_codex_agent",
        "request_id": request_id,
        "parse_status": parse_status,
        "fallback_used": parse_status != "ok",
        "fallback_reason": fallback_reason,
        "model": CODEX_MODEL,
        "effort": CODEX_REASONING_EFFORT,
        "usage": usage,
        "usage_source": "codex_event_stream" if usage else "unreported",
    }

    json.dump(result, sys.stdout)
    return 0


def _codex_command(
    *,
    last_message: Path,
    output_schema: Path | None,
    cwd: Path,
) -> list[str]:
    codex_cli = os.environ.get("ESPRESSO_CODEX_CLI", "codex")
    command = [
        *_codex_command_prefix(codex_cli),
        "exec",
        "--json",
        "--output-last-message",
        str(last_message),
        "-m",
        CODEX_MODEL,
        "-c",
        f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"',
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--cd",
        str(cwd),
        "-",
    ]
    if output_schema is not None:
        command[-1:-1] = ["--output-schema", str(output_schema)]
    return command


def _codex_command_prefix(codex_cli: str) -> list[str]:
    path = Path(codex_cli)
    if os.name != "nt":
        return [codex_cli]

    if not path.is_file():
        resolved = shutil.which(codex_cli)
        if resolved:
            path = Path(resolved)

    if path.name.lower() == "codex" and not path.suffix and path.with_suffix(".exe").is_file():
        path = path.with_suffix(".exe")

    if path.is_file() and _is_windowsapps_path(path):
        path = _cached_windowsapps_cli(path)

    if path.is_file() and path.suffix.lower() not in {".exe", ".bat", ".cmd"}:
        return [sys.executable, str(path)]
    if path.is_file():
        return [str(path)]
    return [codex_cli]


def _is_windowsapps_path(path: Path) -> bool:
    return any(part.lower() == "windowsapps" for part in path.parts)


def _cached_windowsapps_cli(source: Path) -> Path:
    """Copy a packaged WindowsApps CLI binary to a user-writable cache.

    Windows can expose Store-packaged binaries on PATH while denying direct
    process creation from the Program Files WindowsApps package directory.
    Copying the same installed binary to a user cache makes the CLI spawnable
    without changing any agent/trust semantics.
    """

    stat = source.stat()
    cache_root = os.environ.get("ESPRESSO_CODEX_CLI_CACHE_DIR")
    root = (
        Path(cache_root)
        if cache_root
        else Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
        / "EspressoProof"
        / "codex-cli-cache"
    )
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:12]
    suffix = source.suffix or ".exe"
    cached = root / f"codex-{digest}-{stat.st_size}-{int(stat.st_mtime_ns)}{suffix}"
    if cached.exists() and cached.stat().st_size == stat.st_size:
        return cached
    tmp = cached.with_name(f"{cached.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, cached)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return cached


def _write_output_schema_if_needed(
    *,
    tmp_dir: Path,
    request: dict,
    system: str,
    prompt: str,
) -> Path | None:
    if not _uses_generic_agent_output(system=system, prompt=prompt):
        return None
    schema = request.get("required_json_schema")
    if not isinstance(schema, dict):
        return None
    path = tmp_dir / "agent_output.schema.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    return path


def _validate_required_keys_if_needed(
    *,
    data: dict,
    request: dict,
    system: str,
    prompt: str,
) -> None:
    if not _uses_generic_agent_output(system=system, prompt=prompt):
        return
    schema = request.get("required_json_schema")
    required = schema.get("required", []) if isinstance(schema, dict) else []
    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(f"Codex output missing required keys: {', '.join(missing)}")


def _uses_generic_agent_output(*, system: str, prompt: str) -> bool:
    text = f"{system}\n{prompt}".lower()
    return any(
        marker in text
        for marker in (
            "external agent diagnostic",
            "prover agent",
            "verifier agent",
            "reviewer agent",
            "strategy reviewer",
            "evidence reviewer",
            "line proof reviewer",
            "line-numbered proof writer",
            "proof-consensus review",
            "proof gap resolver",
            '"role": "prover"',
            '"role": "line_numbered_proof_writer"',
            '"role": "proof_gap_resolver"',
            "prover_loop_round",
        )
    )


def _is_diagnostic_request(*, system: str, prompt: str) -> bool:
    return "external agent diagnostic" in f"{system}\n{prompt}".lower()


def _build_full_prompt(
    *,
    system: str,
    prompt: str,
    goal_text: str,
    skill_names: tuple[str, ...],
    skill_text: str,
) -> str:
    return "\n\n".join(
        [
            "ESPRESSO ACTIVE GOAL",
            goal_text,
            "ESPRESSO SELECTED SKILLS",
            ", ".join(skill_names),
            skill_text,
            "ESPRESSO SYSTEM PROMPT",
            system,
            "ESPRESSO STAGE REQUEST",
            prompt,
            "ESPRESSO OUTPUT CONTRACT",
            _INSTRUCTION,
        ]
    )


def _run_codex(
    command: list[str], *, full_prompt: str, last_message: Path, timeout: int
) -> tuple[str, int, str]:
    proc = subprocess.run(
        command,
        input=full_prompt,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "codex CLI returned non-zero"
        raise RuntimeError(detail)
    text = last_message.read_text(encoding="utf-8") if last_message.exists() else ""
    if not text.strip():
        text = _last_text_from_json_events(proc.stdout)
    return text, proc.returncode, proc.stdout


def _usage_from_json_events(stdout: str) -> dict | None:
    """The last usage dict in the ``codex exec --json`` event stream, keys
    passed through only when actually present (absent stays absent — null
    means unknown, never 0)."""

    usage: dict | None = None
    for raw in (stdout or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        candidate = event.get("usage")
        if not isinstance(candidate, dict):
            info = event.get("info")
            candidate = info.get("usage") if isinstance(info, dict) else None
        if isinstance(candidate, dict) and candidate:
            usage = candidate
    return usage


def _last_text_from_json_events(stdout: str) -> str:
    chunks: list[str] = []
    for raw in stdout.splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        text = event.get("text") or event.get("message") or event.get("content")
        if isinstance(text, str):
            chunks.append(text)
    return "\n".join(chunks)


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence is not None:
        return json.loads(fence.group(1))
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise ValueError("no JSON object found in Codex output")
    return json.loads(match.group(0))


def _log_start(
    *,
    log_dir: Path,
    request_id: str,
    selection,
    command: list[str],
    cwd: Path,
    system: str,
    prompt: str,
) -> None:
    timestamp = utc_timestamp()
    base = {
        "timestamp": timestamp,
        "event": "agent_start",
        "request_id": request_id,
        "stage": selection.stage,
        "skills": list(selection.skills),
        "goal_file": selection.goal.path,
        "command_argv": command,
        "command_preview": shell_preview(command),
        "working_directory": str(cwd),
        "model": CODEX_MODEL,
        "effort": CODEX_REASONING_EFFORT,
    }
    log_event(log_dir, base)
    log_prompt_record(
        log_dir,
        {
            **base,
            "system_prompt": system,
            "user_prompt": prompt,
            "injected_goal": selection.goal.text,
            "injected_skill_text": selection.skill_text,
            "result": {},
        },
    )


def _log_finish(
    *,
    log_dir: Path,
    request_id: str,
    selection,
    return_code: int | None,
    duration: float,
    parse_status: str,
    fallback_reason: str | None,
) -> None:
    log_event(
        log_dir,
        {
            "event": "agent_finish",
            "request_id": request_id,
            "stage": selection.stage,
            "skills": list(selection.skills),
            "goal_file": selection.goal.path,
            "model": CODEX_MODEL,
            "effort": CODEX_REASONING_EFFORT,
            "return_code": return_code,
            "duration_sec": duration,
            "ok": parse_status == "ok",
            "parse_status": parse_status,
            "fallback_reason": fallback_reason,
        },
    )


def _emit_status(*, selection) -> None:
    print(
        f"[espresso-agent] Using goal: {selection.goal.path or 'none'}",
        file=sys.stderr,
    )
    print(
        f"[espresso-agent] Using skills: {', '.join(selection.skills)}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
