from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .human_time_allocation import (
    only_valid_route as _only_valid_route,
    priority_for_strategy,
    recommended_allocation_for_priority,
    time_allocation_prompt_context,
    write_time_allocation_context,
)
from .claim_clear import ensure_claim_clear_for_downstream
from .io import read_yaml, write_yaml
from .models import LLMAgentRunReport, ProverLoopReport, ProverRoundIndex, ProverRoundReport
from .pressure_status import artifact_revision_hash
from .proof_language import PROOF_COMPLETE_PHRASES
from .research_bank import load_bankable_dict
from .time_allocation import (
    TIME_ALLOCATION_FILENAME,
    ensure_prover_time_allocation_plan,
    time_allocation_prompt_payload,
    write_time_allocation_plan,
)
from .update_inbox import append_update_inbox_event


AGENT_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "source_requests": {"type": "array", "items": {"type": "string"}},
        "missing_hypotheses": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string"},
        "recommended_human_action": {"type": "string"},
    },
    "required": [
        "summary",
        "steps",
        "source_requests",
        "missing_hypotheses",
        "risks",
        "verdict",
        "recommended_human_action",
    ],
}

# Phrases redacted from live agent text by safe_text/safe_list. This MUST stay a
# superset of the proof-complete phrases the kernel forbids in
# validators._first_forbidden_proof_language: the prover-round language guard
# scans the (already redacted) content fields, so any phrase the validator
# detects but redaction misses would let a live reviewer/prover sentence (e.g.
# "... is proven irrational") fail the run closed instead of being contained.
# test_forbidden_language_redaction_covers_validator pins this invariant.
FORBIDDEN_LANGUAGE = [
    "proved",
    "proven",
    "proves",
    "verified",
    "certainly",
    "obviously",
    "clearly",
    "complete proof",
    "proof complete",
    "this proof",
    "full proof",
    "proof establishes",
    "establishes the theorem",
    "establishes the claim",
    "no gaps remain",
    "the claim is true",
    "the theorem is established",
    "proof-close",
    # Goal 41: Chinese global completion markers (FORBIDDEN_LANGUAGE must stay
    # a superset of the validator's parse-time gate; the ASCII lookarounds in
    # forbidden_pattern() are satisfied around CJK characters).
    "证毕",
    "证明完毕",
    "证明完成",
    "定理得证",
    "命题得证",
    "猜想得证",
    "定理已证明",
    "命题已证明",
    "我们证明了该定理",
    "我们证明了这个定理",
]
FORBIDDEN_LANGUAGE = list(dict.fromkeys([*FORBIDDEN_LANGUAGE, *PROOF_COMPLETE_PHRASES]))

INPUT_ARTIFACTS = [
    "locked_claim.yaml",
    "hypothesis_ledger.yaml",
    "literature_review.yaml",
    "examples.yaml",
    "computation_checks.yaml",
    "counterexamples.yaml",
    "tests.yaml",
    "source_grounding.yaml",
    "review.yaml",
    "idea_grade.yaml",
    "strategy_brief.yaml",
]

MISSING_CONTEXT_NOTES = {
    "source_support": {
        "source_request": (
            "Missing source context: exact source support is not checked for this route."
        ),
        "risk": (
            "Unchecked citations cannot support a proof candidate; route source use must "
            "stay pending until source grounding validates exact locator and hypotheses."
        ),
    },
    "theorem_applicability": {
        "source_request": (
            "Missing theorem context: theorem applicability is not checked for this route."
        ),
        "risk": (
            "Pending theorem applicability cannot support a proof candidate; route theorem "
            "use must stay conditional until applicability validation passes."
        ),
    },
    "examples_pressure": {
        "source_request": (
            "Missing example context: example pressure is pending or partial."
        ),
        "risk": (
            "Unchecked examples cannot count as survived evidence for a proof candidate."
        ),
    },
    "counterexamples_pressure": {
        "source_request": (
            "Missing counterexample context: counterexample pressure is pending or partial."
        ),
        "risk": (
            "No counterexample found in unchecked pressure is not evidence against refutation."
        ),
    },
}

ESPRESSO_REVIEWER_ROLES = [
    "strategy_reviewer",
    "evidence_reviewer",
    "line_proof_reviewer",
]

CODEX_EXTERNAL_MODEL = "gpt-5.5-medium"
DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 120
DEFAULT_CODEX_EXTERNAL_TIMEOUT_SECONDS = 360
EXTERNAL_AGENT_TIMEOUT_ENV = "ESPRESSO_EXTERNAL_AGENT_TIMEOUT_SECONDS"


class LLMClient(Protocol):
    provider: str
    model: str

    def complete_json(self, *, system: str, prompt: str) -> dict:
        ...


class StubLLMClient:
    provider = "stub"

    def __init__(self, model: str = "stub-espresso-agent") -> None:
        self.model = model

    def complete_json(self, *, system: str, prompt: str) -> dict:
        lowered = system.lower()
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
                    "schema": AGENT_OUTPUT_SCHEMA,
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
    except (ProcessLookupError, PermissionError, OSError):
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
        self.command = shlex.split(raw_command)
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
            "required_json_schema": AGENT_OUTPUT_SCHEMA,
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
    return any(Path(part).name == "espresso_codex_agent.py" for part in command)


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
    command = shlex.split(raw_command) if raw_command else []
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


def _agent_input_artifacts(manifest: dict) -> list[str]:
    inputs = list(INPUT_ARTIFACTS)
    rel = (manifest.get("optional_artifacts") or {}).get("time_allocation")
    if rel and rel not in inputs:
        inputs.append(rel)
    return inputs


def _route_allocation_entry(plan, route_id: str | None) -> dict | None:
    if route_id is None:
        return None
    for entry in plan.entries:
        if entry.route_id == route_id:
            return entry.model_dump(mode="json")
    return None


def _time_allocation_context(run_dir: Path, plan) -> dict:
    payload = time_allocation_prompt_payload(plan)
    theorem_missing = True
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    theorem_rel = (manifest.get("optional_artifacts") or {}).get("theorem_applicability")
    if theorem_rel and (run_dir / theorem_rel).exists():
        try:
            theorem = read_yaml(run_dir / theorem_rel)
            theorem_missing = theorem.get("overall_status") != "applicable"
        except Exception:  # noqa: BLE001 - fail closed for prompt context
            theorem_missing = True
    if theorem_missing and "theorem_applicability" not in payload["missing_context"]:
        payload["missing_context"].append("theorem_applicability")
    return payload


