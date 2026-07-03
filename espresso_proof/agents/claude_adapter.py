#!/usr/bin/env python3
"""Espresso external-agent adapter routing agent reasoning through the local
`claude` CLI (Claude Code / Claude Max subscription).

This lets Espresso run REAL candidate-generating agents without a separate paid
API key: the `claude` CLI authenticates with your Claude Max subscription, so the
calls count against that subscription, not an OpenAI/Anthropic API bill.

Wire it up:

    export ESPRESSO_EXTERNAL_AGENT_COMMAND="uv run espresso-claude-agent"
    espresso orchestrate --input <claim.md> --provider external --front-half external
    # or: espresso run-llm-agents --run <run_dir> --provider external

Contract (espresso_proof.agents.llm_agents.ExternalCommandClient):
- reads ONE JSON request on stdin: {system, prompt, provider, model,
  required_json_schema, timeout_seconds?} — timeout_seconds is optional and
  additive (the client's actual outer timeout; absent for older callers).
- on success (or a safely-fallbackable failure) prints ONE JSON object on
  stdout and exits 0.
- when the requesting role's required_json_schema CANNOT be satisfied by the
  conservative fallback (back-half roles: proof writer, consensus reviewer,
  gap resolver), a failure exits NON-ZERO with a FATAL line on stderr instead
  of emitting schema-invalid JSON — the caller's transactional rollback takes
  over (fail closed).

This adapter is SCHEMA-AGNOSTIC: each Espresso stage (Prover/Reviewers AND the
front-half agents — Understanding Claim, Literature, Examples, Counterexamples,
Strategy, Attacker, Source/Theorem, Grade) embeds its own required_json_schema in
the request. The adapter forwards that schema to claude and returns claude's raw
JSON object verbatim, so every stage gets content shaped for its own artifact.

Skill injection: like the Codex adapter (espresso_proof.agents.codex_adapter),
this adapter selects the role-specific Espresso skill text with
``select_agent_skills`` over the system+prompt and prepends it (plus the
mathematical-safety skill and any explicit active goal) to the prompt sent to
claude. This is deterministic prompt assembly only; it changes no statuses.

Safety: the adapter only produces CANDIDATE planning JSON. The Espresso kernel
re-validates and contains every field (candidate-only status, proof-complete
language withheld, no source/evidence/proof status upgrade), so a weak or garbled
response is safe. On any failure the adapter emits a conservative fallback object
so the run continues and stays at needs_human / the mock skeleton value.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from espresso_proof.agents.adapter_runtime import (  # noqa: E402 - needs ROOT on sys.path
    DEFAULT_EXTERNAL_TIMEOUT_SECONDS,
    fallback_is_schema_valid,
    resolve_inner_timeout,
)

# Conservative fallback. Includes the Prover/Reviewer keys so that path stays
# safe even on failure; front-half normalizers ignore unknown keys and fall back
# to the validated mock skeleton when their fields are absent.
_FALLBACK = {
    "summary": "No usable agent output was produced.",
    "steps": [],
    "source_requests": [],
    "missing_hypotheses": [],
    "risks": [],
    "verdict": "needs_revision",
    "recommended_human_action": "revise",
}

_INSTRUCTION = (
    "Return ONLY a single JSON object (no markdown fences, no prose before or "
    "after) that matches the required_json_schema given in the request above. "
    "This is a CANDIDATE planning output only: do NOT claim anything is proved, "
    "verified, or proof-complete, and do NOT assert source_backed / checked "
    "status — the kernel decides those."
)


def main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        request = {}
    system = str(request.get("system", ""))
    prompt = str(request.get("prompt", ""))
    # required_json_schema handling: ExternalCommandClient dispatches it per role
    # (_required_json_schema_for_system). For FRONT-HALF stages it is the generic
    # prover default while the stage payload embeds its own schema — appending it
    # would force prover-shaped output, so we don't. For BACK-HALF roles (prover,
    # reviewers, proof writer, consensus, gap resolver) it is the authoritative
    # schema, and the payload may embed the WRONG generic one (agent_prompt always
    # embeds AGENT_OUTPUT_SCHEMA) — the Codex adapter enforces it via
    # --output-schema; without this, real Claude runs return prover-shaped JSON
    # for write-proof and the kernel rejects the artifact (verified by live smoke).
    output_schema = _authoritative_output_schema(request, system=system, prompt=prompt)
    inner_timeout = resolve_inner_timeout(
        request=request, default_outer=DEFAULT_EXTERNAL_TIMEOUT_SECONDS
    )

    try:
        selection = _select_skills(system=system, prompt=prompt)
        _emit_status(selection=selection)
        full_prompt = _build_full_prompt(
            system=system, prompt=prompt, selection=selection, output_schema=output_schema
        )
        data = _extract_json(_run_claude(full_prompt, timeout=inner_timeout))
        if not isinstance(data, dict):
            raise ValueError("claude output was not a JSON object")
        result = _scrub_candidate(data)  # neutralize any status smuggle in prose
    except Exception as exc:  # noqa: BLE001 - fallback where safe, else fail closed
        reason = f"claude adapter could not produce usable JSON: {exc}"[:300]
        if not fallback_is_schema_valid(request, _FALLBACK):
            # Back-half authoritative-schema role: the prover-shaped fallback
            # would be normalized into garbage downstream (e.g. lines=[]).
            # Exit non-zero so the caller's transactional rollback takes over.
            print(f"[espresso-agent] FATAL: {reason}", file=sys.stderr)
            return 1
        result = dict(_FALLBACK)
        result["summary"] = reason

    json.dump(result, sys.stdout)
    return 0


def _run_claude(prompt: str, *, timeout: int) -> str:
    claude_cli = os.environ.get("ESPRESSO_CLAUDE_CLI", "claude")
    proc = subprocess.run(
        [claude_cli, "-p", prompt],
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "claude CLI returned non-zero")
    return proc.stdout


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Prefer a fenced ```json block if present, else the first balanced object.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence is not None:
        return json.loads(fence.group(1))
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise ValueError("no JSON object found in claude output")
    return json.loads(match.group(0))


def _select_skills(*, system: str, prompt: str):
    """Same role-skill selection the Codex adapter uses (skill text + active
    goal). Lazily imported so a broken install fails inside the fallback guard
    instead of crashing the adapter process."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from espresso_proof.agents.agent_skill_catalog import select_agent_skills

    return select_agent_skills(
        system=system,
        prompt=prompt,
        cwd=Path.cwd(),
        active_goal_file=os.environ.get("ESPRESSO_ACTIVE_GOAL_FILE"),
        request_type=os.environ.get("ESPRESSO_REQUEST_TYPE"),
    )


