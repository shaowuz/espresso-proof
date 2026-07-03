"""LLM transport clients and adapter diagnostics for Espresso agents.

Split out of ``espresso_proof.agents.llm_agents``; that module remains the
compatibility facade and re-exports every name defined here.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from espresso_proof.agents.output_schemas import (
    AGENT_OUTPUT_SCHEMA,
    _required_json_schema_for_system,
)
from espresso_proof.agents.sanitize import (
    extract_response_text,
    normalize_verdict,
    strip_code_fence,
)
from espresso_proof.kernel.llm_client import LLMClient


CODEX_EXTERNAL_MODEL = "gpt-5.5-medium"
DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 120
DEFAULT_CODEX_EXTERNAL_TIMEOUT_SECONDS = 360
EXTERNAL_AGENT_TIMEOUT_ENV = "ESPRESSO_EXTERNAL_AGENT_TIMEOUT_SECONDS"
CODEX_ADAPTER_DIAGNOSTIC_KEY = "_espresso_codex_adapter"


class StubLLMClient:
    provider = "stub"

    def __init__(self, model: str = "stub-espresso-agent") -> None:
        self.model = model

    def complete_json(self, *, system: str, prompt: str) -> dict:
        lowered = system.lower()
        if "line-numbered proof writer" in lowered:
            # Goal 69: a real structured parity proof of n^2 - n even (consecutive
            # integers route), each line with a concrete justification + dependencies.
            return {
                "proof_kind": "consecutive_integers",
                "lines": [
                    {"line_id": "L1", "statement": "n^2 - n = n(n-1) for every integer n.",
                     "justification": "Algebraic factoring: distribute n(n-1) = n^2 - n.", "depends_on": []},
                    {"line_id": "L2", "statement": "n and n-1 are consecutive integers.",
                     "justification": "They differ by 1 by definition of consecutive integers.", "depends_on": []},
                    {"line_id": "L3", "statement": "Among any two consecutive integers exactly one is even.",
                     "justification": "Integers alternate parity, so of n and n-1 one is even.", "depends_on": ["L2"]},
                    {"line_id": "L4", "statement": "Therefore 2 divides n(n-1).",
                     "justification": "A product with an even factor is divisible by 2.", "depends_on": ["L3"]},
                    {"line_id": "L5", "statement": "Hence n^2 - n is even for every integer n.",
                     "justification": "Combine L1 and L4: n^2 - n = n(n-1) is divisible by 2.", "depends_on": ["L1", "L4"]},
                ],
            }
        if "proof-consensus review" in lowered:
            import re as _re

            line_ids = _re.findall(r"['\"]?line_id['\"]?\s*[:=]\s*['\"]([^'\"]+)['\"]", prompt)
            return {
                "verdict": "no_fatal_flaw_found",
                "looked_for": "non-sequiturs, misapplied lemmas, missing cases",
                "finding": "Stub proof-consensus review found no argument-breaking flaw in the line-numbered proof.",
                "cited_line_ids": sorted(set(line_ids)) or ["L1"],
                "residual_objections": ["Exact textbook locators still pending (residual, non-fatal)."],
            }
        if "strategy reviewer" in lowered:
            return {
                "summary": "Strategy reviewer sees a plausible route but flags theorem drift and missing genericity control.",
                "steps": [
                    "Compare the locked claim against the proposed route.",
                    "Check whether very general and all objects are kept separate.",
                ],
                "source_requests": [
                    "Exact theorem supporting the main reduction.",
                ],
                "missing_hypotheses": [
                    "Precise genericity condition for the route.",
                ],
                "risks": [
                    "The strategy may prove only a narrower statement than the locked claim.",
                ],
                "verdict": "needs_revision",
                "recommended_human_action": "revise",
            }
        if "evidence reviewer" in lowered:
            return {
                "summary": "Evidence reviewer found candidate examples useful but not enough to upgrade evidence status.",
                "steps": [
                    "Separate checked examples from human_needed examples.",
                    "Do not count unchecked examples as survived or failed evidence.",
                ],
                "source_requests": [
                    "Source or exact computation for the most dangerous example.",
                ],
                "missing_hypotheses": [],
                "risks": [
                    "Example pressure remains incomplete until computation/source checks are pinned.",
                ],
                "verdict": "needs_sources",
                "recommended_human_action": "revise",
            }
        if "line proof reviewer" in lowered:
            return {
                "summary": "Line proof reviewer treats the current argument as sketch-only and requests exact theorem locators.",
                "steps": [
                    "Split the sketch into proof steps.",
                    "Mark external theorem steps as candidate until exact locators match hypotheses.",
                ],
                "source_requests": [
                    "Exact locator for every external theorem step.",
                ],
                "missing_hypotheses": [
                    "Any theorem-specific assumptions not present in the locked claim.",
                ],
                "risks": [
                    "A sketch-only argument cannot become proof-complete evidence.",
                ],
                "verdict": "needs_sources",
                "recommended_human_action": "revise",
            }
        if "verifier" in lowered:
            return {
                "summary": "Verifier found the route exploratory and blocked by source applicability checks.",
                "steps": [
                    "Check whether every proposed theorem has an exact locator.",
                    "Pressure the proposed route with the dangerous examples already listed in artifacts.",
                ],
                "source_requests": [
                    "Exact theorem statement for the main proof step.",
                    "Applicability statement matching the locked claim hypotheses.",
                ],
                "missing_hypotheses": [
                    "Exact genericity condition needed by the candidate route.",
                    "Compatibility between the locked claim and the cited theorem hypotheses.",
                ],
                "risks": [
                    "The route may silently pass from very general objects to all objects.",
                    "Existing source grounding remains candidate-level.",
                ],
                "verdict": "needs_sources",
                "recommended_human_action": "revise",
            }
        return {
            "summary": "Prover proposes a candidate route but leaves source gaps and example pressure unresolved.",
            "steps": [
                "State the intended obstruction as a conditional route.",
                "Map each proof step to an exact source request before raising confidence.",
                "Use special examples to test whether the obstruction survives outside the generic case.",
            ],
            "source_requests": [
                "Exact locator for the obstruction theorem.",
                "Exact hypothesis statement for genericity and specialization.",
            ],
            "missing_hypotheses": [
                "Whether the route applies to all objects or only very general ones.",
            ],
            "risks": [
                "Candidate route may depend on missing hypotheses.",
                "The current evidence cannot upgrade proof status.",
            ],
            "verdict": "candidate",
            "recommended_human_action": "revise",
        }


class OpenAIResponsesClient:
    provider = "openai"

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        timeout: int = 60,
    ) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.api_key = os.environ.get(api_key_env, "")
        if not self.api_key:
            raise RuntimeError(f"{api_key_env} is not set")

    def complete_json(self, *, system: str, prompt: str) -> dict:
        payload = {
            "model": self.model,
            "instructions": system,
            "input": prompt,
            "store": False,
            "max_output_tokens": 1200,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "espresso_agent_output",
                    "schema": _required_json_schema_for_system(system),
                    "strict": True,
                }
            },
        }
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI Responses API failed: {exc.code} {body}") from exc
        text = extract_response_text(data)
        try:
            return json.loads(strip_code_fence(text))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenAI response was not valid agent JSON: {text[:500]}") from exc


class ExternalAgentTimeoutError(RuntimeError):
    """Goal 65C: the external agent subprocess exceeded its timeout and its whole
    process group was killed. Carries the timeout (seconds) so callers can record
    timeout metadata (timed_out / timeout_ms) on the agent event."""

    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"external agent command timed out after {timeout_seconds}s")


def _kill_process_group(proc: subprocess.Popen) -> None:
    """Goal 65C: kill the external agent's ENTIRE process group so a forking or
    hung child cannot survive a timeout as an orphan that blocks the worker
    thread. Best-effort; never raises. Falls back to killing the direct child."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (AttributeError, ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 - best-effort
            pass