def _append_unique(values: list[str], new_values: list[str]) -> list[str]:
    seen = {value.strip() for value in values}
    out = list(values)
    for value in new_values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _apply_missing_context_notes(prover: dict, time_context: dict) -> None:
    missing = [
        item
        for item in time_context.get("missing_context", [])
        if item in MISSING_CONTEXT_NOTES
    ]
    if not missing:
        return
    source_requests = [
        MISSING_CONTEXT_NOTES[item]["source_request"]
        for item in missing
    ]
    risks = [
        MISSING_CONTEXT_NOTES[item]["risk"]
        for item in missing
    ]
    prover["source_requests"] = _append_unique(prover.get("source_requests", []), source_requests)
    prover["risks"] = _append_unique(prover.get("risks", []), risks)


def run_llm_agent_pass(
    *,
    run_dir: Path,
    provider: str,
    model: str,
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    verifier_count: int = 1,
    reviewer_set: str = "generic",
    provider_diagnostic: dict | None = None,
) -> dict:
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    allocation_plan = ensure_prover_time_allocation_plan(run_dir)
    client = build_client(
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
    )
    context = load_agent_context(run_dir)
    time_context = _time_allocation_context(run_dir, allocation_plan)
    context["time_allocation_plan"] = time_context
    context["prover_missing_context_to_record"] = time_context["missing_context"]
    input_artifacts = _agent_input_artifacts(context["manifest"])
    now = datetime.now(UTC).isoformat()
    prover_raw = client.complete_json(
        system=prover_system_prompt(),
        prompt=agent_prompt(context, role="prover"),
    )
    prover = make_prover_attempt(
        prover_raw,
        client=client,
        created_at=now,
        input_artifacts=input_artifacts,
    )
    _apply_missing_context_notes(prover, time_context)
    verifier_reviews = []
    reviewer_roles = _reviewer_roles(reviewer_set, verifier_count)
    for index, reviewer_role in enumerate(reviewer_roles, start=1):
        verifier_raw = client.complete_json(
            system=verifier_system_prompt(index, reviewer_role=reviewer_role),
            prompt=agent_prompt(
                {**context, "prover_attempt": prover},
                role=reviewer_role,
            ),
        )
        verifier_reviews.append(
            make_verifier_review(
                verifier_raw,
                client=client,
                created_at=datetime.now(UTC).isoformat(),
                index=index,
                reviewer_role=reviewer_role,
            )
        )
    gate = make_human_gate(verifier_reviews)
    report = {
        "schema_version": 1,
        "claim_id": context["claim"]["id"],
        "run_id": context["manifest"]["run_id"],
        "generated_at": now,
        "provider": client.provider,
        "model": client.model,
        "input_artifacts": input_artifacts,
        "prover_attempt": prover,
        "verifier_reviews": verifier_reviews,
        "human_gate": gate,
        "provider_diagnostic": provider_diagnostic,
        "notes": [
            "Initial LLM agent runtime; outputs are candidate planning artifacts only.",
            f"Reviewer set: {reviewer_set}; roles: {', '.join(reviewer_roles)}.",
            "This artifact must not upgrade Idea grade, source_backed, checked evidence, or proof status.",
            "Human gate must be resolved through a validated CLI/API path before deeper automated work.",
        ],
    }
    LLMAgentRunReport.model_validate(report)
    return report


# The ONLY machine-answerable request shape: the generic disjunctive
# "(source or) exact computation for the most dangerous example/case" —
# an ANCHORED FULLMATCH (Kernel Safety + Math Workflow review): a request
# naming any specific object ("exact computation of the Hodge numbers ...")
# never matches and stays open, because claim-scoped evidence does not bind
# to specifics the entries' targets do not cover. Fail-closed false
# negatives are the intended direction.
_ANSWERABLE_COMPUTATION_REQUEST = re.compile(
    r"(?:source\s+or\s+)?exact\s+computation\s+for\s+the\s+most\s+dangerous\s+"
    r"(?:example|case)\s*\.?",
    re.IGNORECASE,
)


def _split_machine_answered_blockers(
    blockers: list[str],
    *,
    run_dir: Path | None,
    claim_id: str,
    source_request_texts: set[str] | None = None,
) -> tuple[list[str], list[dict]]:
    """Split reviewer blockers into (open, machine_answered).

    Goal 42E cycle 3: a blocker is machine-answered ONLY when ALL hold:
    (a) it originated as a reviewer SOURCE REQUEST (provenance by text-set
        membership against the round's ``source_requests``; objections and
        missing hypotheses are never answerable regardless of phrasing);
    (b) its WHOLE text fullmatches the generic disjunctive
        "(source or) exact computation for the most dangerous example" form
        — requests naming specific objects stay open;
    (c) this run carries trusted, confirmed machine-check entries for THIS
        locked claim (instance_checked / computation_checked in
        proof_verification.yaml).
    Each answer records WHICH entries discharged WHICH request text.
    Resolution is bookkeeping over reviewer requests; it never touches any
    proof/source/evidence/grade status."""

    if run_dir is None or not blockers:
        return blockers, []
    pv_path = Path(run_dir) / "proof_verification.yaml"
    if not pv_path.exists():
        return blockers, []
    try:
        entries = read_yaml(pv_path).get("entries", [])
    except Exception:  # noqa: BLE001 - fail closed: unreadable evidence answers nothing
        return blockers, []
    confirmed = [
        e for e in entries
        if e.get("trusted") and e.get("confirmed")
        and e.get("earned_status") in {"instance_checked", "computation_checked"}
        and e.get("claim_id") == claim_id
    ]
    if not confirmed:
        return blockers, []
    requests = {str(t).strip() for t in (source_request_texts or set())}
    answered: list[dict] = []
    open_blockers: list[str] = []
    for blocker in blockers:
        text = str(blocker).strip()
        if text in requests and _ANSWERABLE_COMPUTATION_REQUEST.fullmatch(
            " ".join(text.split())
        ):
            ids = [str(e.get("id", "")) for e in confirmed]
            if len(ids) > 8:
                ids = ids[:8] + [f"+{len(ids) - 8} more"]
            answered.append({"request": text, "entry_ids": ids})
        else:
            open_blockers.append(blocker)
    if not answered:
        return blockers, []
    return open_blockers, answered


def _machine_answer_note(answered: list[dict]) -> str:
    parts = "; ".join(
        f"{item['request']!r} answered by trusted machine check(s) "
        f"[{', '.join(item['entry_ids'])}]"
        for item in answered
    )
    note = (
        f" Machine-answered reviewer request(s): {parts} "
        "(see proof_verification.yaml; the evidence is scoped, never a "
        "universal proof)."
    )
    return note if len(note) <= 600 else note[:597] + "..."


