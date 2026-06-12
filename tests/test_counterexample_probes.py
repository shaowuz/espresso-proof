"""Goal 42E cycle 1: deterministic finite-group counterexample probes."""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.counterexample_probes import (
    _dihedral_cayley,
    propose_deterministic_counterexamples,
)
from espresso_proof.finite_group_verifier import verify_group_abelian
from espresso_proof.io import read_yaml
from espresso_proof.models import CounterexampleTest

TRIVIAL_ORDER6_HYPOTHESIS = {
    "name": "G is a group of order 6",
    "status": "explicit",
    "source": "user",
}


def test_dihedral_tables_are_nonabelian_groups() -> None:
    for n in (3, 4, 5, 6):
        verdict = verify_group_abelian(_dihedral_cayley(n))
        assert verdict["is_group"] is True
        assert verdict["is_abelian"] is False
        assert verdict["order"] == 2 * n


def test_trivial_hypotheses_yield_admissible_checked_counterexample() -> None:
    claim = {
        "normalized_statement": "Every group of order 6 is abelian.",
        "conclusion": "G is abelian.",
        "hypotheses": [TRIVIAL_ORDER6_HYPOTHESIS],
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


def test_undetermined_hypotheses_downgrade_to_unclear_no_refutation() -> None:
    # An empty hypothesis list (claim parser did not determine hypotheses)
    # still records the machine fact, but NEVER asserts admissibility or
    # refutation.
    claim = {"normalized_statement": "Every group of order 6 is abelian."}
    tests = propose_deterministic_counterexamples(claim)
    assert len(tests) == 1
    record = CounterexampleTest.model_validate(tests[0])
    assert record.result.value == "failed"
    assert record.satisfies_all_claim_hypotheses == "unclear"
    assert record.refutes_claim is False
    assert "not yet determined" in (record.explanation or "")


def test_nontrivial_hypothesis_fails_closed() -> None:
    # "Every cyclic group of order 6 is abelian" normalized with hypothesis
    # "G is cyclic" is TRUE — any non-trivial hypothesis silences the probe.
    claim = {
        "normalized_statement": "Every group of order 6 is abelian.",
        "hypotheses": [
            TRIVIAL_ORDER6_HYPOTHESIS,
            {"name": "G is cyclic", "status": "explicit", "source": "agent"},
        ],
    }
    assert propose_deterministic_counterexamples(claim) == []


@pytest.mark.parametrize(
    "statement",
    [
        # symbolic order p^2 (the TRUE theorem) must never fire the probe.
        "Every group of order p^2, where p is prime, is abelian.",
        # order 4: all groups abelian — no construction, no fabricated pressure.
        "Every group of order 4 is abelian.",
        # odd orders are out of probe scope (dihedral-only construction).
        "Every group of order 15 is abelian.",
        "Every group of order 21 is abelian.",
        # unrelated claims never match.
        "Every continuous function attains a maximum.",
        # ---- adversarial TRUE/modified claims (Kernel Safety + Math Workflow
        # review): the anchored fullmatch must reject every one of these. ----
        "It is not true that every group of order 6 is abelian.",
        "It is false that every group of order 6 is abelian.",
        "Every group of order 6 has an abelian subgroup.",
        "Every group of order 6 contains an abelian subgroup.",
        "Every group of order 6 is abelian or dihedral.",
        "Every nilpotent group of order 6 is abelian.",
        "Every cyclic group of order 6 is abelian.",
        "Every group of order 6 is non-abelian.",
        "Every quotient of a group of order 8 by a subgroup of order 4 is abelian.",
        "Every subgroup of a group of order 6 is abelian.",
    ],
)
def test_modified_or_true_claims_get_no_probe(statement: str) -> None:
    assert propose_deterministic_counterexamples({"normalized_statement": statement}) == []


def test_match_never_spans_statement_and_conclusion() -> None:
    # Only the normalized statement is matched; text split across fields
    # (statement without "abelian", conclusion supplying it) must not fire.
    claim = {
        "normalized_statement": "Every group of order 6 is",
        "conclusion": "abelian.",
    }
    assert propose_deterministic_counterexamples(claim) == []


def test_probe_wired_into_full_run_and_kernel_valid(tmp_path: Path) -> None:
    # End-to-end: the false-theorem dogfood case now produces a machine-checked
    # counterexample through the real runtime, the run stays kernel-valid, and
    # — because the mock vague-claim path leaves hypotheses undetermined — the
    # record is honestly downgraded (no refutation without admissibility).
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
        and t.get("operator_id") == "deterministic_finite_group_probe"
    ]
    assert checked, "the order-6 run must carry a machine-checked counterexample"
    assert all(
        t.get("refutes_claim") is False
        and t.get("satisfies_all_claim_hypotheses") == "unclear"
        for t in checked
    ), "undetermined mock hypotheses must never earn a refutation"


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
