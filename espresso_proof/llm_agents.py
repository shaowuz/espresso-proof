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
from .models import LLMAgentRunReport


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

FORBIDDEN_LANGUAGE = [
    "proved",
    "verified",
    "certainly",
    "obviously",
    "clearly",
    "complete proof",
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
        timeout: int = 120,
    ) -> None:
        self.model = model or "external-json-agent"
        self.command_env = command_env
        self.timeout = timeout
        raw_command = os.environ.get(command_env, "")
        if not raw_command:
            raise RuntimeError(f"{command_env} is not set")
        self.command = shlex.split(raw_command)
        if not self.command:
            raise RuntimeError(f"{command_env} is empty")

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
        if isinstance(data.get("output"), dict):
            return data["output"]
        return data


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
    command_present = bool(os.environ.get(command_env, ""))
    report = {
        "provider": "external",
        "model": model or "external-json-agent",
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
        "examples": read_yaml(run_dir / artifacts["examples"]),
        "computation_checks": read_yaml(run_dir / artifacts["computation_checks"]),
        "counterexamples": read_yaml(run_dir / artifacts["counterexamples"]),
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
    return ["verifier" for _ in range(max(1, verifier_count))]


def safe_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [safe_text(item) for item in value if str(item).strip()][:12]


def safe_text(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    for phrase in FORBIDDEN_LANGUAGE:
        text = re.sub(
            forbidden_pattern(phrase),
            f"[withheld proof-like phrase: {phrase}]",
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
