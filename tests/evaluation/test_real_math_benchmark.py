from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.cli import app
from espresso_proof.evaluation.real_math_benchmark import (
    build_real_math_capability_report,
    case_from_run,
)
from espresso_proof.kernel.io import read_yaml, write_text, write_yaml
from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures
from espresso_proof.kernel.validators import validate_run
from espresso_proof.literature.pdf_ingestion import ingest_source_for_theorems
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.verification.finite_group_verifier import cyclic_group_table
from espresso_proof.verification.proof_verification import verify_identity_table_in_run
from espresso_proof.verification.source_verification import ground_run

FIXTURES = Path("tests/fixtures")
THM = (
    "Theorem 1.2. Exact literature statement supporting the Hodge-theoretic step "
    "over base field C for cubic fourfolds."
)


def _case(run_dir: Path, case_id: str = "case") -> dict:
    return {
        "case_id": case_id,
        "run_dir": run_dir,
        "provider": "stub",
        "model_fingerprint": "stub-front-half-v0",
        "prompt_version": "test-prompt-v1",
        "repeat_count": 1,
    }


def _external_agent_command(tmp_path: Path) -> str:
    script = tmp_path / "external_agent.py"
    script.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "json.loads(sys.stdin.read())",
                "json.dump({",
                "  'summary': 'External command returned candidate-only output.',",
                "  'steps': ['Check exact source hypotheses.'],",
                "  'source_requests': ['Exact theorem locator.'],",
                "  'missing_hypotheses': ['Genericity condition.'],",
                "  'risks': ['Route may be too narrow.'],",
                "  'verdict': 'needs_sources',",
                "  'recommended_human_action': 'revise',",
                "}, sys.stdout)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return f"{sys.executable} {script}"


def _codex_adapter_command(tmp_path: Path) -> str:
    script = tmp_path / "espresso_codex_agent.py"
    script.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "json.loads(sys.stdin.read())",
                "json.dump({",
                "  'summary': 'Codex adapter returned candidate-only output.',",
                "  'steps': ['Check exact source hypotheses.'],",
                "  'source_requests': ['Exact theorem locator.'],",
                "  'missing_hypotheses': ['Genericity condition.'],",
                "  'risks': ['Route may be too narrow.'],",
                "  'verdict': 'needs_sources',",
                "  'recommended_human_action': 'revise',",
                "  '_espresso_codex_adapter': {",
                "    'adapter': 'espresso_codex_agent',",
                "    'request_id': 'benchmark-request',",
                "    'parse_status': 'ok',",
                "    'fallback_used': False,",
                "    'fallback_reason': '',",
                "    'model': 'gpt-5.5',",
                "    'effort': 'medium'",
                "  }",
                "}, sys.stdout)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return f"{sys.executable} {script}"


def _make_claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)


