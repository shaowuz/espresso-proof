"""Tests for espresso_proof/agents/espresso_core_adapter.py (espresso-core prover backend).

Phase A contract (docs/engineering/ESPRESSO_CORE_INTEGRATION.md): NO network,
NO real espresso-core execution, NO new deps. Mock mode reads a fixture core
run directory; every translation must be schema-valid, candidate-only JSON
that the espresso_proof kernel then re-validates and contains.

Conventions follow tests/orchestration/test_codex_agent_adapter.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from espresso_proof.agents import espresso_core_adapter as adapter
from espresso_proof.agents.llm_agents import (
    AGENT_OUTPUT_SCHEMA,
    LINE_NUMBERED_PROOF_OUTPUT_SCHEMA,
    collect_unsafe_language,
    line_numbered_proof_writer_system_prompt,
    prover_system_prompt,
)
from espresso_proof.kernel.models import LineNumberedProof

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "espresso_proof" / "agents" / "espresso_core_adapter.py"

_CORE_ENV_KEYS = (
    "ESPRESSO_CORE_MODE",
    "ESPRESSO_CORE_FIXTURE_RUN",
    "ESPRESSO_CORE_ROOT",
    "ESPRESSO_CORE_CLI",
    "ESPRESSO_CORE_CONFIG",
    "ESPRESSO_CORE_RUNS_DIR",
    "ESPRESSO_CORE_FALLBACK_AGENT_COMMAND",
)


# ---------------------------------------------------------------------------
# Fixture: a small realistic espresso-core runs/<id>/ tree (mirrors the layout
# produced by `espresso-core solve` and pinned in the run contract: run_summary,
# state, final_proof.md, global_verification.yaml, verification/, claim_lock/,
# plans/, strategies/, research_materials/best_materials/, best_verified/).
# ---------------------------------------------------------------------------
def _write_core_run(
    tmp_path: Path,
    *,
    proof_status: str = "proved",
    global_verdict: str = "pass",
    issues: list[dict] | None = None,
    unresolved_gaps: list[str] | None = None,
) -> Path:
    run_dir = tmp_path / "core-run"
    for sub in (
        "claim_lock",
        "plans",
        "strategies",
        "verification",
        "best_verified",
        "research_materials/best_materials",
    ):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)

    def _dump(rel: str, data: dict) -> None:
        (run_dir / rel).write_text(yaml.safe_dump(data), encoding="utf-8")

    _dump(
        "run_summary.yaml",
        {
            "run_id": "even-square-fixture",
            "problem_id": "CLAIM_MAIN",
            "status": proof_status,
            "proof_status": proof_status,
            "soundness_level": "artifact_valid",
            "phase": "live_complete",
            "node_count": 1,
            "status_counts": {proof_status: 1},
            "nodes": {"PROP1": {"status": proof_status, "blocked_by": [], "attempts": 1}},
            "raw_stage": "run_complete",
        },
    )
    _dump(
        "global_verification.yaml",
        {
            "target_id": "final_proof",
            "verifier": "GlobalVerifierQuorum",
            "verdict": global_verdict,
            "issues": issues or [],
            "requires_error_clinic": False,
        },
    )
    _dump(
        "verification/direct_final_proof.verifier-001.attempt-001.yaml",
        {
            "target_id": "final_proof",
            "verifier": "GlobalVerifier",
            "verdict": global_verdict,
            "issues": issues or [],
        },
    )
    _dump(
        "claim_lock/claim_lock.yaml",
        {
            "claim_id": "CLAIM_MAIN",
            "status": "locked",
            "locked_statement": "Prove that if `n` is an even integer, then `n^2` is even.",
            "hypotheses": [
                "An integer `m` is even if there exists an integer `r` such that `m = 2r`."
            ],
            "conclusion": "n^2 is even",
            "ambiguities": [
                {
                    "severity": "minor",
                    "issue": "Domain of `n` should be pinned to the integers.",
                    "resolution": "",
                    "resolution_status": "open",
                }
            ],
            "strategy_origin": "human",
        },
    )
    _dump(
        "plans/proof_route.yaml",
        {"claim_id": "CLAIM_MAIN", "route": "direct_proof", "difficulty": "easy"},
    )
    _dump(
        "plans/proposition_plan.yaml",
        {
            "locked_conclusion": "n^2 is even",
            "propositions": [
                {
                    "id": "PROP1",
                    "kind": "assembly",
                    "statement": "Directly derive the locked conclusion for even `n`.",
                    "depends_on": [],
                }
            ],
        },
    )
    _dump(
        "strategies/selected_strategy.yaml",
        {
            "strategy_id": "DIRECT_STRATEGY_CLAIM_MAIN",
            "human_strategy": "Write n = 2k and expand the square.",
            "strategy_origin": "human",
            "material_gaps": [],
        },
    )
    _dump(
        "research_materials/best_materials/promotion.yaml",
        {
            "material_status": "candidate",
            "trusted_for_proof": False,
            "unresolved_gaps": unresolved_gaps or [],
        },
    )
    (run_dir / "final_proof.md").write_text(
        "# Final Proof\n\n"
        "Let n be an even integer, so n = 2k for an integer k.\n\n"
        "Then n^2 = (2k)^2 = 4k^2 = 2(2k^2).\n\n"
        "Since 2k^2 is an integer, n^2 is even.\n",
        encoding="utf-8",
    )
    (run_dir / "best_verified/PROP1.md").write_text(
        "# Lemma\n\nSquaring 2k gives 4k^2, which is twice the integer 2k^2.\n",
        encoding="utf-8",
    )
    return run_dir


def _prover_prompt() -> str:
    return json.dumps(
        {
            "role": "prover_loop_round",
            "protocol_rules": ["candidate routes only"],
            "context": {
                "claim": {
                    "id": "CLAIM_MAIN",
                    "raw_text": "If n is an even integer then n^2 is even.",
                    "normalized_statement": "For every even integer n, n^2 is even.",
                    "hypotheses": [{"name": "n is an even integer", "status": "assumed", "source": "user"}],
                    "conclusion": "n^2 is even",
                },
                "strategy_brief": {
                    "top_strategies": [
                        {
                            "id": "strategy_route_001",
                            "title": "Witness expansion",
                            "why_this_route": "Write n = 2k and expand the square directly.",
                            "next_loop_plan": "Carry the witness through the final parity step.",
                        }
                    ]
                },
                "previous_prover_loop_attempt": {"strategy_id": "strategy_route_001"},
                "source_grounding": {
                    "source_refs": [
                        {
                            "id": "SRC1",
                            "status": "source_backed",
                            "statement_or_summary": "An integer m is even iff m = 2r for some integer r.",
                            "title": "Elementary Number Theory",
                            "locator": {"kind": "theorem", "value": "Definition 1.2"},
                        }
                    ]
                },
            },
            "required_json_schema": AGENT_OUTPUT_SCHEMA,
        },
        indent=2,
    )


def _run_adapter(
    *,
    system: str,
    prompt: str,
    env_overrides: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _CORE_ENV_KEYS}
    env.update(env_overrides)
    request = {
        "system": system,
        "prompt": prompt,
        "provider": "external",
        "model": "espresso-core-backend",
        "required_json_schema": {},
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Role detection
# ---------------------------------------------------------------------------
def test_role_detection_routes_only_prover_family_to_core() -> None:
    assert adapter.detect_role(system=prover_system_prompt(), prompt=_prover_prompt()) == "prover"
    assert adapter.detect_role(system="You are Espresso's prover agent.", prompt='{"role": "prover"}') == "prover"
    assert (
        adapter.detect_role(
            system=line_numbered_proof_writer_system_prompt(),
            prompt='{"role": "line_numbered_proof_writer"}',
        )
        == "proof_writer"
    )
    # Reviewer prompts EMBED the prover attempt ('"role": "prover"' appears
    # nested in context); they must still stay independent of the core backend.
    reviewer_prompt = json.dumps(
        {
            "role": "round_2_strategy_reviewer",
            "context": {"prover_attempt": {"role": "prover", "status": "needs_human"}},
        }
    )
    assert (
        adapter.detect_role(system="You are Espresso strategy reviewer agent.", prompt=reviewer_prompt)
        == "other"
    )
    assert (
        adapter.detect_role(
            system="PROOF-CONSENSUS REVIEW (advisory mock check, NOT machine verification).",
            prompt='{"role": "consensus_line_proof_reviewer"}',
        )
        == "other"
    )
    assert (
        adapter.detect_role(
            system="You are the Espresso Proof Literature-Review agent.", prompt="{}"
        )
        == "other"
    )


# ---------------------------------------------------------------------------
# Prover-round translation
# ---------------------------------------------------------------------------
def test_prover_round_translation_is_schema_valid_and_candidate_only(tmp_path: Path) -> None:
    run_dir = _write_core_run(tmp_path, proof_status="proved", global_verdict="pass")

    completed = _run_adapter(
        system=prover_system_prompt(),
        prompt=_prover_prompt(),
        env_overrides={
            "ESPRESSO_CORE_MODE": "mock",
            "ESPRESSO_CORE_FIXTURE_RUN": str(run_dir),
        },
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    for key in AGENT_OUTPUT_SCHEMA["required"]:
        assert key in payload, f"missing required key {key}"
    assert isinstance(payload["summary"], str) and payload["summary"]
    for list_key in ("steps", "source_requests", "missing_hypotheses", "risks"):
        assert isinstance(payload[list_key], list)
        assert all(isinstance(item, str) for item in payload[list_key])

    # Core "proved" maps to plausible_direction + candidate wording, NEVER stronger.
    assert payload["verdict"] == "plausible_direction"
    assert payload["recommended_human_action"] == "continue"
    assert "candidate" in payload["summary"].lower()
    # The kernel's own unsafe-language collector must find nothing.
    assert collect_unsafe_language(payload) == []
    # Raw core status vocabulary must not leak anywhere in the reply.
    assert '"proved"' not in completed.stdout.lower()
    # Standing honesty risk about the LLM quorum is always present.
    assert any("quorum" in risk.lower() for risk in payload["risks"])

    meta = payload["_espresso_core_adapter"]
    assert meta["backend"] == "espresso-core"
    assert meta["role"] == "prover"
    assert meta["parse_status"] == "ok"
    assert meta["fallback_used"] is False


@pytest.mark.parametrize(
    ("proof_status", "expected_verdict", "expected_action"),
    [
        ("needs_human", "blocked", "stop"),
        ("partial", "needs_revision", "revise"),
        ("failed", "needs_revision", "revise"),
    ],
)
def test_non_accepted_core_outcomes_map_to_conservative_verdicts(
    tmp_path: Path, proof_status: str, expected_verdict: str, expected_action: str
) -> None:
    run_dir = _write_core_run(
        tmp_path,
        proof_status=proof_status,
        global_verdict="uncertain",
        issues=[
            {
                "severity": "critical",
                "type": "logical_gap",
                "message": "The parity step is not justified for negative n.",
            }
        ],
        unresolved_gaps=["Cite the definition of evenness with an exact locator."],
    )

    completed = _run_adapter(
        system=prover_system_prompt(),
        prompt=_prover_prompt(),
        env_overrides={
            "ESPRESSO_CORE_MODE": "mock",
            "ESPRESSO_CORE_FIXTURE_RUN": str(run_dir),
        },
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["verdict"] == expected_verdict
    assert payload["recommended_human_action"] == expected_action
    # Failed verifications -> risks; missing citations -> source_requests.
    assert any("parity step" in risk for risk in payload["risks"])
    assert any("exact locator" in req for req in payload["source_requests"])
    # Claim-lock ambiguities surface as missing hypotheses.
    assert any("Domain of `n`" in item for item in payload["missing_hypotheses"])
    assert collect_unsafe_language(payload) == []


# ---------------------------------------------------------------------------
# Proof-writer translation
# ---------------------------------------------------------------------------
def test_proof_writer_translation_produces_valid_line_numbered_proof(tmp_path: Path) -> None:
    run_dir = _write_core_run(tmp_path)

    completed = _run_adapter(
        system=line_numbered_proof_writer_system_prompt(),
        prompt=json.dumps({"role": "line_numbered_proof_writer", "context": {}}),
        env_overrides={
            "ESPRESSO_CORE_MODE": "mock",
            "ESPRESSO_CORE_FIXTURE_RUN": str(run_dir),
        },
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    for key in LINE_NUMBERED_PROOF_OUTPUT_SCHEMA["required"]:
        assert key in payload
    assert payload["proof_kind"] == "espresso_core_direct_proof"
    assert len(payload["lines"]) >= 3  # hypotheses + lemma + final-proof steps
    line_schema = LINE_NUMBERED_PROOF_OUTPUT_SCHEMA["properties"]["lines"]["items"]
    for line in payload["lines"]:
        for key in line_schema["required"]:
            assert key in line, f"line missing {key}"
        assert line["statement"].strip() and line["justification"].strip()

    # The kernel's OWN model (the trust gate the proof writer runs) accepts it,
    # with status pinned advisory and a well-founded depends_on chain.
    record = LineNumberedProof.model_validate(
        {
            "schema_version": 1,
            "claim_id": "CLAIM_MAIN",
            "strategy_id": "strategy_route_001",
            "proof_kind": payload["proof_kind"],
            "status": "advisory",
            "lines": payload["lines"],
            "generated_at": "2026-07-01T00:00:00+00:00",
            "notes": [],
        }
    )
    assert record.status == "advisory"
    # Imported lemma lines carry an explicit unchecked_gap for the LLM quorum,
    # and the final line acknowledges that no machine checking happened.
    all_deps = [dep for line in record.lines for dep in line.dependencies]
    assert any(dep.type == "unchecked_gap" and dep.ref.startswith("core_quorum_") for dep in all_deps)
    assert any("quorum" in gap.lower() for gap in record.lines[-1].gaps)
    assert not any(dep.machine_verified or dep.source_grounded for dep in all_deps)
    # The kernel's unsafe-language collector finds nothing in any emitted text.
    flattened_line_text = {
        "summary": payload["proof_kind"],
        "steps": [
            f"{line['statement']} {line['justification']} {' '.join(line['gaps'])}"
            for line in payload["lines"]
        ],
        "risks": [dep["role"] for line in payload["lines"] for dep in line["dependencies"]],
    }
    assert collect_unsafe_language(flattened_line_text) == []


# ---------------------------------------------------------------------------
# Delegation of non-prover roles
# ---------------------------------------------------------------------------
def _write_fake_fallback(tmp_path: Path) -> tuple[Path, Path]:
    stdin_log = tmp_path / "fallback_stdin.json"
    fake = tmp_path / "fake_fallback_agent.py"
    fake.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json, sys",
                f"stdin_log = {str(stdin_log)!r}",
                "raw = sys.stdin.read()",
                "open(stdin_log, 'w', encoding='utf-8').write(raw)",
                "json.dump({'summary': 'fallback adapter answered.', 'steps': [],",
                "           'source_requests': [], 'missing_hypotheses': [], 'risks': [],",
                "           'verdict': 'needs_sources', 'recommended_human_action': 'revise',",
                "           'marker': 'fallback-agent'}, sys.stdout)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return fake, stdin_log


def test_non_prover_roles_delegate_unchanged_to_fallback_adapter(tmp_path: Path) -> None:
    fake, stdin_log = _write_fake_fallback(tmp_path)
    system = "You are the Espresso Proof Literature-Review agent. Return JSON only."
    prompt = '{"claim": "Every group of order p^2 is abelian."}'

    completed = _run_adapter(
        system=system,
        prompt=prompt,
        env_overrides={
            "ESPRESSO_CORE_MODE": "mock",
            "ESPRESSO_CORE_FIXTURE_RUN": "/nonexistent",
            "ESPRESSO_CORE_FALLBACK_AGENT_COMMAND": str(fake),
        },
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    # Passed through UNCHANGED: fallback's own fields, no adapter meta injected.
    assert payload["marker"] == "fallback-agent"
    assert payload["verdict"] == "needs_sources"
    assert "_espresso_core_adapter" not in payload
    forwarded = json.loads(stdin_log.read_text(encoding="utf-8"))
    assert forwarded["system"] == system
    assert forwarded["prompt"] == prompt


def test_prover_role_with_mode_off_delegates_to_fallback(tmp_path: Path) -> None:
    fake, _ = _write_fake_fallback(tmp_path)

    completed = _run_adapter(
        system=prover_system_prompt(),
        prompt=_prover_prompt(),
        env_overrides={"ESPRESSO_CORE_FALLBACK_AGENT_COMMAND": str(fake)},
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["marker"] == "fallback-agent"


# ---------------------------------------------------------------------------
# Conservative fallback (never crash the round)
# ---------------------------------------------------------------------------
def test_malformed_core_output_returns_conservative_fallback(tmp_path: Path) -> None:
    run_dir = tmp_path / "broken-run"
    run_dir.mkdir()
    (run_dir / "run_summary.yaml").write_text("not: [valid: yaml", encoding="utf-8")

    completed = _run_adapter(
        system=prover_system_prompt(),
        prompt=_prover_prompt(),
        env_overrides={
            "ESPRESSO_CORE_MODE": "mock",
            "ESPRESSO_CORE_FIXTURE_RUN": str(run_dir),
        },
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["verdict"] == "needs_revision"
    assert payload["recommended_human_action"] == "revise"
    assert "could not produce usable JSON" in payload["summary"]
    meta = payload["_espresso_core_adapter"]
    assert meta["fallback_used"] is True
    assert meta["fallback_reason"]
    assert collect_unsafe_language(payload) == []


def test_missing_fallback_command_returns_conservative_fallback() -> None:
    completed = _run_adapter(
        system="You are the Espresso Proof Examples agent.",
        prompt="{}",
        env_overrides={},
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["verdict"] == "needs_revision"
    assert payload["_espresso_core_adapter"]["fallback_used"] is True


# ---------------------------------------------------------------------------
# Live-mode command construction (built, never executed here)
# ---------------------------------------------------------------------------
def test_live_mode_pins_exact_espresso_core_cli_flags(monkeypatch) -> None:
    monkeypatch.setenv("ESPRESSO_CORE_ROOT", "/opt/espresso-core")
    monkeypatch.setenv("ESPRESSO_CORE_CLI", "/opt/espresso-core/.venv/bin/espresso-core")
    monkeypatch.setenv("ESPRESSO_CORE_CONFIG", "/opt/espresso-core/config.host.yaml")

    solve = adapter.build_solve_command(Path("/runs/x/problem.md"), Path("/runs/x/run"))
    assert solve == [
        "/opt/espresso-core/.venv/bin/espresso-core",
        "solve",
        "/runs/x/problem.md",
        "/runs/x/run",
        "--mode",
        "live",
        "--schemas-dir",
        "/opt/espresso-core/schemas",
        "--config",
        "/opt/espresso-core/config.host.yaml",
        "--force",
    ]

    verify = adapter.build_verify_command(Path("/runs/x/run"))
    assert verify == [
        "/opt/espresso-core/.venv/bin/espresso-core",
        "verify-run",
        "/runs/x/run",
        "--schemas-dir",
        "/opt/espresso-core/schemas",
    ]


def test_problem_markdown_bundle_carries_claim_strategy_and_sources() -> None:
    text = adapter.build_problem_markdown(_prover_prompt())
    assert "## Claim" in text
    assert "For every even integer n, n^2 is even." in text
    # The HUMAN-chosen strategy is what makes core treat the run as
    # strategy_origin=human instead of stalling at needs_human.
    assert "## Human strategy" in text
    assert "Write n = 2k and expand the square directly." in text
    assert "## Known results" in text
    assert "Elementary Number Theory" in text
    assert "## Sources or references" in text

    # Without a chosen strategy the section is honestly absent (core will then
    # stop at needs_human until approve-strategy, mapped to verdict=blocked).
    stripped = json.loads(_prover_prompt())
    stripped["context"].pop("strategy_brief")
    stripped["context"].pop("previous_prover_loop_attempt")
    text_without = adapter.build_problem_markdown(json.dumps(stripped))
    assert "## Human strategy" not in text_without
