"""Goal 42E cycle 1: deterministic finite-group counterexample probes."""

from __future__ import annotations

from pathlib import Path

from espresso_proof.counterexample_probes import (
    _dihedral_cayley,
    propose_deterministic_counterexamples,
)
from espresso_proof.finite_group_verifier import verify_group_abelian
from espresso_proof.io import read_yaml
from espresso_proof.models import CounterexampleTest

FIXTURES = Path("tests/fixtures")


def test_dihedral_tables_are_nonabelian_groups() -> None:
    for n in (3, 4, 5, 6):
        verdict = verify_group_abelian(_dihedral_cayley(n))
        assert verdict["is_group"] is True
        assert verdict["is_abelian"] is False
        assert verdict["order"] == 2 * n


def test_order6_claim_yields_admissible_checked_counterexample() -> None:
    claim = {
        "normalized_statement": "Every group of order 6 is abelian.",
        "conclusion": "G is abelian.",
    }
    tests = propose_deterministic_counterexamples(claim)
    assert len(tests) == 1
    record = CounterexampleTest.model_validate(tests[0])  # kernel schema + gates
    assert record.result.value == "failed"
    assert record.refutes_claim is True
    assert record.satisfies_all_claim_hypotheses is True
    assert record.refutes_step_only is False
    assert "machine check" in (record.support or "")
    assert "dihedral" in record.example_id


def test_symbolic_and_true_claims_get_no_probe() -> None:
    # symbolic order p^2 (the TRUE theorem) must never fire the probe.
    assert propose_deterministic_counterexamples(
        {"normalized_statement": "Every group of order p^2, where p is prime, is abelian."}
    ) == []
    # order 4: all groups abelian — no construction, no fabricated pressure.
    assert propose_deterministic_counterexamples(
        {"normalized_statement": "Every group of order 4 is abelian."}
    ) == []
    # odd orders are out of probe scope (dihedral-only construction).
    assert propose_deterministic_counterexamples(
        {"normalized_statement": "Every group of order 15 is abelian."}
    ) == []
    # unrelated claims never match.
    assert propose_deterministic_counterexamples(
        {"normalized_statement": "Every continuous function attains a maximum."}
    ) == []


def test_probe_wired_into_full_run_and_kernel_valid(tmp_path: Path) -> None:
    # End-to-end: the false-theorem dogfood case now produces a CHECKED
    # counterexample through the real runtime, and the run stays kernel-valid.
    from espresso_proof.io import write_text
    from espresso_proof.orchestrator import orchestrate_run
    from espresso_proof.validators import validate_run

    input_path = tmp_path / "input.md"
    write_text(input_path, "Every group of order 6 is abelian.\n")
    result = orchestrate_run(
        project="probe_e2e",
        input_path=input_path,
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    validate_run(run_dir)
    counterexamples = read_yaml(run_dir / "counterexamples.yaml")
    checked = [
        t for t in counterexamples.get("tests", [])
        if t.get("result") == "failed"
        and t.get("refutes_claim")
        and t.get("satisfies_all_claim_hypotheses") is True
    ]
    assert checked, "the order-6 run must carry an admissible checked counterexample"


def test_true_p2_run_unaffected(tmp_path: Path) -> None:
    from espresso_proof.io import write_text
    from espresso_proof.orchestrator import orchestrate_run
    from espresso_proof.validators import validate_run

    input_path = tmp_path / "input.md"
    write_text(input_path, "Every group of order p^2, where p is prime, is abelian.\n")
    result = orchestrate_run(
        project="probe_p2",
        input_path=input_path,
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    validate_run(run_dir)
    counterexamples = read_yaml(run_dir / "counterexamples.yaml")
    assert not any(
        t.get("refutes_claim") for t in counterexamples.get("tests", [])
    ), "a true symbolic claim must never receive a fabricated refutation"