def build_prover_loop_report(
    *,
    agent_report: dict,
    strategy_brief: dict,
    human_choice: dict | None = None,
    approved_effort: str = "normal_pass",
    run_dir: Path | None = None,
) -> dict:
    allocation_plan = ensure_prover_time_allocation_plan(run_dir) if run_dir is not None else None
    top_strategies = strategy_brief.get("top_strategies", [])[:3]
    if not top_strategies:
        top_strategies = [
            {
                "id": "strategy_missing_brief",
                "title": "No strategy brief route available",
            }
        ]
    verifier_reviews = agent_report.get("verifier_reviews", [])
    reviewer_pointers = [_review_pointer(review) for review in verifier_reviews]
    blockers = sorted(
        {
            item
            for review in verifier_reviews
            for item in (
                review.get("objections", [])
                + review.get("missing_hypotheses", [])
                + review.get("source_requests", [])
            )
            if str(item).strip()
        }
    )
    blockers, machine_answered = _split_machine_answered_blockers(
        blockers,
        run_dir=run_dir,
        claim_id=agent_report["claim_id"],
        source_request_texts={
            str(item).strip()
            for review in verifier_reviews
            for item in review.get("source_requests", [])
        },
    )
    blockers = blockers[:12]
    gate = agent_report["human_gate"]
    selected_strategy = _selected_strategy_from_choice(human_choice, top_strategies)
    approved_effort = _approved_effort_from_choice(human_choice, approved_effort)
    attempts = []
    for index, strategy in enumerate(top_strategies, start=1):
        strategy_id = strategy.get("id", f"strategy_{index}")
        allocation = _allocation_for_strategy(
            strategy_id=strategy_id,
            selected_strategy_id=selected_strategy,
            gate=gate,
            reviewer_reviews=verifier_reviews,
            allocation_entry=(
                _route_allocation_entry(allocation_plan, strategy_id)
                if allocation_plan is not None
                else None
            ),
        )
        status = _attempt_status_for_allocation(allocation, blockers)
        attempts.append(
            {
                "attempt_id": f"attempt_round_001_{strategy_id}",
                "round_index": 1,
                "strategy_id": strategy_id,
                "parent_attempt_id": None,
                "prover_attempt_id": agent_report["prover_attempt"]["agent_id"],
                "source_request_count": len(agent_report["prover_attempt"].get("source_requests", [])),
                "risk_count": len(agent_report["prover_attempt"].get("risks", [])),
                "reviewer_ids": [review["verifier_id"] for review in verifier_reviews],
                "reviewer_objections": reviewer_pointers,
                "time_allocation": allocation,
                "status": status,
                "blockers": blockers,
                "next_action": _next_action_for_strategy(
                    index=index,
                    strategy=strategy,
                    gate=gate,
                    blockers=blockers,
                )
                + (_machine_answer_note(machine_answered) if machine_answered else ""),
            }
        )
    decision = "ask_human" if gate["status"] == "pending" else _decision_from_gate(gate)
    report = {
        "schema_version": 1,
        "claim_id": agent_report["claim_id"],
        "run_id": agent_report["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "source_llm_agent_run": "llm_agent_run.yaml",
        "selected_strategy_id": selected_strategy,
        "budget": {
            "approved_effort": approved_effort,
            "max_rounds": _max_rounds_for_effort(approved_effort),
            "rounds_used": 1,
            "max_agent_calls": _max_agent_calls_for_effort(approved_effort),
            "agent_calls_used": 1 + len(verifier_reviews),
            "stop_if_human_gate_pending": True,
        },
        "attempts": attempts,
        "human_gate_status": gate["status"],
        "loop_decision": decision,
        "decision_reason": _loop_decision_reason(
            gate, blockers, machine_answered=machine_answered
        ),
        "audit_note": (
            "Prover-loop scaffold: records attempt portfolio, reviewer objections, "
            "time allocation, and human gate without upgrading proof/source/evidence status."
        ),
    }
    ProverLoopReport.model_validate(report)
    return report


def write_initial_prover_round_artifacts(
    *,
    run_dir: Path,
    agent_report: dict,
    loop_report: dict,
) -> dict:
    """Write durable round artifacts for the initial prover/reviewer pass."""
    for attempt in loop_report.get("attempts", []):
        if attempt.get("round_index") != 1:
            continue
        _write_prover_round_artifact(
            run_dir=run_dir,
            agent_report=agent_report,
            loop_report=loop_report,
            attempt=attempt,
            prover_attempt=agent_report["prover_attempt"],
            verifier_reviews=agent_report["verifier_reviews"],
            source="initial_llm_agent_run",
        )
    return refresh_prover_round_index(run_dir=run_dir, loop_report=loop_report)


def refresh_prover_round_index(*, run_dir: Path, loop_report: dict) -> dict:
    rounds_dir = run_dir / "prover_rounds"
    entries = []
    for attempt in loop_report.get("attempts", []):
        artifact_path = _round_artifact_rel_path(attempt)
        path = run_dir / artifact_path
        if not path.exists():
            continue
        round_report = ProverRoundReport.model_validate(read_yaml(path))
        entries.append(
            {
                "round_index": round_report.round_index,
                "strategy_id": round_report.strategy_id,
                "loop_attempt_id": round_report.loop_attempt_id,
                "artifact_path": artifact_path,
                "status": round_report.status,
                "time_allocation": round_report.time_allocation,
            }
        )
    index = {
        "schema_version": 1,
        "claim_id": loop_report["claim_id"],
        "run_id": loop_report["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "source_prover_loop": "prover_loop.yaml",
        "rounds": sorted(entries, key=lambda item: (item["round_index"], item["loop_attempt_id"])),
    }
    ProverRoundIndex.model_validate(index)
    rounds_dir.mkdir(exist_ok=True)
    write_yaml(run_dir / "prover_rounds" / "index.yaml", index)
    return index


def _write_prover_round_artifact(
    *,
    run_dir: Path,
    agent_report: dict,
    loop_report: dict,
    attempt: dict,
    prover_attempt: dict,
    verifier_reviews: list[dict],
    source: str,
) -> dict:
    report = {
        "schema_version": 1,
        "claim_id": loop_report["claim_id"],
        "run_id": loop_report["run_id"],
        "round_index": attempt["round_index"],
        "generated_at": datetime.now(UTC).isoformat(),
        "source": source,
        "strategy_id": attempt["strategy_id"],
        "loop_attempt_id": attempt["attempt_id"],
        "parent_attempt_id": attempt.get("parent_attempt_id"),
        "input_artifacts": agent_report.get("input_artifacts", INPUT_ARTIFACTS),
        "context_summary": (
            "Durable prover/reviewer round artifact. It records the candidate prover output, "
            "the independent reviewer outputs, and the loop allocation for this strategy."
        ),
        "prover_attempt": prover_attempt,
        "verifier_reviews": verifier_reviews,
        "reviewer_blockers": attempt.get("blockers", []),
        "human_gate_status": loop_report["human_gate_status"],
        "time_allocation": attempt["time_allocation"],
        "status": attempt["status"],
        "next_action": attempt["next_action"],
        "safety_note": (
            "This round artifact does not upgrade proof status, source_backed status, "
            "checked evidence, or human preference into verification."
        ),
    }
    ProverRoundReport.model_validate(report)
    path = run_dir / _round_artifact_rel_path(attempt)
    path.parent.mkdir(exist_ok=True)
    write_yaml(path, report)
    return report


def _round_artifact_rel_path(attempt: dict) -> str:
    strategy = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(attempt.get("strategy_id", "strategy")))
    attempt_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(attempt.get("attempt_id", "attempt")))
    return f"prover_rounds/round_{int(attempt['round_index']):03d}_{attempt_id}_{strategy}.yaml"