class ExternalCommandClient:
    provider = "external"

    def __init__(
        self,
        *,
        model: str,
        command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
        timeout: int | None = None,
    ) -> None:
        self.model = model or "external-json-agent"
        self.command_env = command_env
        raw_command = os.environ.get(command_env, "")
        if not raw_command:
            raise RuntimeError(f"{command_env} is not set")
        self.command = _normalize_external_command(_split_external_command(raw_command))
        if not self.command:
            raise RuntimeError(f"{command_env} is empty")
        if _is_codex_external_command(self.command):
            self.model = CODEX_EXTERNAL_MODEL
        self.timeout = _resolve_external_timeout(self.command, requested_timeout=timeout)

    def complete_json(self, *, system: str, prompt: str) -> dict:
        request = {
            "system": system,
            "prompt": prompt,
            "provider": self.provider,
            "model": self.model,
            "required_json_schema": _required_json_schema_for_system(system),
        }
        # Goal 65C: run the external agent in its OWN process group
        # (start_new_session=True) so a timeout can reclaim the WHOLE group via
        # os.killpg. The order-3 postmortem's prime suspect was a forking/hung
        # external CLI that subprocess.run(timeout=) could not reclaim (it kills
        # only the direct child), leaving an orphan that blocks the worker thread.
        proc = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(input=json.dumps(request), timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_process_group(proc)
            try:
                proc.communicate(timeout=5)  # reap; no zombie / dangling pipe
            except Exception:  # noqa: BLE001 - best-effort reap
                pass
            raise ExternalAgentTimeoutError(self.timeout) from exc
        if proc.returncode != 0:
            detail = (stderr or "").strip() or (stdout or "").strip()
            raise RuntimeError(f"external agent command failed: {detail}")
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"external agent command did not return JSON: {(stdout or '')[:500]}"
            ) from exc
        # A well-formed agent must return a JSON OBJECT. Valid-but-non-dict JSON
        # (a list / string / number / null) fails the same clean, classifiable
        # way as the other adapter errors instead of an opaque AttributeError on
        # .get below.
        if not isinstance(data, dict):
            raise RuntimeError(
                f"external agent command did not return a JSON object: {(stdout or '')[:500]}"
            )
        if isinstance(data.get("output"), dict):
            return data["output"]
        return data


