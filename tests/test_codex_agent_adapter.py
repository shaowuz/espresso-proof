from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from espresso_proof.agent_skill_catalog import select_agent_skills
from espresso_proof.llm_agents import ExternalCommandClient


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "espresso_codex_agent.py"


def _write_fake_codex(tmp_path: Path, *, message: str | None = None, return_code: int = 0) -> Path:
    fake = tmp_path / "codex"
    argv_log = tmp_path / "codex_argv.json"
    message_text = message or json.dumps(
        {
            "summary": "Codex returned candidate-only output.",
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
                f"message_text = {message_text!r}",
                f"return_code = {return_code!r}",
                "with open(argv_log, 'w', encoding='utf-8') as handle:",
                "    json.dump(sys.argv, handle)",
                "last_message = None",
                "for index, arg in enumerate(sys.argv):",
                "    if arg == '--output-last-message' and index + 1 < len(sys.argv):",
                "        last_message = sys.argv[index + 1]",
                "if last_message:",
                "    with open(last_message, 'w', encoding='utf-8') as handle:",
                "        handle.write(message_text)",
                "print(json.dumps({'event': 'fake_codex_done', 'text': message_text}))",
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
    fake_codex: Path,
    goal_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    log_dir = tmp_path / "logs"
    env = os.environ.copy()
    env["ESPRESSO_CODEX_CLI"] = str(fake_codex)
    env["ESPRESSO_AGENT_LOG_DIR"] = str(log_dir)
    if goal_file is not None:
        env["ESPRESSO_ACTIVE_GOAL_FILE"] = str(goal_file)
    request = {
        "system": system,
        "prompt": prompt,
        "provider": "external",
        "model": "ignored-by-codex-adapter",
        "required_json_schema": {},
    }
    return subprocess.run(
        [sys.executable, str(ADAPTER)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
        timeout=30,
    )


def test_codex_adapter_pins_model_effort_injects_goal_and_logs_full_prompt(tmp_path: Path) -> None:
    fake_codex = _write_fake_codex(tmp_path)
    goal = tmp_path / "goal.md"
    goal.write_text("Active goal: test Codex external mode.", encoding="utf-8")

    completed = _run_adapter(
        tmp_path=tmp_path,
        system="You are the Espresso Proof Literature-Review agent. Return JSON only.",
        prompt='{"claim": "Every group of order p^2 is abelian."}',
        fake_codex=fake_codex,
        goal_file=goal,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["verdict"] == "needs_sources"
    argv = json.loads((tmp_path / "codex_argv.json").read_text(encoding="utf-8"))
    assert argv[1] == "exec"
    assert "-m" in argv
    assert argv[argv.index("-m") + 1] == "gpt-5.5"
    assert "-c" in argv
    assert 'model_reasoning_effort="xhigh"' in argv
    assert "--ask-for-approval" not in argv
    full_prompt = argv[-1]
    assert "Active goal: test Codex external mode." in full_prompt
    assert "literature-review" in full_prompt
    assert "Skill: mathematical-safety" in full_prompt

    events_path = tmp_path / "logs" / "codex_agent_events.jsonl"
    prompt_path = tmp_path / "logs" / "codex_agent_prompts.log"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["agent_start", "agent_finish"]
    assert events[0]["model"] == "gpt-5.5"
    assert events[0]["effort"] == "xhigh"
    assert events[0]["skills"] == ["literature-review"]
    assert events[1]["ok"] is True
    prompt_log = prompt_path.read_text(encoding="utf-8")
    assert "command_argv:" in prompt_log
    assert "system_prompt:" in prompt_log
    assert "user_prompt:" in prompt_log
    assert "injected_skill_text:" in prompt_log
    assert "Active goal: test Codex external mode." in prompt_log


def test_codex_adapter_returns_safe_fallback_and_logs_reason_for_bad_json(tmp_path: Path) -> None:
    fake_codex = _write_fake_codex(tmp_path, message="not json")

    completed = _run_adapter(
        tmp_path=tmp_path,
        system="You are Espresso's prover agent. Return JSON only.",
        prompt='{"role": "prover"}',
        fake_codex=fake_codex,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["verdict"] == "needs_revision"
    assert "codex adapter could not produce usable JSON" in payload["summary"]
    events_path = tmp_path / "logs" / "codex_agent_events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["ok"] is False
    assert events[-1]["parse_status"] == "fallback"
    assert events[-1]["fallback_reason"]


def test_codex_skill_selection_covers_core_subagent_roles() -> None:
    cases = [
        ("You are the Espresso Proof Understanding-Claim agent.", "{}", ("claim-understanding",)),
        ("You are the Espresso Proof Examples agent.", "{}", ("examples",)),
        ("You are the Espresso Proof Counterexamples agent.", "{}", ("counterexamples",)),
        ("You are the Espresso Proof Strategy agent (generator + grader + brief).", "{}", ("strategy-generator", "strategy-grader")),
        ("You are the Espresso Proof Example/Counterexample Attacker.", "{}", ("strategy-attacker",)),
        ("You are the Espresso Proof Source-Matching agent.", "{}", ("reference-matcher",)),
        ("You are Espresso's prover agent.", '{"role": "prover"}', ("prover",)),
        ("You are Espresso strategy reviewer agent.", "{}", ("proof-outline-reviewer",)),
        ("You are Espresso evidence reviewer agent.", "{}", ("literature-use-reviewer",)),
        ("You are Espresso line proof reviewer agent.", "{}", ("line-proof-reviewer",)),
    ]
    for system, prompt, expected in cases:
        selected = select_agent_skills(system=system, prompt=prompt, cwd=ROOT)
        assert selected.skills == expected


def test_legacy_claude_adapter_remains_available() -> None:
    legacy = ROOT / "scripts" / "espresso_claude_agent.py"
    assert legacy.exists()
    assert "claude" in legacy.read_text(encoding="utf-8")


def test_external_client_records_codex_model_when_command_is_codex_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter = tmp_path / "espresso_codex_agent.py"
    adapter.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "json.loads(sys.stdin.read())",
                "json.dump({",
                "  'summary': 'candidate',",
                "  'steps': [],",
                "  'source_requests': [],",
                "  'missing_hypotheses': [],",
                "  'risks': [],",
                "  'verdict': 'needs_sources',",
                "  'recommended_human_action': 'revise',",
                "}, sys.stdout)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", f"{sys.executable} {adapter}")

    client = ExternalCommandClient(model="gpt-4.1")

    assert client.model == "gpt-5.5-xhigh"
    assert client.timeout == 360
    assert client.complete_json(system="Return JSON only.", prompt="{}")["verdict"] == "needs_sources"


def test_external_client_timeout_defaults_and_env_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    legacy = tmp_path / "legacy_agent.py"
    legacy.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "json.loads(sys.stdin.read())",
                "json.dump({'verdict': 'needs_sources'}, sys.stdout)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    codex = tmp_path / "espresso_codex_agent.py"
    codex.write_text(legacy.read_text(encoding="utf-8"), encoding="utf-8")

    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", f"{sys.executable} {legacy}")
    assert ExternalCommandClient(model="external").timeout == 120

    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", f"{sys.executable} {codex}")
    assert ExternalCommandClient(model="external").timeout == 360

    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_TIMEOUT_SECONDS", "450")
    assert ExternalCommandClient(model="external").timeout == 450