def _reviewer_blocker_slots(
    review: dict, open_blocker_texts: set[str] | None
) -> list[tuple[str, int]]:
    slots: list[tuple[str, int]] = []
    for field in ("objections", "missing_hypotheses", "source_requests"):
        for index, item in enumerate(review.get(field, []) or [], start=1):
            text = str(item).strip()
            if not text:
                continue
            if open_blocker_texts is not None and text not in open_blocker_texts:
                continue
            slots.append((field, index))
    return slots


def _append_reviewer_objection_events(
    *,
    run_dir: Path,
    claim_id: str,
    route_id: str,
    origin_stage: str,
    origin_artifact: str,
    event_id_prefix: str,
    verifier_reviews: list[dict],
    open_blocker_texts: set[str] | None = None,
) -> bool:
    """Route reviewer blockers into Update Inbox as scheduling-only pointers.

    The event records provenance to the validated origin artifact, not blocker
    prose. Reviewer text remains owned by the prover-round artifact and its
    language/status-smuggling validators.
    """

    artifact_hash = artifact_revision_hash(run_dir, origin_artifact)
    created_any = False
    event_index = 0
    for review in verifier_reviews:
        slots = _reviewer_blocker_slots(review, open_blocker_texts)
        if not slots:
            continue
        verifier_id = str(review.get("verifier_id", "reviewer"))
        safe_verifier = re.sub(r"[^a-zA-Z0-9_.-]+", "_", verifier_id)
        for field, blocker_index in slots:
            event_index += 1
            append_update_inbox_event(
                run_dir,
                {
                    "id": f"{event_id_prefix}_{event_index:03d}",
                    "type": "reviewer_objection",
                    "origin_stage": origin_stage,
                    "origin_artifact": origin_artifact,
                    "origin_artifact_id_or_path": (
                        f"{safe_verifier}:{field}:{blocker_index:03d}"
                    ),
                    "locked_claim_id": claim_id,
                    "route_id_or_step_id": route_id,
                    "artifact_revision_or_hash": artifact_hash,
                    "validator_ok": True,
                    "created_at": datetime.now(UTC).isoformat(),
                    "dedupe_key": "canonicalized-by-writer",
                    "applied_at": None,
                    "effect": "scheduling_only",
                    "advisory_only": True,
                },
            )
            created_any = True
    return created_any


def route_initial_reviewer_blockers_to_update_inbox(
    *, run_dir: Path, agent_report: dict, selected_strategy_id: str | None
) -> bool:
    """Persist initial reviewer blocker pointers before building resolved loop state."""

    if not selected_strategy_id or selected_strategy_id == "none":
        return False
    verifier_reviews = agent_report.get("verifier_reviews", [])
    blockers = sorted(
        {
            item
            for review in verifier_reviews
            for item in (
                review.get("objections", [])
                + review.get("missing_hypotheses", [])
                + review.get("source_requests", [])
            )
            if str(item).strip()
        }
    )
    blockers, _ = _split_machine_answered_blockers(
        blockers,
        run_dir=run_dir,
        claim_id=agent_report["claim_id"],
        source_request_texts={
            str(item).strip()
            for review in verifier_reviews
            for item in review.get("source_requests", [])
        },
    )
    created = _append_reviewer_objection_events(
        run_dir=run_dir,
        claim_id=agent_report["claim_id"],
        route_id=str(selected_strategy_id),
        origin_stage="llm_prover_reviewers",
        origin_artifact="llm_agent_run.yaml",
        event_id_prefix="upd_reviewer_objection_initial",
        verifier_reviews=verifier_reviews,
        open_blocker_texts=set(blockers[:12]),
    )
    if created:
        write_time_allocation_plan(run_dir)
    return created


def _human_feedback_advisory(run_dir: Path) -> str:
    """The human's advisory ranking note for this run (human_choice.taste_judgment).

    Pure read, fail-closed to empty. This is advisory prompt context only and
    carries no evidence; the kernel re-validates every round output."""

    try:
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        rel = manifest.get("artifacts", {}).get("human_choice")
        if not rel or not (run_dir / rel).exists():
            return ""
        return str(read_yaml(run_dir / rel).get("taste_judgment") or "")[:2000]
    except Exception:  # noqa: BLE001
        return ""


def _selected_strategy_from_choice(human_choice: dict | None, strategies: list[dict]) -> str | None:
    strategy_ids = [strategy.get("id") for strategy in strategies]
    if human_choice and human_choice.get("selected_strategy") == "none":
        return None
    if (
        human_choice
        and human_choice.get("status") == "selected"
        and human_choice.get("selected_strategy") in strategy_ids
    ):
        return human_choice["selected_strategy"]
    return strategy_ids[0] if strategy_ids else None


def _approved_effort_from_choice(human_choice: dict | None, fallback: str) -> str:
    if human_choice and human_choice.get("status") == "selected" and human_choice.get("effort"):
        return str(human_choice["effort"])
    return fallback


def _allocation_for_strategy(
    *,
    strategy_id: str,
    selected_strategy_id: str | None,
    gate: dict,
    reviewer_reviews: list[dict],
    allocation_entry: dict | None = None,
) -> str:
    if gate.get("status") == "pending":
        return "ask_human"
    if gate.get("decision") == "stop":
        return "stop"
    is_selected = strategy_id == selected_strategy_id
    if allocation_entry is not None:
        action = allocation_entry.get("action")
        if action == "stop":
            return "stop"
        if action == "ask_human":
            return "ask_human"
        if action in {"source_pinning", "repair_attempt"}:
            return "repair" if is_selected else "cheap_check"
        if action == "cheap_check":
            return "cheap_check"
        if action == "bounded_proof_exploration":
            return "expand" if is_selected else "cheap_check"
    if any(review.get("verdict") == "blocked" for review in reviewer_reviews):
        return "pause"
    if any(review.get("verdict") in {"needs_revision", "needs_sources"} for review in reviewer_reviews):
        return "repair" if is_selected else "cheap_check"
    return "expand" if is_selected else "cheap_check"