def _basic_run(tmp_path: Path) -> Path:
    run_dir = run_mock_critique(
        "real_math_benchmark",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    _make_claim_clear(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief_path = run_dir / manifest["artifacts"]["strategy_brief"]
    brief = read_yaml(brief_path)
    brief["top_strategies"][0]["status"] = "promising"
    brief["top_strategies"][0]["score"] = 60
    brief["top_strategies"][0]["source_status"] = "candidate"
    write_yaml(brief_path, brief)
    return run_dir


def _hodge_run(tmp_path: Path) -> Path:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    _make_claim_clear(run_dir)
    return run_dir


def _ground_hodge_source(run_dir: Path) -> None:
    src = run_dir / "src.txt"
    src.write_text(THM, encoding="utf-8")
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    extraction = ingest_source_for_theorems(
        claim_id=claim["id"],
        source=str(src),
        source_id="src_real",
        title="Real Source",
        authors=["J.-P. Serre"],
        source_type="paper",
    )
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = (
        "theorem_extraction.yaml"
    )
    write_yaml(run_dir / "theorem_extraction.yaml", extraction)
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    ground_run(run_dir=run_dir, theorem_extraction_path=run_dir / "theorem_extraction.yaml")


def _append_human_attestation(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    report = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": "2026-06-20T00:00:00+00:00",
        "entries": [
            {
                "id": "human_1",
                "verifier_type": "human_checked",
                "claim_id": claim["id"],
                "target": "full solution",
                "input_summary": "human attestation record",
                "result": "human attested externally",
                "trusted": False,
                "confirmed": True,
                "earned_status": "human_attested",
                "scope_note": "Human attestation is separate from machine proof.",
            }
        ],
        "notes": ["Benchmark fixture for separating human attestation."],
    }
    write_yaml(run_dir / "proof_verification.yaml", report)
    manifest.setdefault("optional_artifacts", {})["proof_verification"] = (
        "proof_verification.yaml"
    )
    write_yaml(run_dir / "run_manifest.yaml", manifest)


def _row(report: dict) -> dict:
    assert len(report["cases"]) == 1
    return report["cases"][0]


def test_real_math_benchmark_separates_valid_run_from_solution_claims(
    tmp_path: Path,
) -> None:
    run_dir = _basic_run(tmp_path)
    report = build_real_math_capability_report([_case(run_dir, "valid_route")])
    row = _row(report)

    assert report["schema_version"] == 1
    assert report["metrics"]["valid_run_rate"] == 1.0
    assert report["metrics"]["useful_route_rate"] == 1.0
    assert report["metrics"]["real_agent_valid_run_rate"] == 0.0
    assert row["valid_run"] is True
    assert row["provider_ready"] is False
    assert row["adapter_fallback_used"] is False
    assert row["real_agent_valid_run"] is False
    assert row["useful_route_found"] is True
    assert row["checked_scoped_fact"] is False
    assert row["source_supported_argument"] is False
    assert row["formal_solution"] is False
    assert row["human_attested_solution"] is False
    assert row["agent_solved"] is False
    assert row["provider"] == "stub"
    assert row["model_fingerprint"] == "stub-front-half-v0"
    assert row["prompt_version"] == "test-prompt-v1"
    assert row["repeat_count"] == 1


def test_real_math_benchmark_counts_useful_witness_route_below_proof_threshold(
    tmp_path: Path,
) -> None:
    claim_path = tmp_path / "tensor_counterexample.md"
    claim_path.write_text(
        "Find a counterexample that tensor product is left exact.\n",
        encoding="utf-8",
    )
    run_dir = run_mock_critique("tensor_benchmark", claim_path, tmp_path)
    _make_claim_clear(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    route_patch = {
        "title": "Tensor Z/2Z counterexample witness",
        "score": 40,
        "status": "fragile",
        "why_this_route": "Use the short exact sequence Z --multiplication by 2--> Z -> Z/2Z.",
        "main_risk": "The witness still needs scoped checking.",
        "source_status": "candidate",
        "next_loop_plan": "Check the mod 2 tensor-induced map is non-injective.",
    }
    synthesis_path = run_dir / manifest["artifacts"]["strategy_synthesis"]
    synthesis = read_yaml(synthesis_path)
    synthesis["routes"][0].update(route_patch)
    write_yaml(synthesis_path, synthesis)
    brief_path = run_dir / manifest["artifacts"]["strategy_brief"]
    brief = read_yaml(brief_path)
    brief["top_strategies"][0].update(route_patch)
    write_yaml(brief_path, brief)
    validate_run(run_dir)

    report = build_real_math_capability_report([_case(run_dir, "tensor_witness")])
    row = _row(report)

    assert row["useful_route_found"] is True
    assert row["checked_scoped_fact"] is False
    assert row["formal_solution"] is False


def test_real_math_benchmark_counts_source_supported_argument_only_after_grounding(
    tmp_path: Path,
) -> None:
    run_dir = _hodge_run(tmp_path)
    _ground_hodge_source(run_dir)
    report = build_real_math_capability_report([_case(run_dir, "source_supported")])
    row = _row(report)

    assert row["valid_run"] is True
    assert row["source_supported_argument"] is True
    assert row["agent_solved"] is False
    assert row["solved_full_claim"] is False
    assert row["formal_solution"] is False
    assert row["human_attested_solution"] is False
    assert report["metrics"]["source_supported_argument_rate"] == 1.0
    assert report["metrics"]["agent_solved_rate"] == 0.0


def test_real_math_benchmark_blocks_source_solution_when_applicability_not_overall_clear(
    tmp_path: Path,
) -> None:
    run_dir = _hodge_run(tmp_path)
    _ground_hodge_source(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    applicability_path = run_dir / manifest["optional_artifacts"]["theorem_applicability"]
    applicability = read_yaml(applicability_path)
    applicability["overall_status"] = "blocked"
    applicability["ledger_usages"] = [
        {
            "proof_step_id": "ledger:missing_genericity",
            "source_ref_id": "missing_source_ref",
            "theorem_id": None,
            "theorem_label": None,
            "theorem_statement": "Genericity condition for the cited theorem",
            "locator": {"kind": "unknown", "value": "missing_genericity"},
            "source_status": "unverified",
            "conclusion_status": "unclear",
            "conditions": [
                {
                    "id": "cond_001",
                    "text": "The cubic fourfold is very general.",
                    "condition_type": "genericity",
                    "status": "not_checked",
                    "evidence": "Benchmark trap: unresolved theorem condition ledger.",
                    "definition_locator": None,
                }
            ],
            "applicability_status": "unclear",
            "blocks_source_backed": True,
            "notes": ["Unresolved ledger blocker; source support is not a full solution."],
        }
    ]
    write_yaml(applicability_path, applicability)
    refresh_final_report_disclosures(run_dir)
    validate_run(run_dir)

    report = build_real_math_capability_report(
        [_case(run_dir, "source_supported_with_blocked_applicability")]
    )
    row = _row(report)

    assert row["valid_run"] is True
    assert row["source_supported_argument"] is False
    assert row["agent_solved"] is False
    assert report["metrics"]["source_supported_argument_rate"] == 0.0
    assert report["metrics"]["agent_solved_rate"] == 0.0


def test_real_math_benchmark_counts_scoped_machine_checks_without_global_proof(
    tmp_path: Path,
) -> None:
    run_dir = _basic_run(tmp_path)
    verify_identity_table_in_run(
        run_dir,
        table=cyclic_group_table(4),
        identity="commutative",
        entry_id="bench_commutative",
    )
    report = build_real_math_capability_report([_case(run_dir, "scoped_check")])
    row = _row(report)

    assert row["valid_run"] is True
    assert row["checked_scoped_fact"] is True
    assert row["agent_solved"] is False
    assert row["solved_full_claim"] is False
    assert row["formal_solution"] is False
    assert row["human_attested_solution"] is False
    assert report["metrics"]["checked_scoped_fact_rate"] == 1.0
    assert report["metrics"]["agent_solved_rate"] == 0.0


def test_real_math_benchmark_keeps_human_attestation_out_of_agent_solved(
    tmp_path: Path,
) -> None:
    # V0 has no human-attestation artifact, so a recorded human_attested entry
    # is unverifiable say-so: the kernel rejects the whole run (fail closed),
    # and an invalid run can never count as ANY kind of solution — least of
    # all an agent-solved one.
    from espresso_proof.validation.errors import KernelValidationError

    run_dir = _basic_run(tmp_path)
    _append_human_attestation(run_dir)
    with pytest.raises(KernelValidationError, match="human_attested is not earnable"):
        validate_run(run_dir)

    report = build_real_math_capability_report([_case(run_dir, "human_attested")])
    row = _row(report)

    assert row["valid_run"] is False
    assert row["human_attested_solution"] is False
    assert row["agent_solved"] is False
    assert row["checked_scoped_fact"] is False
    assert row["source_supported_argument"] is False
    assert row["formal_solution"] is False
    assert row["solved_full_claim"] is False
    assert report["metrics"]["human_attested_solution_rate"] == 0.0
    assert report["metrics"]["agent_solved_rate"] == 0.0


def test_real_math_benchmark_claim_confirmation_is_not_solution_attestation(
    tmp_path: Path,
) -> None:
    run_dir = _basic_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_path = run_dir / manifest["artifacts"]["locked_claim"]
    claim = read_yaml(claim_path)
    claim["human_confirmed"] = True
    write_yaml(claim_path, claim)
    validate_run(run_dir)

    report = build_real_math_capability_report([_case(run_dir, "claim_confirmed")])
    row = _row(report)

    assert row["valid_run"] is True
    assert row["human_attested_solution"] is False
    assert row["agent_solved"] is False
    assert report["metrics"]["human_attested_solution_rate"] == 0.0
    assert report["metrics"]["agent_solved_rate"] == 0.0


def test_real_math_benchmark_overclaim_trap_does_not_become_solution(
    tmp_path: Path,
) -> None:
    run_dir = _basic_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    final_path = run_dir / manifest["artifacts"]["final_report"]
    write_text(final_path, final_path.read_text(encoding="utf-8") + "\nWe proved the theorem. proof-close.\n")

    report = build_real_math_capability_report([_case(run_dir, "overclaim_trap")])
    row = _row(report)

    assert row["overclaim"] is True
    assert row["overclaim_phrase"]
    assert row["agent_solved"] is False
    assert row["source_supported_argument"] is False
    assert row["checked_scoped_fact"] is False
    assert row["formal_solution"] is False
    assert row["human_attested_solution"] is False
    assert report["metrics"]["overclaim_count"] == 1


def test_real_math_benchmark_requires_model_and_prompt_metadata(tmp_path: Path) -> None:
    run_dir = _basic_run(tmp_path)
    bad = _case(run_dir)
    bad.pop("model_fingerprint")
    with pytest.raises(ValueError, match="model_fingerprint"):
        build_real_math_capability_report([bad])


def test_real_math_benchmark_counts_real_external_run_without_solution_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", _external_agent_command(tmp_path))
    result = CliRunner().invoke(
        app,
        [
            "run-flow",
            "--input",
            str(FIXTURES / "promising_but_unproved" / "input.md"),
            "--project",
            "real_agent_smoke",
            "--literature-mode",
            "local",
            "--provider",
            "external",
            "--model",
            "json-agent-test",
            "--reviewer-set",
            "espresso",
            "--agent-network-preflight",
            "--output-root",
            str(tmp_path / "runs"),
        ],
    )
    assert result.exit_code == 0, result.output
    match = re.search(r"Wrote run artifacts to\s+(.+)", result.output)
    assert match, result.output
    run_dir = Path(match.group(1).strip())
    validate_run(run_dir)

    case = case_from_run(
        run_dir,
        case_id="real_external_smoke",
        prompt_version="test-real-agent-smoke-v1",
        repeat_count=1,
    )
    report = build_real_math_capability_report([case])
    row = _row(report)

    assert row["provider"] == "external"
    assert row["model_fingerprint"] == "json-agent-test"
    assert row["valid_run"] is True
    assert row["provider_ready"] is True
    assert row["adapter_fallback_used"] is False
    assert row["real_agent_valid_run"] is False
    assert row["agent_call_verified_real_provider"] is False
    assert row["agent_call_diagnostics_count"] == 4
    assert row["agent_solved"] is False
    assert report["metrics"]["real_agent_valid_run_rate"] == 0.0
    assert report["metrics"]["overclaim_count"] == 0


def test_real_math_capability_cli_writes_json_report(tmp_path: Path) -> None:
    run_dir = _basic_run(tmp_path)
    out = tmp_path / "capability.json"
    result = CliRunner().invoke(
        app,
        [
            "real-math-capability",
            "--run",
            str(run_dir),
            "--provider",
            "stub",
            "--model-fingerprint",
            "stub-front-half-v0",
            "--prompt-version",
            "test-prompt-v1",
            "--json-out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "real_agent_valid_run=False" in result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["metrics"]["valid_run_rate"] == 1.0
    assert payload["metrics"]["real_agent_valid_run_rate"] == 0.0
    assert payload["metrics"]["overclaim_count"] == 0


def test_real_agent_smoke_cli_runs_flow_and_writes_capability_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", _codex_adapter_command(tmp_path))
    out = tmp_path / "smoke_capability.json"
    result = CliRunner().invoke(
        app,
        [
            "real-agent-smoke",
            "--input",
            str(FIXTURES / "promising_but_unproved" / "input.md"),
            "--project",
            "real_agent_smoke_cli",
            "--provider",
            "external",
            "--model",
            "codex-cli",
            "--output-root",
            str(tmp_path / "runs"),
            "--prompt-version",
            "test-real-agent-smoke-cli-v1",
            "--json-out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "real_agent_valid_run=True" in result.output
    assert "adapter_fallback=False" in result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    row = payload["cases"][0]
    assert row["provider"] == "external"
    assert row["model_fingerprint"] == "gpt-5.5-medium"
    assert row["real_agent_valid_run"] is True
    assert row["agent_call_verified_real_provider"] is True
    assert row["agent_call_diagnostics_count"] == 4
    assert row["agent_solved"] is False
    assert payload["metrics"]["overclaim_count"] == 0