def _authoritative_output_schema(request: dict, *, system: str, prompt: str) -> dict | None:
    """Return request["required_json_schema"] when it is authoritative for this
    call (back-half roles, same marker set the Codex adapter enforces
    --output-schema for); None for front-half stages, whose payloads carry their
    own schema that must not be overridden. Fails open to the old passthrough
    behavior if the marker check cannot be imported."""
    schema = request.get("required_json_schema")
    if not isinstance(schema, dict) or not schema.get("required"):
        return None
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from espresso_proof.agents.codex_adapter import _uses_generic_agent_output

        if _uses_generic_agent_output(system=system, prompt=prompt):
            return schema
    except Exception:  # noqa: BLE001 - keep old behavior rather than crash
        return None
    return None


def _build_full_prompt(*, system: str, prompt: str, selection, output_schema: dict | None = None) -> str:
    """Mirror the Codex adapter's prompt assembly: active goal + selected skill
    text ahead of the stage's own system/prompt, closed by the output contract."""
    sections = [
        "ESPRESSO ACTIVE GOAL",
        selection.goal.text,
        "ESPRESSO SELECTED SKILLS",
        ", ".join(selection.skills),
        selection.skill_text,
        "ESPRESSO SYSTEM PROMPT",
        system,
        "ESPRESSO STAGE REQUEST",
        prompt,
    ]
    if output_schema is not None:
        sections += [
            "ESPRESSO REQUIRED OUTPUT SCHEMA (authoritative for this call)",
            json.dumps(output_schema, indent=2),
            "If a schema embedded in the stage request above disagrees with this "
            "one, THIS schema wins: return a single JSON object with exactly its "
            "required keys.",
        ]
    sections += [
        "ESPRESSO OUTPUT CONTRACT",
        _INSTRUCTION,
    ]
    return "\n\n".join(sections)


def _emit_status(*, selection) -> None:
    print(
        f"[espresso-agent] Using goal: {selection.goal.path or 'none'}",
        file=sys.stderr,
    )
    print(
        f"[espresso-agent] Using skills: {', '.join(selection.skills)}",
        file=sys.stderr,
    )


# Free-text fields a model may fill with prose. We scrub these with the kernel's
# OWN detectors so a real model that writes "this step is source_backed" / "proof-close"
# cannot trip the kernel's status-smuggle / proof-language gates and roll the run
# back. The kernel re-validates regardless; this only keeps the candidate text
# safe so the legitimate math survives instead of being discarded.
_PROSE_FIELDS = frozenset({
    "summary", "steps", "proposed_steps", "output_summary", "source_requests",
    "missing_hypotheses", "risks", "reasons", "finding", "looked_for",
    "residual_objections", "note", "notes", "objections", "scope_note",
})


def _scrub_candidate(data: dict) -> dict:
    """Best-effort: neutralize proof/status-smuggle language in known prose fields
    using the kernel's own scrubber. Falls back to pass-through if unavailable."""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from espresso_proof.orchestration.advisory_proof_sketch import scrub_sketch_text
    except Exception:  # noqa: BLE001 - never block on the scrubber being importable
        return data

    def scrub(value):
        if isinstance(value, str):
            return scrub_sketch_text(value)
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    return {k: (scrub(v) if k in _PROSE_FIELDS else v) for k, v in data.items()}


if __name__ == "__main__":
    raise SystemExit(main())