def _attempt_status_for_allocation(allocation: str, blockers: list[str]) -> str:
    if allocation == "stop":
        return "stopped"
    if allocation in {"pause"}:
        return "blocked"
    if allocation == "ask_human":
        return "waiting_human"
    if blockers:
        return "needs_repair"
    # repair allocation with zero OPEN blockers (e.g. the only recorded
    # request was machine-answered): nothing is repairable — the attempt is
    # an honest candidate; the kernel model rejects needs_repair without
    # blockers, so this is also the fail-closed branch.
    return "candidate"


def _next_action_for_strategy(*, index: int, strategy: dict, gate: dict, blockers: list[str]) -> str:
    title = strategy.get("title") or strategy.get("id") or f"strategy {index}"
    if gate.get("status") == "pending":
        if index == 1:
            return f"Ask mathematician whether to spend the next round on {title}."
        return f"Keep {title} as a low-budget alternative until human preference is resolved."
    if gate.get("decision") == "stop":
        return f"Stop before spending another prover round on {title}."
    if blockers:
        return f"Repair {title} against reviewer blockers before expanding proof text."
    return f"Expand {title} with source matching and checked examples."


def _decision_from_gate(gate: dict) -> str:
    decision = gate.get("decision")
    if decision == "continue":
        return "continue"
    if decision == "stop":
        return "stop"
    return "repair"


def _loop_decision_reason(
    gate: dict, blockers: list[str], *, machine_answered: list[dict] | None = None
) -> str:
    if gate.get("status") == "pending":
        return "Human gate is pending; Espresso must not run deeper prover rounds yet."
    if gate.get("decision") == "stop":
        return "Human gate resolved to stop."
    if blockers:
        return "Reviewer objections or source requests require repair before another proof round."
    if machine_answered:
        return (
            "Human gate resolved; recorded reviewer computation request(s) were "
            "machine-answered by trusted scoped checks (see attempt next_action "
            "and proof_verification.yaml)."
        )
    return "Human gate resolved and no blocking reviewer objection was recorded."


def _max_rounds_for_effort(effort: str) -> int:
    return {
        "quick_test": 1,
        "normal_pass": 2,
        "deep_exploration": 4,
        "long_watch": 8,
    }.get(effort, 2)


def _max_agent_calls_for_effort(effort: str) -> int:
    return {
        "quick_test": 4,
        "normal_pass": 8,
        "deep_exploration": 20,
        "long_watch": 50,
    }.get(effort, 8)


def resolve_agent_gate(
    *,
    run_dir: Path,
    decision: str,
    reason: str,
) -> dict:
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    agent_path = manifest.get("optional_artifacts", {}).get("llm_agent_run")
    if not agent_path:
        raise RuntimeError("run has no llm_agent_run optional artifact")
    report = read_yaml(run_dir / agent_path)
    gate = report["human_gate"]
    if gate.get("status") == "resolved":
        raise RuntimeError("human gate is already resolved")
    if decision not in gate["allowed_decisions"]:
        raise RuntimeError(f"decision {decision!r} is not allowed for this gate")
    gate.update(
        {
            "status": "resolved",
            "decision": decision,
            "reason": reason,
            "human_confirmed": True,
            "resolved_at": datetime.now(UTC).isoformat(),
        }
    )
    LLMAgentRunReport.model_validate(report)
    write_yaml(run_dir / agent_path, report)
    return report