def _is_codex_external_command(command: list[str]) -> bool:
    normalized = [part.strip("\"'") for part in command]
    return (
        any(Path(part).name in {"espresso_codex_agent.py", "espresso-codex-agent"} for part in normalized)
        or "espresso_proof.agents.codex_adapter" in normalized
    )


def _split_external_command(raw_command: str) -> list[str]:
    """Split an env-provided command without corrupting Windows backslashes."""

    parts = shlex.split(raw_command, posix=os.name != "nt")
    if os.name == "nt":
        return [part.strip("\"'") for part in parts]
    return parts


def _normalize_external_command(command: list[str]) -> list[str]:
    """Make a bare Python script external-agent command executable on Windows."""

    if len(command) == 1:
        script = Path(command[0])
        if script.suffix.lower() == ".py" and script.exists():
            return [sys.executable, str(script)]
    return command


def _resolve_external_timeout(command: list[str], *, requested_timeout: int | None) -> int:
    if requested_timeout is not None:
        return requested_timeout
    raw_env = os.environ.get(EXTERNAL_AGENT_TIMEOUT_ENV)
    if raw_env:
        try:
            value = int(raw_env)
        except ValueError as exc:
            raise RuntimeError(f"{EXTERNAL_AGENT_TIMEOUT_ENV} must be an integer") from exc
        if value < 1:
            raise RuntimeError(f"{EXTERNAL_AGENT_TIMEOUT_ENV} must be positive")
        return value
    if _is_codex_external_command(command):
        return DEFAULT_CODEX_EXTERNAL_TIMEOUT_SECONDS
    return DEFAULT_EXTERNAL_TIMEOUT_SECONDS


def openai_agent_diagnostic(
    *,
    model: str,
    api_key_env: str = "OPENAI_API_KEY",
    network_check: bool = False,
    timeout: int = 30,
) -> dict:
    """Report whether the real OpenAI agent path is configured and callable."""
    key_present = bool(os.environ.get(api_key_env, ""))
    report = {
        "provider": "openai",
        "model": model,
        "api_key_env": api_key_env,
        "api_key_present": key_present,
        "endpoint": "https://api.openai.com/v1/responses",
        "structured_output": {
            "format": "json_schema",
            "schema_name": "espresso_agent_output",
            "strict": True,
            "store": False,
        },
        "network_check": "skipped",
        "status": "configured" if key_present else "missing_key",
        "message": (
            f"{api_key_env} is set; run with --network-check to verify a live Responses API call."
            if key_present
            else f"{api_key_env} is not set."
        ),
    }
    if not network_check:
        return report
    if not key_present:
        report["network_check"] = "not_run"
        return report

    try:
        client = OpenAIResponsesClient(model=model, api_key_env=api_key_env, timeout=timeout)
        sample = client.complete_json(
            system=(
                "You are Espresso's agent diagnostic. Return JSON only. "
                "Do not claim any mathematical result is proved."
            ),
            prompt=(
                "Return a minimal candidate-only Espresso agent response for this diagnostic. "
                "Use verdict needs_sources and recommended_human_action revise."
            ),
        )
        missing = [
            key for key in AGENT_OUTPUT_SCHEMA["required"] if key not in sample
        ]
        if missing:
            raise RuntimeError(f"diagnostic response missing keys: {', '.join(missing)}")
        report.update(
            {
                "status": "ready",
                "network_check": "passed",
                "sample_verdict": normalize_verdict(sample.get("verdict")),
                "message": "OpenAI Responses API returned schema-valid Espresso agent JSON.",
            }
        )
    except Exception as exc:
        report.update(
            {
                "status": "error",
                "network_check": "failed",
                "error": str(exc),
                "message": "OpenAI Responses API diagnostic failed.",
            }
        )
    return report


