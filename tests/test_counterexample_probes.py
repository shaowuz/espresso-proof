"""Goal 42E cycle 1: deterministic finite-group counterexample probes."""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.verification.counterexample_probes import (
    _dihedral_cayley,
    propose_deterministic_counterexamples,
)
from espresso_proof.verification.finite_group_verifier import verify_group_abelian
from espresso_proof.kernel.io import read_yaml
from espresso_proof.kernel.models import CounterexampleTest

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


def test_semidirect_tables_are_nonabelian_groups() -> None:
    # Goal 42E cycle 10: odd orders q*p with q | p-1 — the smallest is
    # 21 = Z_7 x| Z_3 (r=2: 2^3 = 8 = 1 mod 7); 55 = Z_11 x| Z_5 and
    # 57 = Z_19 x| Z_3 exercise other (p, q, r) shapes; 183 = Z_61 x| Z_3
    # pins the largest in-cap odd construction.
    from espresso_proof.verification.counterexample_probes import (
        _odd_pq_with_nonabelian_group,
        _semidirect_cayley,
    )

    for order in (21, 55, 57, 183):
        p, q, r = _odd_pq_with_nonabelian_group(order)
        assert p * q == order and pow(r, q, p) == 1
        verdict = verify_group_abelian(_semidirect_cayley(p, q, r))
        assert verdict["is_group"] is True
        assert verdict["is_abelian"] is False
        assert verdict["order"] == order
    # orders whose groups are all abelian/cyclic, and out-of-scope prime
    # powers, construct nothing.
    for order in (15, 33, 35, 45, 27, 9, 25):
        assert _odd_pq_with_nonabelian_group(order) is None, order


def test_order21_claim_yields_admissible_checked_counterexample() -> None:
    claim = {
        "normalized_statement": "Every group of order 21 is abelian.",
        "conclusion": "G is abelian.",
        "hypotheses": [{"name": "G is a group of order 21", "status": "explicit", "source": "user"}],
    }
    tests = propose_deterministic_counterexamples(claim)
    assert len(tests) == 1
    record = CounterexampleTest.model_validate(tests[0])
    assert record.result.value == "failed"
    assert record.refutes_claim is True
    assert record.satisfies_all_claim_hypotheses is True
    assert "machine check" in (record.support or "")
    assert "semidirect" in record.example_id


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
        # odd orders where the claim is TRUE fail closed: 15 = 3*5 and
        # 35 = 5*7 (q does not divide p-1: every group cyclic), 45 = 3^2*5
        # (every group abelian) — no fabricated pressure against true claims.
        "Every group of order 15 is abelian.",
        "Every group of order 35 is abelian.",
        "Every group of order 45 is abelian.",
        # odd prime powers (27 = 3^3 has the non-abelian Heisenberg group)
        # stay out of the semidirect probe's scope: fail closed, not wrong.
        "Every group of order 27 is abelian.",
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
    # the finite-group scaffold locks the hypotheses precisely enough for the
    # admissible table witness to refute this exact universal claim.
    from espresso_proof.kernel.io import write_text
    from espresso_proof.orchestration.orchestrator import orchestrate_run
    from espresso_proof.kernel.validators import validate_run

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
        t.get("refutes_claim") is True
        and t.get("satisfies_all_claim_hypotheses") is True
        for t in checked
    ), "the locked order-6 scaffold should let the checked witness refute the exact claim"


def test_true_p2_run_unaffected(tmp_path: Path) -> None:
    from espresso_proof.kernel.io import write_text
    from espresso_proof.orchestration.orchestrator import orchestrate_run
    from espresso_proof.kernel.validators import validate_run

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
