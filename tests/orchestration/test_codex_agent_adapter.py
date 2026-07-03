from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from espresso_proof.agents import codex_adapter
from espresso_proof.agents.agent_skill_catalog import select_agent_skills
from espresso_proof.agents.llm_agents import (
    ExternalCommandClient,
    agent_prompt,
    line_numbered_proof_writer_system_prompt,
    proof_consensus_system_prompt,
    proof_gap_resolver_system_prompt,
    prover_system_prompt,
    verifier_system_prompt,
)


ROOT = Path(__file__).resolve().parents[2]


def _write_fake_codex(tmp_path: Path, *, message: str | None = None, return_code: int = 0) -> Path:
    fake = tmp_path / "codex"
    argv_log = tmp_path / "codex_argv.json"
    stdin_log = tmp_path / "codex_stdin.txt"
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
                f"stdin_log = {str(stdin_log)!r}",
                f"message_text = {message_text!r}",
                f"return_code = {return_code!r}",
                "with open(argv_log, 'w', encoding='utf-8') as handle:",
                "    json.dump(sys.argv, handle)",
                "stdin_text = sys.stdin.read()",
                "with open(stdin_log, 'w', encoding='utf-8') as handle:",
                "    handle.write(stdin_text)",
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
    schema: dict | None = None,
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
        "required_json_schema": {} if schema is None else schema,
    }
    return subprocess.run(
        [sys.executable, "-m", "espresso_proof.agents.codex_adapter"],
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
    meta = payload["_espresso_codex_adapter"]
    assert meta["adapter"] == "espresso_codex_agent"
    assert meta["parse_status"] == "ok"
    assert meta["fallback_used"] is False
    assert meta["request_id"]
    argv = json.loads((tmp_path / "codex_argv.json").read_text(encoding="utf-8"))
    assert argv[1] == "exec"
    assert "-m" in argv
    assert argv[argv.index("-m") + 1] == "gpt-5.5"
    assert "-c" in argv
    assert 'model_reasoning_effort="medium"' in argv
    assert "--ask-for-approval" not in argv
    assert argv[-1] == "-"
    full_prompt = (tmp_path / "codex_stdin.txt").read_text(encoding="utf-8")
    assert "Active goal: test Codex external mode." in full_prompt
    assert "literature-review" in full_prompt
    assert "Skill: mathematical-safety" in full_prompt

    events_path = tmp_path / "logs" / "codex_agent_events.jsonl"
    prompt_path = tmp_path / "logs" / "codex_agent_prompts.log"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["agent_start", "agent_finish"]
    assert events[0]["model"] == "gpt-5.5"
    assert events[0]["effort"] == "medium"
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
    assert payload["_espresso_codex_adapter"]["fallback_used"] is True
    events_path = tmp_path / "logs" / "codex_agent_events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["ok"] is False
    assert events[-1]["parse_status"] == "fallback"
    assert events[-1]["fallback_reason"]


def test_codex_adapter_marks_diagnostic_fallback_out_of_band(tmp_path: Path) -> None:
    fake_codex = _write_fake_codex(tmp_path, message="not json")

    completed = _run_adapter(
        tmp_path=tmp_path,
        system="You are Espresso's external agent diagnostic. Return JSON only.",
        prompt="Return a minimal candidate-only Espresso agent response for this diagnostic.",
        fake_codex=fake_codex,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    # Normal agent fields still fail closed to a candidate-only fallback.
    assert payload["verdict"] == "needs_revision"
    # Diagnostic readiness must be able to tell this was NOT live Codex JSON.
    meta = payload["_espresso_codex_adapter"]
    assert meta["adapter"] == "espresso_codex_agent"
    assert meta["parse_status"] == "fallback"
    assert meta["fallback_used"] is True
    assert meta["fallback_reason"]


@pytest.mark.skipif(os.name != "nt", reason="WindowsApps launcher caching is Windows-only")
def test_codex_command_prefix_caches_windowsapps_cli(tmp_path: Path, monkeypatch) -> None:
    source = (
        tmp_path
        / "Program Files"
        / "WindowsApps"
        / "OpenAI.Codex_1.0.0.0_x64__test"
        / "app"
        / "resources"
        / "codex.exe"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake windowsapps codex executable")
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("ESPRESSO_CODEX_CLI_CACHE_DIR", str(cache_dir))

    prefix = codex_adapter._codex_command_prefix(str(source))

    assert len(prefix) == 1
    cached = Path(prefix[0])
    assert cached.parent == cache_dir
    assert "WindowsApps" not in str(cached)
    assert cached.read_bytes() == source.read_bytes()
    assert cached.suffix == ".exe"


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


_NEW_BACKHALF_SKILLS = (
    "proof-writer",
    "proof-gap-resolver",
    "reviewer-theorem-applicability",
    "reviewer-adversarial-gap",
)


def test_skill_selection_covers_backhalf_proof_roles_with_runtime_prompts() -> None:
    """Each Goal-69/70 back-half role selects its own skill against the EXACT
    system/prompt text the runtime sends (llm_agents prompt builders), and the
    role skill owns ``selection.stage`` (first hit in the catalog)."""
    cases = [
        (
            line_numbered_proof_writer_system_prompt(),
            agent_prompt({}, role="line_numbered_proof_writer"),
            "proof-writer",
        ),
        (
            proof_gap_resolver_system_prompt(),
            agent_prompt({}, role="proof_gap_resolver"),
            "proof-gap-resolver",
        ),
        (
            proof_consensus_system_prompt("theorem_lemma_applicability_reviewer"),
            agent_prompt({}, role="proof_review_theorem_lemma_applicability_reviewer"),
            "reviewer-theorem-applicability",
        ),
        (
            proof_consensus_system_prompt("adversarial_gap_reviewer"),
            agent_prompt({}, role="proof_review_adversarial_gap_reviewer"),
            "reviewer-adversarial-gap",
        ),
    ]
    for system, prompt, expected_skill in cases:
        selected = select_agent_skills(system=system, prompt=prompt, cwd=ROOT)
        assert expected_skill in selected.skills, (expected_skill, selected.skills)
        assert selected.stage == expected_skill
        # The role skill's own MD file is injected (safety skill always first).
        assert "Skill: mathematical-safety" in selected.skill_text
        assert f"Skill: {expected_skill}" in selected.skill_text
        # No cross-injection between the four back-half role skills.
        for other in _NEW_BACKHALF_SKILLS:
            if other != expected_skill:
                assert other not in selected.skills, (system[:60], selected.skills)


def test_third_consensus_seat_still_selects_line_proof_reviewer_skill() -> None:
    selected = select_agent_skills(
        system=proof_consensus_system_prompt("line_proof_reviewer"),
        prompt=agent_prompt({}, role="proof_review_line_proof_reviewer"),
        cwd=ROOT,
    )
    assert "line-proof-reviewer" in selected.skills
    for skill in _NEW_BACKHALF_SKILLS:
        assert skill not in selected.skills


def test_backhalf_skills_do_not_fire_on_prover_or_verifier_rounds() -> None:
    """Negative: a plain prover round / verifier reviews (real runtime prompt
    text) must not pick up any of the four back-half role skills, and their
    pre-existing selection is unchanged."""
    prover_selected = select_agent_skills(
        system=prover_system_prompt(),
        prompt=agent_prompt({}, role="prover"),
        cwd=ROOT,
    )
    for skill in _NEW_BACKHALF_SKILLS:
        assert skill not in prover_selected.skills
    assert "prover" in prover_selected.skills
    for role in ("strategy_reviewer", "evidence_reviewer", "line_proof_reviewer", "verifier"):
        selected = select_agent_skills(
            system=verifier_system_prompt(1, reviewer_role=role),
            prompt=agent_prompt({}, role=role),
            cwd=ROOT,
        )
        for skill in _NEW_BACKHALF_SKILLS:
            assert skill not in selected.skills, (role, selected.skills)


def test_backhalf_skills_do_not_fire_on_front_half_stage_calls() -> None:
    """Negative: the real front-half stage system prompts never trigger the
    back-half proof skills (no accidental cross-injection)."""
    from espresso_proof.front_half.stages.attacker import _ATTACKER_SYSTEM
    from espresso_proof.front_half.stages.check_generator import _CHECK_GENERATOR_SYSTEM
    from espresso_proof.front_half.stages.counterexamples import _COUNTEREXAMPLES_SYSTEM
    from espresso_proof.front_half.stages.examples import _EXAMPLES_SYSTEM
    from espresso_proof.front_half.stages.grade import _GRADE_SYSTEM
    from espresso_proof.front_half.stages.literature import _LITERATURE_SYSTEM
    from espresso_proof.front_half.stages.source import _SOURCE_THEOREM_SYSTEM
    from espresso_proof.front_half.stages.understanding import _UNDERSTANDING_SYSTEM

    front_half_systems = (
        _ATTACKER_SYSTEM,
        _CHECK_GENERATOR_SYSTEM,
        _COUNTEREXAMPLES_SYSTEM,
        _EXAMPLES_SYSTEM,
        _GRADE_SYSTEM,
        _LITERATURE_SYSTEM,
        _SOURCE_THEOREM_SYSTEM,
        _UNDERSTANDING_SYSTEM,
    )
    for system in front_half_systems:
        selected = select_agent_skills(
            system=system,
            prompt='{"claim": "Every group of order p^2 is abelian."}',
            cwd=ROOT,
        )
        for skill in _NEW_BACKHALF_SKILLS:
            assert skill not in selected.skills, (system[:60], selected.skills)


def test_codex_adapter_schema_gate_covers_proof_specific_roles() -> None:
    cases = [
        ("You are the Espresso line-numbered proof writer.", "{}"),
        ("You are the proof-consensus review agent.", "{}"),
        ("You are the proof gap resolver.", "{}"),
        ("Return JSON only.", '{"role": "line_numbered_proof_writer"}'),
        ("Return JSON only.", '{"role": "proof_gap_resolver"}'),
    ]
    for system, prompt in cases:
        assert codex_adapter._uses_generic_agent_output(system=system, prompt=prompt) is True


def test_codex_run_sends_utf8_stdin_to_cli(tmp_path: Path) -> None:
    stdin_bytes = tmp_path / "stdin.bin"
    fake = tmp_path / "fake_codex.py"
    fake.write_text(
        "\n".join(
            [
                "import sys",
                f"stdin_bytes = {str(stdin_bytes)!r}",
                "last_message = None",
                "for index, arg in enumerate(sys.argv):",
                "    if arg == '--output-last-message' and index + 1 < len(sys.argv):",
                "        last_message = sys.argv[index + 1]",
                "with open(stdin_bytes, 'wb') as handle:",
                "    handle.write(sys.stdin.buffer.read())",
                "if last_message:",
                "    with open(last_message, 'w', encoding='utf-8') as handle:",
                "        handle.write('{\"ok\": true}')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    prompt = "prime order café 数学"
    last_message = tmp_path / "last_message.txt"

    text, return_code, _stdout = codex_adapter._run_codex(
        [sys.executable, str(fake), "--output-last-message", str(last_message)],
        full_prompt=prompt,
        last_message=last_message,
        timeout=30,
    )

    assert return_code == 0
    assert json.loads(text)["ok"] is True
    assert stdin_bytes.read_bytes() == prompt.encode("utf-8")


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

    assert client.model == "gpt-5.5-medium"
    assert client.timeout == 360
    assert client.complete_json(system="Return JSON only.", prompt="{}")["verdict"] == "needs_sources"


@pytest.mark.parametrize("payload", ["[1, 2, 3]", '"a string"', "42", "null"])
def test_external_client_non_dict_json_fails_with_clean_runtimeerror(
    tmp_path: Path, monkeypatch, payload: str
) -> None:
    # A well-formed agent must return a JSON OBJECT. Valid-but-non-dict JSON
    # (list / string / number / null) must fail closed with a clear RuntimeError,
    # NOT an opaque AttributeError on data.get(...). (Dogfood: Case 2/4 adapter
    # robustness finding.)
    adapter = tmp_path / "agent.py"
    adapter.write_text(
        "import sys\n" f"sys.stdout.write({payload!r})\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", f"{sys.executable} {adapter}")
    client = ExternalCommandClient(model="gpt-4.1")
    with pytest.raises(RuntimeError, match="did not return a JSON object"):
        client.complete_json(system="Return JSON only.", prompt="{}")


def test_external_client_valid_json_object_still_works(tmp_path: Path, monkeypatch) -> None:
    # The guard must not regress the normal dict path: a JSON object with no
    # "output" wrapper is returned verbatim.
    adapter = tmp_path / "agent.py"
    adapter.write_text(
        "import sys\n" 'sys.stdout.write(\'{"verdict": "needs_sources"}\')\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", f"{sys.executable} {adapter}")
    client = ExternalCommandClient(model="gpt-4.1")
    assert client.complete_json(system="x", prompt="{}")["verdict"] == "needs_sources"


def test_external_client_runs_bare_python_script_command(tmp_path: Path, monkeypatch) -> None:
    adapter = tmp_path / "agent.py"
    adapter.write_text(
        "import sys\n" 'sys.stdout.write(\'{"verdict": "needs_sources"}\')\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", str(adapter))

    client = ExternalCommandClient(model="gpt-4.1")

    assert Path(client.command[0]) == Path(sys.executable)
    assert client.command[1] == str(adapter)
    assert client.complete_json(system="x", prompt="{}")["verdict"] == "needs_sources"


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


# ---------------------------------------------------------------------------
# PR1a: schema-aware fail-closed fallback for back-half roles
# ---------------------------------------------------------------------------

from espresso_proof.agents.llm_agents import (
    AGENT_OUTPUT_SCHEMA,
    LINE_NUMBERED_PROOF_OUTPUT_SCHEMA,
    line_numbered_proof_writer_system_prompt,
)


def test_codex_adapter_fails_closed_for_writer_schema_on_cli_failure(tmp_path: Path) -> None:
    # [hard-negative] writer role + dying codex CLI: no prover-shaped fallback
    # may reach stdout; exit 1 and log parse_status=error_propagated instead.
    fake_codex = _write_fake_codex(tmp_path, message="boom", return_code=3)
    completed = _run_adapter(
        tmp_path=tmp_path,
        system=line_numbered_proof_writer_system_prompt(),
        prompt="write the line-numbered proof",
        fake_codex=fake_codex,
        schema=LINE_NUMBERED_PROOF_OUTPUT_SCHEMA,
    )
    assert completed.returncode == 1
    assert "FATAL" in completed.stderr
    assert completed.stdout.strip() == ""
    log_dir = tmp_path / "logs"
    events = [
        json.loads(line)
        for path in sorted(log_dir.glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    finish = [e for e in events if e.get("event") == "finish" or e.get("parse_status")]
    assert any(e.get("parse_status") == "error_propagated" for e in finish)


def test_codex_adapter_prover_fallback_unchanged_on_cli_failure(tmp_path: Path) -> None:
    # Regression guard: prover-schema failures keep the exit-0 fallback contract.
    fake_codex = _write_fake_codex(tmp_path, message="boom", return_code=3)
    completed = _run_adapter(
        tmp_path=tmp_path,
        system="You are the Espresso prover.",
        prompt="attempt the route",
        fake_codex=fake_codex,
        schema=AGENT_OUTPUT_SCHEMA,
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert "could not produce usable JSON" in payload["summary"]
