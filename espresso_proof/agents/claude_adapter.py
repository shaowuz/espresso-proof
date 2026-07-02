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
- reads ONE JSON request on stdin: {system, prompt, provider, model, required_json_schema}
- must print ONE JSON object on stdout.

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
    # NOTE: do NOT append request["required_json_schema"]. Each stage already
    # embeds its OWN required_json_schema inside `prompt` (the Prover/Reviewer
    # agent_prompt and every front-half payload). The top-level
    # request["required_json_schema"] is hardcoded to the Prover schema by
    # ExternalCommandClient, so appending it would override the front-half stage's
    # schema and force Prover-shaped output for every stage.

    try:
        selection = _select_skills(system=system, prompt=prompt)
        _emit_status(selection=selection)
        full_prompt = _build_full_prompt(system=system, prompt=prompt, selection=selection)
        data = _extract_json(_run_claude(full_prompt))
        if not isinstance(data, dict):
            raise ValueError("claude output was not a JSON object")
        result = _scrub_candidate(data)  # neutralize any status smuggle in prose
    except Exception as exc:  # noqa: BLE001 - emit safe fallback, never crash the run
        result = dict(_FALLBACK)
        result["summary"] = f"claude adapter could not produce usable JSON: {exc}"[:300]

    json.dump(result, sys.stdout)
    return 0


def _run_claude(prompt: str, timeout: int = 110) -> str:
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


def _build_full_prompt(*, system: str, prompt: str, selection) -> str:
    """Mirror the Codex adapter's prompt assembly: active goal + selected skill
    text ahead of the stage's own system/prompt, closed by the output contract."""
    return "\n\n".join(
        [
            "ESPRESSO ACTIVE GOAL",
            selection.goal.text,
            "ESPRESSO SELECTED SKILLS",
            ", ".join(selection.skills),
            selection.skill_text,
            "ESPRESSO SYSTEM PROMPT",
            system,
            "ESPRESSO STAGE REQUEST",
            prompt,
            "ESPRESSO OUTPUT CONTRACT",
            _INSTRUCTION,
        ]
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
        from espresso_proof.orchestration.advisory_proof_sketch import _scrub_sketch_text
    except Exception:  # noqa: BLE001 - never block on the scrubber being importable
        return data

    def scrub(value):
        if isinstance(value, str):
            return _scrub_sketch_text(value)
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    return {k: (scrub(v) if k in _PROSE_FIELDS else v) for k, v in data.items()}


if __name__ == "__main__":
    raise SystemExit(main())