def append_prover_loop_round(
    *,
    run_dir: Path,
    provider: str,
    model: str,
    api_key_env: str = "OPENAI_API_KEY",
    external_agent_command_env: str = "ESPRESSO_EXTERNAL_AGENT_COMMAND",
    reviewer_set: str = "espresso",
    client: "LLMClient | None" = None,
) -> dict:
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    allocation_plan = ensure_prover_time_allocation_plan(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    optional = manifest.get("optional_artifacts", {})
    agent_path = optional.get("llm_agent_run")
    loop_path = optional.get("prover_loop")
    if not agent_path or not loop_path:
        raise RuntimeError("run requires llm_agent_run.yaml and prover_loop.yaml")
    agent_report = read_yaml(run_dir / agent_path)
    loop_report = read_yaml(run_dir / loop_path)
    if agent_report["human_gate"]["status"] != "resolved":
        raise RuntimeError("cannot run another prover round before resolving the human gate")
    if loop_report.get("loop_decision") in {"ask_human", "stop"}:
        raise RuntimeError(f"cannot append round when loop_decision={loop_report.get('loop_decision')}")

    attempts = loop_report["attempts"]
    if loop_report["budget"]["rounds_used"] >= loop_report["budget"]["max_rounds"]:
        raise RuntimeError("prover loop budget has no remaining rounds")
    selected_strategy_id = loop_report.get("selected_strategy_id")
    parent_attempt = _select_parent_attempt(attempts, selected_strategy_id)
    next_round = max(attempt["round_index"] for attempt in attempts) + 1

    client = client or build_client(
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
    )
    context = load_agent_context(run_dir)
    time_context = _time_allocation_context(run_dir, allocation_plan)
    input_artifacts = _agent_input_artifacts(manifest)
    # Goal 26: the subgoal graph is the scheduling center. Pick the highest-value
    # OPEN subgoal and NAME it to the prover so the round works on that specific
    # subgoal id + local claim (not the whole claim). This is scheduling only: it
    # never sets/upgrades any status, and a contradicted (refuted) main goal
    # yields no target (stale optimism cannot override a checked refutation).
    from .subgoal_graph import assemble_subgoal_graph, select_next_subgoal

    target_node = select_next_subgoal(assemble_subgoal_graph(run_dir))
    target_subgoal = (
        {
            "id": target_node.get("id"),
            "local_claim": target_node.get("statement"),
            "state": target_node.get("state"),
            "instruction": (
                "Work on THIS subgoal only; cite its id in your steps. Produce "
                "candidate work for this local claim. Do not claim the whole claim "
                "is proved. Status upgrades require the kernel's hard gates."
            ),
        }
        if target_node is not None
        else {
            "id": None,
            "local_claim": None,
            "state": None,
            "instruction": (
                "No open subgoal is selectable (the claim may be refuted or every "
                "subgoal is already resolved/blocked). Do not invent progress."
            ),
        }
    )
    round_context = {
        **context,
        "previous_prover_loop_attempt": parent_attempt,
        "reviewer_blockers_to_repair": parent_attempt.get("blockers", []),
        "loop_decision": loop_report.get("loop_decision"),
        "time_allocation": parent_attempt.get("time_allocation"),
        "time_allocation_plan": time_context,
        "time_allocation_entry": _route_allocation_entry(
            allocation_plan, parent_attempt.get("strategy_id")
        ),
        "prover_missing_context_to_record": time_context["missing_context"],
        "target_subgoal": target_subgoal,
        # Goal 43C: the human's advisory ranking/time-allocation note for this
        # continuation (from human_choice.taste_judgment). It is prompt context
        # ONLY — the round's outputs are re-validated by the kernel, so this can
        # never upgrade proof/source/evidence/grade/claim status.
        "human_feedback_advisory": _human_feedback_advisory(run_dir),
        # Goal 54C: structured human strategy feedback (score/priority/note) +
        # the derived advisory allocation hint, as prompt context. Advisory only.
        "human_time_allocation": time_allocation_prompt_context(run_dir, selected_strategy_id),
    }
    created_at = datetime.now(UTC).isoformat()
    prover_raw = client.complete_json(
        system=prover_system_prompt(),
        prompt=agent_prompt(round_context, role="prover_loop_round"),
    )
    prover = make_prover_attempt(
        prover_raw,
        client=client,
        created_at=created_at,
        input_artifacts=input_artifacts,
    )
    prover["agent_id"] = f"llm_prover_{next_round:03d}"
    _apply_missing_context_notes(prover, time_context)

    reviewer_roles = _reviewer_roles(reviewer_set, 3)
    verifier_reviews = []
    for index, reviewer_role in enumerate(reviewer_roles, start=1):
        verifier_raw = client.complete_json(
            system=verifier_system_prompt(index, reviewer_role=reviewer_role),
            prompt=agent_prompt(
                {**round_context, "prover_attempt": prover},
                role=f"round_{next_round}_{reviewer_role}",
            ),
        )
        review = make_verifier_review(
            verifier_raw,
            client=client,
            created_at=datetime.now(UTC).isoformat(),
            index=index,
            reviewer_role=reviewer_role,
            target_subgoal_id=target_subgoal.get("id"),  # Goal 54B: bind objections to the attacked subgoal
        )
        review["verifier_id"] = f"round_{next_round:03d}_{review['verifier_id']}"
        verifier_reviews.append(review)

    blockers = sorted(
        {
            item
            for review in verifier_reviews
            for item in (
                review.get("objections", [])
                + review.get("missing_hypotheses", [])
                + review.get("source_requests", [])
            )
            if str(item).strip()
        }
    )
    blockers, machine_answered = _split_machine_answered_blockers(
        blockers,
        run_dir=run_dir,
        claim_id=loop_report["claim_id"],
        source_request_texts={
            str(item).strip()
            for review in verifier_reviews
            for item in review.get("source_requests", [])
        },
    )
    blockers = blockers[:12]
    reviewer_pointers = [_review_pointer(review) for review in verifier_reviews]
    selected_strategy_id = parent_attempt["strategy_id"]
    # Goal 54C: the selected route's human priority deterministically sets the
    # allocation. Reviewer blockers always force 'repair' (objections outrank
    # taste); otherwise focus/normal -> expand, repair -> repair, reduce ->
    # cheap_check unless it is the only valid route. Scheduling only — it never
    # sets or upgrades any proof/source/evidence/grade/claim status. The advisory
    # context sidecar is (re)written so the dashboard + replay can audit it.
    allocation_context = write_time_allocation_context(run_dir)
    allocation = recommended_allocation_for_priority(
        priority_for_strategy(allocation_context, selected_strategy_id),
        has_blockers=bool(blockers),
        only_valid_route=_only_valid_route(allocation_context),
    )
    new_attempt = {
        "attempt_id": f"attempt_round_{next_round:03d}_{selected_strategy_id}",
        "round_index": next_round,
        "strategy_id": selected_strategy_id,
        "parent_attempt_id": parent_attempt["attempt_id"],
        "prover_attempt_id": prover["agent_id"],
        "source_request_count": len(prover.get("source_requests", [])),
        "risk_count": len(prover.get("risks", [])),
        "reviewer_ids": [review["verifier_id"] for review in verifier_reviews],
        "reviewer_objections": reviewer_pointers,
        "time_allocation": allocation,
        "status": "needs_repair" if blockers else "candidate",
        "blockers": blockers,
        "next_action": (
            "Repair reviewer blockers before another proof round."
            if blockers
            else "Expand this candidate with source matching and checked examples."
        )
        + (_machine_answer_note(machine_answered) if machine_answered else ""),
        # Goal 39: persist the scheduler's pick so the round is durably bound to
        # a named subgoal (prompt context alone is not auditable). None when no
        # subgoal was selectable. Never earns or upgrades any status.
        "target_subgoal_id": target_subgoal.get("id"),
        "target_local_claim": target_subgoal.get("local_claim") or "",
    }
    loop_report["attempts"].append(new_attempt)
    loop_report["generated_at"] = datetime.now(UTC).isoformat()
    loop_report["budget"]["rounds_used"] = len(
        {attempt["round_index"] for attempt in loop_report["attempts"]}
    )
    loop_report["budget"]["agent_calls_used"] += 1 + len(verifier_reviews)
    loop_report["loop_decision"] = "repair" if blockers else "continue"
    loop_report["decision_reason"] = (
        "Round appended from resolved human gate; reviewer blockers were routed into the prover prompt."
        + (
            " Recorded computation request(s) were machine-answered by trusted scoped checks (see attempt next_action)."
            if machine_answered
            else ""
        )
    )
    ProverLoopReport.model_validate(loop_report)
    write_yaml(run_dir / loop_path, loop_report)
    if not (run_dir / "prover_rounds" / "index.yaml").exists():
        write_initial_prover_round_artifacts(
            run_dir=run_dir,
            agent_report=agent_report,
            loop_report=loop_report,
        )
    _write_prover_round_artifact(
        run_dir=run_dir,
        agent_report={**agent_report, "input_artifacts": input_artifacts},
        loop_report=loop_report,
        attempt=new_attempt,
        prover_attempt=prover,
        verifier_reviews=verifier_reviews,
        source="appended_prover_loop_round",
    )
    refresh_prover_round_index(run_dir=run_dir, loop_report=loop_report)
    manifest.setdefault("optional_artifacts", {})["prover_round_index"] = "prover_rounds/index.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    if _append_reviewer_objection_events(
        run_dir=run_dir,
        claim_id=loop_report["claim_id"],
        route_id=str(new_attempt["strategy_id"]),
        origin_stage="prover_loop_round",
        origin_artifact=_round_artifact_rel_path(new_attempt),
        event_id_prefix=f"upd_reviewer_objection_r{int(new_attempt['round_index']):03d}",
        verifier_reviews=verifier_reviews,
        open_blocker_texts=set(blockers),
    ):
        write_time_allocation_plan(run_dir)
    return loop_report


