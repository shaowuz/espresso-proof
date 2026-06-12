"""Goal 42E cycle 2: deterministic scoped-verification request probes."""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.io import read_yaml
from espresso_proof.models import ScopedVerificationRequests
from espresso_proof.verification_request_probes import (
    propose_scoped_verification_requests,
)


def test_p_squared_claim_yields_schema_valid_commutativity_requests() -> None:
    claim = {
        "normalized_statement": "Every group of order p^2, where p is prime, is abelian.",
        "conclusion": "G is abelian.",
    }
    requests = propose_scoped_verification_requests(claim)
    # two known groups per prime, primes 2 and 3.
    assert len(requests) == 4
    doc = ScopedVerificationRequests.model_validate(
        {"claim_id": "claim_001", "requests": requests}
    )
    assert all(r.kind == "finite_identity_table" for r in doc.requests)
    assert all(r.identity == "commutative" for r in doc.requests)
    assert {len(r.table) for r in doc.requests} == {4, 9}
    # honesty: every target names the classification-theorem caveat.
    assert all("classification theorem" in r.target for r in doc.requests)


@pytest.mark.parametrize(
    "statement",
    [
        # concrete orders belong to the cycle-1 counterexample probe, not here.
        "Every group of order 4 is abelian.",
        "Every group of order 6 is abelian.",
        # modified/unrelated claims never match the anchored form.
        "Every cyclic group of order p^2, where p is prime, is abelian.",
        "It is not true that every group of order p^2, where p is prime, is abelian.",
        "Every group of order p^2, where p is prime, is non-abelian.",
        "Every group of order p^3, where p is prime, is abelian.",
        "Every group of order p^2, where p is prime, has an abelian subgroup.",
        "Every continuous function attains a maximum.",
    ],
)
def test_other_claims_get_no_requests(statement: str) -> None:
    assert propose_scoped_verification_requests(
        {"normalized_statement": statement}
    ) == []


def test_requests_auto_wire_to_instance_checked_in_full_run(tmp_path: Path) -> None:
    # End-to-end through the real runtime + the dogfood harness path: the
    # symbolic p^2 dogfood case earns trusted confirmed instance_checked
    # entries, the run stays kernel-valid, and nothing claims a universal
    # proof.
    from espresso_proof.io import write_text
    from espresso_proof.orchestrator import orchestrate_run, resume_run
    from espresso_proof.proof_verification import auto_wire_scoped_verifications
    from espresso_proof.validators import validate_run

    input_path = tmp_path / "input.md"
    write_text(input_path, "Every group of order p^2, where p is prime, is abelian.\n")
    result = orchestrate_run(
        project="probe_pv",
        input_path=input_path,
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(run_dir / manifest["artifacts"]["strategy_brief"])
    selected = brief["top_strategies"][0]["id"] if brief.get("top_strategies") else "none"
    resume_run(
        run_dir=run_dir,
        selected_strategy=selected,
        effort="normal_pass",
        prover_provider="stub",
    )
    summary = auto_wire_scoped_verifications(run_dir)
    assert len(summary["appended"]) == 4
    validate_run(run_dir)
    report = read_yaml(run_dir / "proof_verification.yaml")
    earned = [
        e for e in report["entries"]
        if e["trusted"] and e["confirmed"] and e["earned_status"] == "instance_checked"
    ]
    assert len(earned) == 4
    assert all("only" in e["scope_note"] for e in earned)
    # second wiring is a no-op (idempotent).
    again = auto_wire_scoped_verifications(run_dir)
    assert not again["appended"]


def test_unrelated_run_wires_nothing(tmp_path: Path) -> None:
    from espresso_proof.io import write_text
    from espresso_proof.orchestrator import orchestrate_run
    from espresso_proof.proof_verification import auto_wire_scoped_verifications

    input_path = tmp_path / "input.md"
    write_text(input_path, "Every group of order 6 is abelian.\n")
    result = orchestrate_run(
        project="probe_none",
        input_path=input_path,
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    summary = auto_wire_scoped_verifications(run_dir)
    assert summary == {"appended": [], "skipped": []}
    assert not (run_dir / "proof_verification.yaml").exists()
