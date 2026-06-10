from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .io import read_yaml, write_yaml
from .models import LLMAgentRunReport, ProverLoopReport, ProverRoundIndex, ProverRoundReport
from .research_bank import load_bankable_dict


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
]

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

ESPRESSO_REVIEWER_ROLES = [
    "strategy_reviewer",
    "evidence_reviewer",
    "line_proof_reviewer",
]

CODEX_EXTERNAL_MODEL = "gpt-5.5-xhigh"
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
        try:
            completed = subprocess.run(
                self.command,
                input=json.dumps(request),
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"external agent command timed out after {self.timeout}s") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(f"external agent command failed: {detail}")
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"external agent command did not return JSON: {completed.stdout[:500]}"
            ) from exc
        # A well-formed agent must return a JSON OBJECT. Valid-but-non-dict JSON
        # (a list / string / number / null) fails the same clean, classifiable
        # way as the other adapter errors instead of an opaque AttributeError on
        # .get below.
        if not isinstance(data, dict):
            raise RuntimeError(
                f"external agent command did not return a JSON object: {completed.stdout[:500]}"
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
    client = build_client(
        provider=provider,
        model=model,
        api_key_env=api_key_env,
        external_agent_command_env=external_agent_command_env,
    )
    context = load_agent_context(run_dir)
    now = datetime.now(UTC).isoformat()
    prover_raw = client.complete_json(
        system=prover_system_prompt(),
        prompt=agent_prompt(context, role="prover"),
    )
    prover = make_prover_attempt(prover_raw, client=client, created_at=now)
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
        "input_artifacts": INPUT_ARTIFACTS,
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


def build_prover_loop_report(
    *,
    agent_report: dict,
    strategy_brief: dict,
    human_choice: dict | None = None,
    approved_effort: str = "normal_pass",
) -> dict:
    top_strategies = strategy_brief.get("top_strategies", [])[:3]
    if not top_strategies:
        top_strategies = [
            {
                "id": "strategy_missing_brief",
                "title": "No strategy brief route available",
            }
        ]
    verifier_reviews = agent_report.get("verifier_reviews", [])
    reviewer_pointers = [
        {
            "verifier_id": review["verifier_id"],
            "verdict": review["verdict"],
            "objection_count": len(review.get("objections", [])),
            "recommended_human_action": review["recommended_human_action"],
        }
        for review in verifier_reviews
    ]
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
    )[:12]
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
                ),
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
        "decision_reason": _loop_decision_reason(gate, blockers),
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


def _selected_strategy_from_choice(human_choice: dict | None, strategies: list[dict]) -> str | None:
    strategy_ids = [strategy.get("id") for strategy in strategies]
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
) -> str:
    if gate.get("status") == "pending":
        return "ask_human"
    if gate.get("decision") == "stop":
        return "stop"
    if any(review.get("verdict") == "blocked" for review in reviewer_reviews):
        return "pause"
    is_selected = strategy_id == selected_strategy_id
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
    if allocation == "repair" or blockers:
        return "needs_repair"
    return "candidate"


def _next_action_for_strategy(*, index: int, strategy: dict, gate: dict, blockers: list[str]) -> str:
    title = strategy.get("title") or strategy.get("id") or f"strategy {index}"
    if gate.get("status") == "pending":
        if index == 1:
            return f"Ask mathematician whether to spend the next round on {title}."
        return f"Keep {title} as a low-budget alternative until human preference is resolved."
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


def _loop_decision_reason(gate: dict, blockers: list[str]) -> str:
    if gate.get("status") == "pending":
        return "Human gate is pending; Espresso must not run deeper prover rounds yet."
    if gate.get("decision") == "stop":
        return "Human gate resolved to stop."
    if blockers:
        return "Reviewer objections or source requests require repair before another proof round."
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
        "target_subgoal": target_subgoal,
    }
    created_at = datetime.now(UTC).isoformat()
    prover_raw = client.complete_json(
        system=prover_system_prompt(),
        prompt=agent_prompt(round_context, role="prover_loop_round"),
    )
    prover = make_prover_attempt(prover_raw, client=client, created_at=created_at)
    prover["agent_id"] = f"llm_prover_{next_round:03d}"

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
    )[:12]
    reviewer_pointers = [
        {
            "verifier_id": review["verifier_id"],
            "verdict": review["verdict"],
            "objection_count": len(review.get("objections", [])),
            "recommended_human_action": review["recommended_human_action"],
        }
        for review in verifier_reviews
    ]
    selected_strategy_id = parent_attempt["strategy_id"]
    allocation = "repair" if blockers else "expand"
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
        ),
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
        agent_report=agent_report,
        loop_report=loop_report,
        attempt=new_attempt,
        prover_attempt=prover,
        verifier_reviews=verifier_reviews,
        source="appended_prover_loop_round",
    )
    refresh_prover_round_index(run_dir=run_dir, loop_report=loop_report)
    manifest.setdefault("optional_artifacts", {})["prover_round_index"] = "prover_rounds/index.yaml"
    write_yaml(run_dir / "run_manifest.yaml", manifest)
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


def make_prover_attempt(raw: dict, *, client: LLMClient, created_at: str) -> dict:
    return {
        "agent_id": "llm_prover_001",
        "role": "prover",
        "provider": client.provider,
        "model": client.model,
        "created_at": created_at,
        "input_artifacts": INPUT_ARTIFACTS,
        "output_summary": safe_text(raw.get("summary", "")),
        "proposed_steps": safe_list(raw.get("steps", [])),
        "source_requests": safe_list(raw.get("source_requests", [])),
        "risks": safe_list(raw.get("risks", [])),
        "status": "needs_human",
    }


def make_verifier_review(
    raw: dict,
    *,
    client: LLMClient,
    created_at: str,
    index: int,
    reviewer_role: str,
) -> dict:
    unsafe_language = collect_unsafe_language(raw)
    verdict = normalize_verdict(raw.get("verdict", "needs_revision"))
    action = normalize_action(raw.get("recommended_human_action", "revise"))
    return {
        "verifier_id": f"{reviewer_role}_{index:03d}",
        "provider": client.provider,
        "model": client.model,
        "created_at": created_at,
        "verdict": verdict,
        "objections": safe_list(raw.get("risks", [])),
        "missing_hypotheses": safe_list(raw.get("missing_hypotheses", [])),
        "source_requests": safe_list(raw.get("source_requests", [])),
        "unsafe_language": unsafe_language,
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
    for phrase in FORBIDDEN_LANGUAGE:
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