def _select_parent_attempt(attempts: list[dict], selected_strategy_id: str | None) -> dict:
    candidates = [
        attempt
        for attempt in attempts
        if selected_strategy_id is None or attempt.get("strategy_id") == selected_strategy_id
    ]
    if not candidates:
        raise RuntimeError("selected strategy has no prover-loop attempt")
    return sorted(candidates, key=lambda item: item["round_index"])[-1]


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


def load_agent_context(run_dir: Path) -> dict:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    return {
        "manifest": manifest,
        "claim": read_yaml(run_dir / artifacts["locked_claim"]),
        "hypothesis_ledger": read_yaml(run_dir / artifacts["hypothesis_ledger"]),
        "literature_review": read_yaml(run_dir / artifacts["literature_review"]),
        # Bankable artifacts may be a single YAML file or a per-item bank dir;
        # load them through the manifest + bank-aware loader.
        "examples": load_bankable_dict(run_dir, "examples", manifest),
        "computation_checks": read_yaml(run_dir / artifacts["computation_checks"]),
        "counterexamples": load_bankable_dict(run_dir, "counterexamples", manifest),
        "tests": read_yaml(run_dir / artifacts["tests"]),
        "source_grounding": read_yaml(run_dir / artifacts["source_grounding"]),
        "review": read_yaml(run_dir / artifacts["review"]),
        "idea_grade": read_yaml(run_dir / artifacts["idea_grade"]),
        "strategy_brief": read_yaml(run_dir / artifacts["strategy_brief"]),
    }


def make_prover_attempt(
    raw: dict,
    *,
    client: LLMClient,
    created_at: str,
    input_artifacts: list[str] | None = None,
) -> dict:
    return {
        "agent_id": "llm_prover_001",
        "role": "prover",
        "provider": client.provider,
        "model": client.model,
        "created_at": created_at,
        "input_artifacts": list(input_artifacts or INPUT_ARTIFACTS),
        "output_summary": safe_text(raw.get("summary", "")),
        "proposed_steps": safe_list(raw.get("steps", [])),
        "source_requests": safe_list(raw.get("source_requests", [])),
        "risks": safe_list(raw.get("risks", [])),
        "status": "needs_human",
    }


_OBJECTION_KINDS = {
    "missing_source", "hypothesis_gap", "definition_gap", "logical_gap",
    "computation_needed", "counterexample_pressure", "unclear_claim",
    "reviewer_disagreement", "other",
}
_OBJECTION_SEVERITIES = {"P0", "P1", "P2", "P3"}


def _pointer_scope_from_review(review: dict) -> tuple[str, str | None]:
    """Goal 54B: derive a ProverLoopReviewPointer's (scope, subgoal_id) from the
    review's scoped objections. Only when every subgoal-scoped objection names the
    SAME subgoal does the pointer claim that subgoal; otherwise it stays 'route'
    (honest default — provenance only, never reconciled against the renumbering graph)."""
    sos = review.get("scoped_objections") or []
    subgoal_ids = {
        s.get("subgoal_id") for s in sos
        if isinstance(s, dict) and s.get("scope") == "subgoal" and s.get("subgoal_id")
    }
    if len(subgoal_ids) == 1:
        return "subgoal", next(iter(subgoal_ids))
    return "route", None


def _review_pointer(review: dict) -> dict:
    """Goal 54B: build one ProverLoopReviewPointer dict, carrying the scope/subgoal_id
    derived from the review's scoped objections (advisory provenance)."""
    scope, subgoal_id = _pointer_scope_from_review(review)
    return {
        "verifier_id": review["verifier_id"],
        "verdict": review["verdict"],
        "objection_count": len(review.get("objections", [])),
        "recommended_human_action": review["recommended_human_action"],
        "scope": scope,
        "subgoal_id": subgoal_id,
    }


def _normalize_scoped_objections(
    raw: dict, *, verifier_id: str, target_subgoal_id: str | None
) -> list[dict]:
    """Goal 54B: structured scoped objections for one reviewer review (advisory only).

    1. Explicit well-formed reviewer ``scoped_objections`` (a subgoal/proof_step scope
       must name its target or it is downgraded to ``route`` — never falsely specific).
    2. Otherwise legacy free-text ``risks`` -> objections; bound to this round's target
       as ``inferred_target`` (honestly marked + reasoned) when a subgoal was attacked,
       else a plain ``route`` ``legacy_fallback``.
    Never raises; malformed entries are dropped.
    """

    def _coerce_explicit(entry: dict, idx: int) -> dict | None:
        if not isinstance(entry, dict):
            return None
        text = str(entry.get("text", "")).strip()
        if not text:
            return None
        scope = entry.get("scope")
        if scope not in {"subgoal", "proof_step", "route", "global"}:
            scope = "route"
        subgoal_id = entry.get("subgoal_id")
        proof_step_id = entry.get("proof_step_id")
        if scope == "subgoal" and not subgoal_id:
            scope = "route"
        if scope == "proof_step" and not proof_step_id:
            scope = "route"
        if scope != "subgoal":
            subgoal_id = None
        if scope != "proof_step":
            proof_step_id = None
        return {
            "id": f"{verifier_id}_obj_{idx:03d}",
            "text": text[:600],
            "scope": scope,
            "scope_source": "explicit",
            "scope_inferred": False,
            "inference_reason": "",
            "subgoal_id": subgoal_id,
            "proof_step_id": proof_step_id,
            "kind": entry.get("kind") if entry.get("kind") in _OBJECTION_KINDS else "other",
            "severity": entry.get("severity") if entry.get("severity") in _OBJECTION_SEVERITIES else "P2",
            "suggested_next_action": str(entry.get("suggested_next_action", ""))[:300],
        }

    explicit = raw.get("scoped_objections")
    if isinstance(explicit, list) and explicit:
        return [c for i, e in enumerate(explicit) if (c := _coerce_explicit(e, i)) is not None]

    out: list[dict] = []
    for i, text in enumerate(safe_list(raw.get("risks", []))):
        text = str(text).strip()
        if not text:
            continue
        if target_subgoal_id:
            out.append({
                "id": f"{verifier_id}_obj_{i:03d}", "text": text[:600], "scope": "subgoal",
                "scope_source": "inferred_target", "scope_inferred": True,
                "inference_reason": (
                    "reviewer objection on the round's attacked subgoal; no explicit scope "
                    "was given, bound to the prover round's target_subgoal_id"
                ),
                "subgoal_id": target_subgoal_id, "proof_step_id": None,
                "kind": "other", "severity": "P2", "suggested_next_action": "",
            })
        else:
            out.append({
                "id": f"{verifier_id}_obj_{i:03d}", "text": text[:600], "scope": "route",
                "scope_source": "legacy_fallback", "scope_inferred": False, "inference_reason": "",
                "subgoal_id": None, "proof_step_id": None,
                "kind": "other", "severity": "P2", "suggested_next_action": "",
            })
    return out


