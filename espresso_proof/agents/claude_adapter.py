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
from espresso_proof.agents.json_repair import loads_forgiving  # noqa: E402 - same

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

    envelope_meta: dict | None = None
    # Which half of the call failed, so the recorded reason names the real
    # cause. A live run recorded "could not produce usable JSON" for a call
    # where the CLI exited non-zero and no reply existed to parse — the record
    # blamed the model for an infrastructure failure.
    phase = "run the claude CLI"
    try:
        selection = _select_skills(
            system=system, prompt=prompt, role=str(request.get("role") or "") or None
        )
        _emit_status(selection=selection)
        full_prompt = _build_full_prompt(
            system=system, prompt=prompt, selection=selection, output_schema=output_schema
        )
        text, envelope_meta = _run_claude(full_prompt, timeout=inner_timeout)
        phase = "produce usable JSON"
        data, repaired = _extract_json(text)
        if not isinstance(data, dict):
            raise ValueError("claude output was not a JSON object")
        result = _scrub_candidate(data)  # neutralize any status smuggle in prose
        # "repaired" is not "ok": the reply needed its own escaping fixed before
        # it would parse. The content is the model's; the record says so.
        parse_status = "repaired" if repaired else "ok"
        fallback_reason = (
            "invalid escapes/control characters inside JSON strings were repaired"
            if repaired
            else ""
        )
    except Exception as exc:  # noqa: BLE001 - fallback where safe, else fail closed
        reason = f"claude adapter could not {phase}: {exc}"[:300]
        if not fallback_is_schema_valid(request, _FALLBACK):
            # Back-half authoritative-schema role: the prover-shaped fallback
            # would be normalized into garbage downstream (e.g. lines=[]).
            # Exit non-zero so the caller's transactional rollback takes over.
            print(f"[espresso-agent] FATAL: {reason}", file=sys.stderr)
            return 1
        result = dict(_FALLBACK)
        result["summary"] = reason
        parse_status = "fallback"
        fallback_reason = reason

    # Canonical identity/usage envelope (provenance only, never evidence):
    # identity and usage come from the claude CLI's own --output-format json
    # reply; an old CLI that prints plain text degrades to unknown, never to a
    # fabricated value.
    meta = envelope_meta or {}
    result["_espresso_adapter"] = {
        "adapter": "espresso_claude_agent",
        "model": meta.get("model"),
        "request_id": meta.get("request_id"),
        "parse_status": parse_status,
        "fallback_used": parse_status != "ok",
        "fallback_reason": fallback_reason,
        "usage": meta.get("usage"),
        "total_cost_usd": meta.get("total_cost_usd"),
        "usage_source": "claude_cli_json" if isinstance(meta.get("usage"), dict) else "unreported",
    }

    json.dump(result, sys.stdout)
    return 0


def _run_claude(prompt: str, *, timeout: int) -> tuple[str, dict | None]:
    """Run the claude CLI and return (reply_text, envelope_meta|None).

    ``--output-format json`` wraps the reply in an envelope carrying identity
    (session_id, modelUsage keys) and usage/cost. Every field is optional; an
    old CLI printing plain text falls back to (stdout, None) — identity and
    usage become UNKNOWN, never fabricated.
    """

    claude_cli = os.environ.get("ESPRESSO_CLAUDE_CLI", "claude")
    # Default to the cheapest Claude model. Without an explicit --model the CLI
    # runs whatever the interactive session defaults to — typically the most
    # expensive tier — so an Espresso run inherited a pricing decision nobody
    # made. The CLI self-reports the resolved model id in its JSON envelope, so
    # provenance records what actually ran regardless of this knob.
    claude_model = os.environ.get("ESPRESSO_CLAUDE_MODEL", "haiku")
    # The prompt goes on STDIN, not argv. As a single argv element it was
    # unbounded — role skill text + system prompt + the indent=2 JSON dump of the
    # whole run context — and a long enough claim or a fat context would exceed
    # ARG_MAX and fail with E2BIG before the model ever saw it. The codex adapter
    # already feeds its child on stdin; this makes the two consistent.
    #
    # start_new_session=True puts the CLI in its OWN process group so a timeout
    # can reclaim the WHOLE group. subprocess.run(timeout=) kills only the direct
    # child, so a CLI that forks (observed: it does) left descendants running and
    # holding the pipe after a timeout — exactly the case the client-side group
    # reclamation was built for, structurally bypassed here.
    proc = subprocess.Popen(
        [claude_cli, "-p", "--model", claude_model, "--output-format", "json"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        _reclaim_process_group(proc)
        raise
    if proc.returncode != 0:
        raise RuntimeError((stderr or "").strip() or "claude CLI returned non-zero")
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout, None
    if not isinstance(envelope, dict) or not isinstance(envelope.get("result"), str):
        return stdout, None
    model_usage = envelope.get("modelUsage")
    model = None
    if isinstance(model_usage, dict) and len(model_usage) == 1:
        model = next(iter(model_usage))
    usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else None
    cost = envelope.get("total_cost_usd")
    meta = {
        "model": model,
        "request_id": envelope.get("session_id"),
        "usage": usage,
        "total_cost_usd": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
        "duration_ms": envelope.get("duration_ms") if isinstance(envelope.get("duration_ms"), int) else None,
    }
    return envelope["result"], meta


def _extract_json(text: str) -> tuple[dict, bool]:
    """Return (object, repaired). See json_repair: a model writing LaTeX into a
    JSON string ("\\frac", "\\le") produces text strict JSON rejects, and
    losing the whole call to that was costing real reviewer feedback."""

    text = text.strip()
    # Prefer a fenced ```json block if present, else the first balanced object.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence is not None:
        return loads_forgiving(fence.group(1))
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise ValueError("no JSON object found in claude output")
    return loads_forgiving(match.group(0))


def _reclaim_process_group(proc) -> None:
    """Kill the CLI's whole process group, then reap it.

    A forking CLI outlives a plain kill of the direct child: the descendants
    keep the pipe open and the worker thread stays blocked. Signalling the group
    is what makes the timeout real. Best-effort by construction — the process
    may already be gone.
    """

    import signal

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            break
        try:
            proc.wait(timeout=2)
            return
        except Exception:  # noqa: BLE001 - escalate to SIGKILL
            continue
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _select_skills(*, system: str, prompt: str, role: str | None = None):
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
        role=role,
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
