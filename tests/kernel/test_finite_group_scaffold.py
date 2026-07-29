from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.kernel.io import read_yaml, write_text
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.orchestration.mock_pipeline import _detect_scenario
from espresso_proof.kernel.validators import validate_run


@pytest.mark.parametrize(
    ("case_id", "claim", "expected_route", "expected_status"),
    [
        (
            "p2_groups_true",
            "Every group of order p^2, where p is prime, is abelian.",
            "strategy_center_quotient_p2",
            "promising",
        ),
        (
            "order6_false",
            "Every group of order 6 is abelian.",
            "strategy_admit_checked_s3_counterexample",
            "likely_false",
        ),
        (
            "p3_groups_false",
            "Every group of order p^3, where p is prime, is abelian.",
            "strategy_test_nonabelian_order_8",
            "likely_false",
        ),
        (
            "center_pgroup_true",
            "Every nontrivial finite p-group has nontrivial center.",
            "strategy_class_equation_center",
            "promising",
        ),
        (
            "cyclic_quotient_center_step",
            "If G/Z(G) is cyclic, then G is abelian.",
            "strategy_center_quotient_cosets",
            "promising",
        ),
    ],
)
def test_goal56a_finite_group_claims_get_domain_specific_scaffolds(
    tmp_path: Path,
    case_id: str,
    claim: str,
    expected_route: str,
    expected_status: str,
) -> None:
    input_path = tmp_path / f"{case_id}.md"
    write_text(input_path, claim + "\n")

    run_dir = run_mock_critique(case_id, input_path, tmp_path)
    validate_run(run_dir)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    locked = read_yaml(run_dir / artifacts["locked_claim"])
    ledger = read_yaml(run_dir / artifacts["hypothesis_ledger"])
    grade = read_yaml(run_dir / artifacts["idea_grade"])
    brief = read_yaml(run_dir / artifacts["strategy_brief"])

    assert locked["objects"] != ["not yet determined"]
    assert locked["hypotheses"]
    assert ledger["missing_or_unclear"] == []
    assert grade["status"] == expected_status
    assert brief["top_strategies"][0]["id"] == expected_route

    route_text = " ".join(
        str(value)
        for route in brief["top_strategies"]
        for value in route.values()
    ).lower()
    assert "eigenvalue" not in route_text
    assert "spectral" not in route_text
    assert "strategy_lock_claim" not in {r["id"] for r in brief["top_strategies"]}


def test_order6_checked_counterexample_becomes_admissible_after_precise_lock(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "order6.md"
    write_text(input_path, "Every group of order 6 is abelian.\n")

    run_dir = run_mock_critique("order6_false", input_path, tmp_path)
    validate_run(run_dir)

    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    counterexamples = read_yaml(run_dir / artifacts["counterexamples"])
    tests = counterexamples["tests"]

    assert tests
    probe = tests[0]
    assert probe["operator_id"] == "deterministic_finite_group_probe"
    assert probe["satisfies_all_claim_hypotheses"] is True
    assert probe["result"] == "failed"
    assert probe["refutes_claim"] is True


@pytest.mark.parametrize(
    "variant",
    [
        "Every cyclic group of order 6 is abelian.",
        "Every group of order p^3, where p is prime, is cyclic.",
        "Every finite group has nontrivial center.",
        "If G/H is cyclic, then G is abelian.",
    ],
)
def test_finite_group_scaffold_does_not_catch_modified_claims(
    tmp_path: Path, variant: str
) -> None:
    path = tmp_path / "input.md"
    assert _detect_scenario(path, variant) == "vague_claim_unclear"
