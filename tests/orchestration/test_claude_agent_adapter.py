"""Claude Max CLI adapter (espresso_proof.agents.claude_adapter).

The claude adapter is the primary real-agent path. It must inject the SAME
``select_agent_skills`` output the Codex adapter injects (skills were previously
dead on this path), while keeping its existing contract intact: schema-agnostic
verbatim passthrough of claude's JSON object, prose-field scrubbing with the
kernel's own detectors, and a conservative fallback object on ANY failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from espresso_proof.agents.llm_agents import line_numbered_proof_writer_system_prompt

ROOT = Path(__file__).resolve().parents[2]


def _write_fake_claude(tmp_path: Path, *, message: str | None = None, return_code: int = 0) -> Path:
    fake = tmp_path / "claude"
    argv_log = tmp_path / "claude_argv.json"
    prompt_log = tmp_path / "claude_prompt.txt"
    message_text = message or json.dumps(
        {
            "summary": "Claude returned candidate-only output.",
            "steps": ["Check the exact theorem locator."],
            "source_requests": ["Need source locator."],
            "missing_hypotheses": [],
            "risks": ["Candidate only."],
            "verdict": "needs_sources",
            "recommended_human_action": "revise",
        }
    )
    fake.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                f"argv_log = {str(argv_log)!r}",
                f"prompt_log = {str(prompt_log)!r}",
                f"message_text = {message_text!r}",
                f"return_code = {return_code!r}",
                "with open(argv_log, 'w', encoding='utf-8') as handle:",
                "    json.dump(sys.argv, handle)",
                "prompt = sys.argv[2] if len(sys.argv) > 2 else ''",
                "with open(prompt_log, 'w', encoding='utf-8') as handle:",
                "    handle.write(prompt)",
                "print(message_text)",
                "sys.exit(return_code)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _run_adapter(
    *,
    tmp_path: Path,
    system: str,
    prompt: str,
    fake_claude: Path,
    goal_file: Path | None = None,
    schema: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ESPRESSO_CLAUDE_CLI"] = str(fake_claude)
    env.pop("ESPRESSO_ACTIVE_GOAL_FILE", None)
    env.pop("ESPRESSO_REQUEST_TYPE", None)
    if goal_file is not None:
        env["ESPRESSO_ACTIVE_GOAL_FILE"] = str(goal_file)
    request = {
        "system": system,
        "prompt": prompt,
        "provider": "external",
        "model": "ignored-by-claude-adapter",
        "required_json_schema": {} if schema is None else schema,
    }
    return subprocess.run(
        [sys.executable, "-m", "espresso_proof.agents.claude_adapter"],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
        timeout=30,
    )


def test_claude_adapter_injects_skills_goal_and_stays_schema_agnostic(tmp_path: Path) -> None:
    """The full prompt sent to the claude CLI carries the selected skill text and
    explicit active goal (mirroring the Codex adapter), and claude's raw JSON
    object comes back verbatim (schema-agnostic passthrough: non-prose fields and
    unknown keys untouched)."""
    stage_shaped = json.dumps(
        {
            "normalized_statement": "Every group of order p^2 is abelian.",
            "objects": ["finite group of order p^2"],
            "verdict": "needs_sources",
            "stage_specific_key": [1, 2, 3],
        }
    )
    fake_claude = _write_fake_claude(tmp_path, message=stage_shaped)
    goal = tmp_path / "goal.md"
    goal.write_text("Active goal: test Claude external mode.", encoding="utf-8")

    completed = _run_adapter(
        tmp_path=tmp_path,
        system="You are the Espresso Proof Literature-Review agent. Return JSON only.",
        prompt='{"claim": "Every group of order p^2 is abelian."}',
        fake_claude=fake_claude,
        goal_file=goal,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    # Schema-agnostic passthrough: stage-shaped output survives verbatim.
    assert payload["normalized_statement"] == "Every group of order p^2 is abelian."
    assert payload["objects"] == ["finite group of order p^2"]
    assert payload["stage_specific_key"] == [1, 2, 3]
    assert payload["verdict"] == "needs_sources"
    # The claude CLI was invoked as `claude -p <full prompt>`.
    argv = json.loads((tmp_path / "claude_argv.json").read_text(encoding="utf-8"))
    assert argv[1] == "-p"
    full_prompt = (tmp_path / "claude_prompt.txt").read_text(encoding="utf-8")
    assert "Active goal: test Claude external mode." in full_prompt
    assert "ESPRESSO SELECTED SKILLS" in full_prompt
    assert "literature-review" in full_prompt
    assert "Skill: mathematical-safety" in full_prompt
    assert "ESPRESSO SYSTEM PROMPT" in full_prompt
    assert "You are the Espresso Proof Literature-Review agent." in full_prompt
    assert "ESPRESSO STAGE REQUEST" in full_prompt
    assert '{"claim": "Every group of order p^2 is abelian."}' in full_prompt
    assert "ESPRESSO OUTPUT CONTRACT" in full_prompt
    # The adapter reports its selection out-of-band on stderr, like Codex.
    assert "Using skills: literature-review" in completed.stderr


def test_claude_adapter_injects_backhalf_proof_writer_skill(tmp_path: Path) -> None:
    """A real proof-writer call through the claude adapter receives the
    proof-writer skill file (previously dead on the claude path)."""
    fake_claude = _write_fake_claude(tmp_path)

    completed = _run_adapter(
        tmp_path=tmp_path,
        system=line_numbered_proof_writer_system_prompt(),
        prompt='{"role": "line_numbered_proof_writer"}',
        fake_claude=fake_claude,
    )

    assert completed.returncode == 0, completed.stderr
    full_prompt = (tmp_path / "claude_prompt.txt").read_text(encoding="utf-8")
    assert "Skill: proof-writer" in full_prompt
    assert "Skill: mathematical-safety" in full_prompt
    assert "proof-writer" in completed.stderr


def test_claude_adapter_appends_authoritative_schema_for_backhalf_roles(tmp_path: Path) -> None:
    """Back-half calls (proof writer, consensus, gap resolver, prover rounds)
    receive request["required_json_schema"] as an authoritative output-schema
    section — agent_prompt embeds the WRONG generic schema for these roles, and
    a live Claude smoke returned prover-shaped JSON for write-proof without this
    (the Codex adapter enforces the same schema via --output-schema)."""
    fake_claude = _write_fake_claude(tmp_path)
    writer_schema = {
        "type": "object",
        "required": ["proof_kind", "lines"],
        "properties": {"proof_kind": {"type": "string"}, "lines": {"type": "array"}},
    }

    completed = _run_adapter(
        tmp_path=tmp_path,
        system=line_numbered_proof_writer_system_prompt(),
        prompt='{"role": "line_numbered_proof_writer"}',
        fake_claude=fake_claude,
        schema=writer_schema,
    )

    assert completed.returncode == 0, completed.stderr
    full_prompt = (tmp_path / "claude_prompt.txt").read_text(encoding="utf-8")
    assert "ESPRESSO REQUIRED OUTPUT SCHEMA (authoritative for this call)" in full_prompt
    assert '"proof_kind"' in full_prompt
    assert "THIS schema wins" in full_prompt


def test_claude_adapter_does_not_override_front_half_stage_schema(tmp_path: Path) -> None:
    """Front-half stage calls embed their OWN schema in the payload; the
    top-level request schema is the generic prover default there and must NOT be
    appended (it would force prover-shaped output for every stage)."""
    fake_claude = _write_fake_claude(tmp_path)
    generic_schema = {
        "type": "object",
        "required": ["summary", "steps", "verdict"],
    }

    completed = _run_adapter(
        tmp_path=tmp_path,
        system="You are the Espresso Proof Literature-Review agent. Return JSON only.",
        prompt='{"task": "propose_candidate_references"}',
        fake_claude=fake_claude,
        schema=generic_schema,
    )

    assert completed.returncode == 0, completed.stderr
    full_prompt = (tmp_path / "claude_prompt.txt").read_text(encoding="utf-8")
    assert "ESPRESSO REQUIRED OUTPUT SCHEMA" not in full_prompt


def test_claude_adapter_scrubs_prose_fields_with_kernel_detectors(tmp_path: Path) -> None:
    """Prose fields still pass through the kernel's own scrubber: proof-complete
    language cannot survive in candidate text, while non-prose fields are
    untouched."""
    smuggled = json.dumps(
        {
            "summary": "The claim is proved. proof-close.",
            "steps": ["Cite the lemma with an exact locator."],
            "verdict": "needs_sources",
        }
    )
    fake_claude = _write_fake_claude(tmp_path, message=smuggled)

    completed = _run_adapter(
        tmp_path=tmp_path,
        system="You are the Espresso Proof Literature-Review agent. Return JSON only.",
        prompt="{}",
        fake_claude=fake_claude,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert "proof-close" not in payload["summary"]
    assert "proved" not in payload["summary"]
    assert payload["steps"] == ["Cite the lemma with an exact locator."]
    assert payload["verdict"] == "needs_sources"


def test_claude_adapter_returns_safe_fallback_for_bad_json(tmp_path: Path) -> None:
    fake_claude = _write_fake_claude(tmp_path, message="not json")

    completed = _run_adapter(
        tmp_path=tmp_path,
        system="You are Espresso's prover agent. Return JSON only.",
        prompt='{"role": "prover"}',
        fake_claude=fake_claude,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["verdict"] == "needs_revision"
    assert payload["recommended_human_action"] == "revise"
    assert "claude adapter could not produce usable JSON" in payload["summary"]


def test_claude_adapter_returns_safe_fallback_when_cli_fails(tmp_path: Path) -> None:
    fake_claude = _write_fake_claude(tmp_path, return_code=3)

    completed = _run_adapter(
        tmp_path=tmp_path,
        system="You are the Espresso Proof Examples agent. Return JSON only.",
        prompt="{}",
        fake_claude=fake_claude,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["verdict"] == "needs_revision"
    assert "claude adapter could not produce usable JSON" in payload["summary"]
