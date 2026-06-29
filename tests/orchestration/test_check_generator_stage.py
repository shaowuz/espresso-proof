"""LLM machine-check generator stage guardrails."""

from __future__ import annotations

import hashlib
from pathlib import Path

from espresso_proof.agents.front_half_agents import run_front_half_agent_pass
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures
from espresso_proof.kernel.validators import validate_run


class _FakeGeneratorClient:
    provider = "fake"
    model = "fake-generator"

    def __init__(self, checks: list[dict]) -> None:
        self.checks = checks

    def complete_json(self, *, system: str, prompt: str) -> dict:
        assert "Machine-Check Generator" in system
        assert '"required_json_schema"' in prompt
        return {"checks": list(self.checks)}


def _prepared_run(tmp_path: Path, *, step_count: int = 2) -> tuple[Path, dict, str]:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(
        "For every positive integer n, 1+...+n = n(n+1)/2.",
        encoding="utf-8",
    )
    run_dir = run_mock_critique("check_generator_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    claim_id = read_yaml(run_dir / artifacts["locked_claim"])["id"]
    write_yaml(
        run_dir / artifacts["examples"],
        {
            "claim_id": claim_id,
            "examples": [
                {
                    "id": "ex_sum",
                    "template_id": "agent_example",
                    "name": "small finite sums",
                    "satisfies_all_claim_hypotheses": "unclear",
                    "hypothesis_mismatches": [],
                    "what_it_tests": ["finite sum identity"],
                    "expected_conclusion": "the identity holds",
                    "observed_or_inferred_behavior": "not yet observed",
                    "result_for_current_idea": "not_tested",
                    "source_or_reason": "test scaffold",
                    "nontriviality": "standard",
                }
            ],
        },
    )
    write_yaml(
        run_dir / artifacts["source_grounding"],
        {
            "claim_id": claim_id,
            "source_refs": [],
            "steps": [
                {
                    "proof_step_id": f"step_{index:03d}",
                    "needed_theorem": "finite sum identity",
                    "status": "candidate",
                    "source_refs": [],
                    "hypothesis_match": {"matches": "unclear", "mismatches": []},
                    "note": "candidate step for a finite computation",
                }
                for index in range(1, step_count + 1)
            ],
        },
    )
    write_yaml(run_dir / artifacts["computation_checks"], {"claim_id": claim_id, "checks": []})
    refresh_final_report_disclosures(run_dir)
    validate_run(run_dir)
    return run_dir, artifacts, claim_id


def _finite_spec(target: str, *, hi: int = 10) -> dict:
    return {
        "kind": "finite_enumeration",
        "expression": f"@n[1:{hi}] sum(1,n)",
        "expected": f"@n[1:{hi}] n*(n+1)/2",
        "target_step_id": target,
    }


def test_check_generator_appends_candidate_only_bound_nonvacuous_specs(tmp_path: Path) -> None:
    run_dir, artifacts, _claim_id = _prepared_run(tmp_path)
    client = _FakeGeneratorClient(
        [
            {**_finite_spec("step_001"), "status": "checked"},
            _finite_spec("step_001"),
        ]
    )

    run_front_half_agent_pass(run_dir=run_dir, client=client, stages=["check_generator"])
    validate_run(run_dir)

    checks = read_yaml(run_dir / artifacts["computation_checks"])["checks"]
    assert len(checks) == 1
    check = checks[0]
    assert check["id"] == "comp_gen_001"
    assert check["status"] == "human_needed"
    assert check["method"] == "manual_review_needed"
    assert check["example_id"] == "ex_sum"
    assert check["machine_check"]["target_step_id"] == "step_001"


def test_check_generator_drops_unbound_trivial_unsupported_and_oversized_specs(tmp_path: Path) -> None:
    run_dir, artifacts, _claim_id = _prepared_run(tmp_path)
    client = _FakeGeneratorClient(
        [
            {"kind": "exact_arithmetic", "expression": "0", "expected": "0", "target_step_id": "step_001"},
            {"kind": "finite_enumeration", "expression": "@n[1:5] 0", "expected": "@n[1:5] 0", "target_step_id": "step_001"},
            {"kind": "finite_enumeration", "expression": "@n[1:5] sum(1,n)", "expected": "@n[1:5] n*(n+1)/2", "target_step_id": "missing"},
            {"kind": "scoped_verification_request", "expression": "x", "expected": "x", "target_step_id": "step_001"},
            {"kind": "polynomial_identity", "expression": "x" * 1200, "expected": "x", "target_step_id": "step_001"},
            _finite_spec("step_001"),
        ]
    )

    run_front_half_agent_pass(run_dir=run_dir, client=client, stages=["check_generator"])
    validate_run(run_dir)

    checks = read_yaml(run_dir / artifacts["computation_checks"])["checks"]
    assert [check["machine_check"]["target_step_id"] for check in checks] == ["step_001"]


def test_check_generator_enforces_per_step_and_per_run_caps(tmp_path: Path) -> None:
    run_dir, artifacts, _claim_id = _prepared_run(tmp_path, step_count=10)
    client = _FakeGeneratorClient(
        [_finite_spec("step_001", hi=10), _finite_spec("step_001", hi=11)]
        + [_finite_spec(f"step_{index:03d}", hi=10 + index) for index in range(2, 11)]
    )

    run_front_half_agent_pass(run_dir=run_dir, client=client, stages=["check_generator"])
    validate_run(run_dir)

    checks = read_yaml(run_dir / artifacts["computation_checks"])["checks"]
    assert len(checks) == 8
    assert [check["id"] for check in checks] == [f"comp_gen_{index:03d}" for index in range(1, 9)]
    targets = [check["machine_check"]["target_step_id"] for check in checks]
    assert len(targets) == len(set(targets))
    assert "step_010" not in targets


def test_check_generator_runtime_does_not_mutate_frozen_surfaces(tmp_path: Path) -> None:
    frozen = [
        Path("espresso_proof/kernel/validators.py"),
        Path("espresso_proof/kernel/models.py"),
        Path("espresso_proof/verification/verifier.py"),
        *sorted(Path("espresso_proof/validation").glob("*.py")),
    ]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in frozen}
    run_dir, _artifacts, _claim_id = _prepared_run(tmp_path)

    run_front_half_agent_pass(
        run_dir=run_dir,
        client=_FakeGeneratorClient([_finite_spec("step_001")]),
        stages=["check_generator"],
    )
    validate_run(run_dir)

    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in frozen}
    assert after == before