def external_agent_diagnostic(
    *,
    model: str,
    command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    network_check: bool = False,
    timeout: int = 120,
) -> dict:
    """Report whether a local JSON agent command is configured and callable."""
    raw_command = os.environ.get(command_env, "")
    command_present = bool(raw_command)
    command = (
        _normalize_external_command(_split_external_command(raw_command))
        if raw_command
        else []
    )
    effective_model = CODEX_EXTERNAL_MODEL if _is_codex_external_command(command) else (
        model or "external-json-agent"
    )
    report = {
        "provider": "external",
        "model": effective_model,
        "command_env": command_env,
        "command_present": command_present,
        "structured_output": {
            "format": "json",
            "schema_name": "espresso_agent_output",
            "strict": True,
            "store": False,
        },
        "network_check": "skipped",
        "status": "configured" if command_present else "missing_command",
        "message": (
            f"{command_env} is set; run with --network-check to verify the JSON agent command."
            if command_present
            else f"{command_env} is not set."
        ),
    }
    if not network_check:
        return report
    if not command_present:
        report["network_check"] = "not_run"
        return report
    try:
        client = ExternalCommandClient(
            model=model,
            command_env=command_env,
            timeout=timeout,
        )
        sample = client.complete_json(
            system=(
                "You are Espresso's external agent diagnostic. Return JSON only. "
                "Do not claim any mathematical result is proved."
            ),
            prompt=(
                "Return a minimal candidate-only Espresso agent response for this diagnostic. "
                "Use verdict needs_sources and recommended_human_action revise."
            ),
        )
        missing = [key for key in AGENT_OUTPUT_SCHEMA["required"] if key not in sample]
        if missing:
            raise RuntimeError(f"diagnostic response missing keys: {', '.join(missing)}")
        if _is_codex_external_command(client.command):
            _validate_codex_adapter_diagnostic(sample, report)
        report.update(
            {
                "status": "ready",
                "network_check": "passed",
                "sample_verdict": normalize_verdict(sample.get("verdict")),
                "message": "External JSON agent command returned schema-valid Espresso agent JSON.",
            }
        )
    except Exception as exc:
        report.update(
            {
                "status": "error",
                "network_check": "failed",
                "error": str(exc),
                "message": "External JSON agent command diagnostic failed.",
            }
        )
    return report


def _validate_codex_adapter_diagnostic(sample: dict, report: dict) -> None:
    meta = sample.get(CODEX_ADAPTER_DIAGNOSTIC_KEY)
    structured = report.setdefault("structured_output", {})
    structured["adapter_diagnostic_key"] = CODEX_ADAPTER_DIAGNOSTIC_KEY
    if not isinstance(meta, dict):
        raise RuntimeError(
            "Codex adapter diagnostic did not return adapter status; cannot verify live Codex output."
        )
    adapter_diagnostic = {
        "adapter": str(meta.get("adapter") or ""),
        "parse_status": str(meta.get("parse_status") or ""),
        "fallback_used": meta.get("fallback_used") is True,
        "fallback_reason": str(meta.get("fallback_reason") or ""),
        "model": str(meta.get("model") or ""),
        "effort": str(meta.get("effort") or ""),
    }
    structured["adapter_diagnostic"] = adapter_diagnostic
    if adapter_diagnostic["parse_status"] != "ok" or adapter_diagnostic["fallback_used"]:
        reason = adapter_diagnostic["fallback_reason"] or "adapter returned fallback output"
        raise RuntimeError(f"Codex adapter fallback used during diagnostic: {reason}")


def build_client(
    *,
    provider: str,
    model: str,
    api_key_env: str,
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
) -> LLMClient:
    if provider == "stub":
        return StubLLMClient(model=model or "stub-espresso-agent")
    if provider == "openai":
        return OpenAIResponsesClient(model=model, api_key_env=api_key_env)
    if provider == "external":
        return ExternalCommandClient(model=model, command_env=external_agent_command_env)
    raise RuntimeError(f"unsupported LLM provider: {provider}")