def make_verifier_review(
    raw: dict,
    *,
    client: LLMClient,
    created_at: str,
    index: int,
    reviewer_role: str,
    target_subgoal_id: str | None = None,
) -> dict:
    unsafe_language = collect_unsafe_language(raw)
    verdict = normalize_verdict(raw.get("verdict", "needs_revision"))
    action = normalize_action(raw.get("recommended_human_action", "revise"))
    verifier_id = f"{reviewer_role}_{index:03d}"
    return {
        "verifier_id": verifier_id,
        "provider": client.provider,
        "model": client.model,
        "created_at": created_at,
        "verdict": verdict,
        "objections": safe_list(raw.get("risks", [])),
        "missing_hypotheses": safe_list(raw.get("missing_hypotheses", [])),
        "source_requests": safe_list(raw.get("source_requests", [])),
        "unsafe_language": unsafe_language,
        "scoped_objections": _normalize_scoped_objections(
            raw, verifier_id=verifier_id, target_subgoal_id=target_subgoal_id
        ),
        "recommended_human_action": action,
        "status": "needs_human" if verdict != "blocked" else "blocked",
    }


def make_human_gate(verifier_reviews: list[dict]) -> dict:
    recommended = "revise"
    if any(review["recommended_human_action"] == "stop" for review in verifier_reviews):
        recommended = "stop"
    elif all(review["recommended_human_action"] == "continue" for review in verifier_reviews):
        recommended = "continue"
    return {
        "gate_id": "human_gate_001",
        "status": "pending",
        "question": (
            "Review the prover route and verifier objections. Choose whether to continue, "
            "revise the claim/route, or stop before any deeper automated work."
        ),
        "allowed_decisions": ["continue", "revise", "stop"],
        "recommended_decision": recommended,
        "decision": None,
        "reason": None,
        "human_confirmed": False,
        "created_at": datetime.now(UTC).isoformat(),
        "resolved_at": None,
    }


def prover_system_prompt() -> str:
    return (
        "You are Espresso's prover agent. Propose candidate proof routes only. "
        "Do not claim the theorem is proved, verified, complete, or true. "
        "Every route must list source requests and risks. Return JSON only."
    )


def verifier_system_prompt(index: int, *, reviewer_role: str = "verifier") -> str:
    role_guidance = {
        "strategy_reviewer": (
            "You are Espresso strategy reviewer agent. Assess theorem drift, strategic soundness, "
            "missing reductions, and analogy risks."
        ),
        "evidence_reviewer": (
            "You are Espresso evidence reviewer agent. Assess example coverage, counterexample pressure, "
            "and whether checked/source_checked evidence is being used honestly."
        ),
        "line_proof_reviewer": (
            "You are Espresso line proof reviewer agent. Parse proof-sketch steps, locate source gaps, "
            "and flag external theorem applicability risks."
        ),
        "verifier": f"You are Espresso verifier agent {index}. Critique the prover route.",
    }.get(reviewer_role, f"You are Espresso verifier agent {index}. Critique the prover route.")
    return (
        f"{role_guidance} "
        "Flag missing hypotheses, source gaps, unsafe proof-like language, and counterexample pressure. "
        "When you are reviewing work on a NAMED target subgoal or proof step, return a "
        "'scoped_objections' list: each item with text, scope (subgoal|proof_step|route|global), "
        "the matching subgoal_id or proof_step_id, kind (missing_source|hypothesis_gap|definition_gap|"
        "logical_gap|computation_needed|counterexample_pressure|unclear_claim|reviewer_disagreement|other), "
        "severity (P0|P1|P2|P3), and a suggested_next_action. An objection only blocks or prioritizes "
        "repair; it never proves, refutes, or closes anything. "
        "Do not upgrade proof, evidence, source, or grade status. Return JSON only."
    )


def agent_prompt(context: dict, *, role: str) -> str:
    return json.dumps(
        {
            "role": role,
            "protocol_rules": [
                "candidate routes only",
                "no proof/source/evidence upgrades",
                "human gate before deeper work",
                "source_backed requires exact locator and matching hypotheses",
                "time_allocation.yaml is scheduling-only and advisory-only",
                "record missing source/theorem/example/counterexample context explicitly",
            ],
            "context": context,
            "required_json_schema": AGENT_OUTPUT_SCHEMA,
        },
        indent=2,
    )


def _reviewer_roles(reviewer_set: str, verifier_count: int) -> list[str]:
    if reviewer_set == "espresso":
        return ESPRESSO_REVIEWER_ROLES
    return ["verifier" for _ in range(max(3, verifier_count))]


def safe_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [safe_text(item) for item in value if str(item).strip()][:12]


def safe_text(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    for phrase in sorted(FORBIDDEN_LANGUAGE, key=len, reverse=True):
        text = re.sub(
            forbidden_pattern(phrase),
            "[withheld proof-like phrase]",
            text,
            flags=re.IGNORECASE,
        )
    return text[:1200] or "No usable agent text returned."


def collect_unsafe_language(raw: dict) -> list[str]:
    text = " ".join(
        [
            str(raw.get("summary", "")),
            " ".join(str(item) for item in raw.get("steps", []) if item),
            " ".join(str(item) for item in raw.get("risks", []) if item),
        ]
    ).lower()
    return [
        phrase
        for phrase in FORBIDDEN_LANGUAGE
        if re.search(forbidden_pattern(phrase), text, flags=re.IGNORECASE)
    ]


def forbidden_pattern(phrase: str) -> str:
    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    return rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])"


def normalize_verdict(value: object) -> str:
    raw = str(value).strip()
    if raw in {"plausible_direction", "needs_revision", "needs_sources", "blocked"}:
        return raw
    if "source" in raw:
        return "needs_sources"
    if "block" in raw or "stop" in raw:
        return "blocked"
    return "needs_revision"


def normalize_action(value: object) -> str:
    raw = str(value).strip()
    if raw in {"continue", "revise", "stop"}:
        return raw
    if "stop" in raw or "block" in raw:
        return "stop"
    if "continue" in raw:
        return "continue"
    return "revise"


def extract_response_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    chunks = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(content.get("text", ""))
    return "\n".join(chunks).strip()


def strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    return stripped
