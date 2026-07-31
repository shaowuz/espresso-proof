"""The amber seal's precondition must be enforced by the kernel, not by its writer.

Found by a hostile pre-publication review. The rule that makes
`agent_consensus_mock_checked` mean anything — no proof line may still carry an
unresolved dependency — lived only in `orchestration/agent_consensus_seal.py`.
The sanctioned path checked it, so a normal seal was safe; but
`_validate_agent_consensus_review` describes itself as "the authoritative
BACKSTOP that blocks a hand-forged consensus bypassing the seal helper" and only
checked that a proof artifact existed. The review wrote
`agent_consensus_review.yaml` by hand, citing real line ids with the five
`bound_*` hashes recomputed from the public unkeyed digest, and sealed a proof
whose lines still carried `gaps`.

The general defect: a rule that decides a status was enforced in the component
that writes the artifact, so anything bypassing that component bypassed the
rule. The rule now lives in `kernel/proof_dependencies.py` and both the seal
helper and the validator call that one implementation.
"""

from __future__ import annotations

from espresso_proof.kernel.proof_dependencies import (
    is_resolved_dependency,
    unresolved_proof_dependency_lines,
)


def _line(line_id: str, **over) -> dict:
    line = {"line_id": line_id, "statement": "s", "justification": "j"}
    line.update(over)
    return line


def test_a_line_with_gaps_is_unresolved() -> None:
    proof = {"lines": [_line("L1", gaps=["external theorem locator missing"])]}
    assert unresolved_proof_dependency_lines(proof) == ["L1"]


def test_a_clean_line_is_resolved() -> None:
    assert unresolved_proof_dependency_lines({"lines": [_line("L1")]}) == []


def _theorem_dep(**over) -> dict:
    """A theorem dependency in the shape the kernel actually requires."""

    dep = {
        "type": "theorem_ref",
        "ref": "Some Theorem 3.2",
        "source_grounded": True,
        "authority": "source_grounded_theorem_ref",
        "machine_verified": False,
        "source_locator": "Author, Book, Theorem 3.2, p. 41",
    }
    dep.update(over)
    return dep


def test_an_unchecked_gap_dependency_is_unresolved() -> None:
    proof = {"lines": [_line("L1", dependencies=[{"type": "unchecked_gap"}])]}
    assert unresolved_proof_dependency_lines(proof) == ["L1"]


def test_a_theorem_ref_not_in_the_trusted_set_is_unresolved() -> None:
    # Naming a theorem is not grounding it: with no trusted refs supplied the
    # dependency cannot be treated as met, however well-formed the entry looks.
    proof = {"lines": [_line("L1", dependencies=[_theorem_dep()])]}
    assert unresolved_proof_dependency_lines(proof) == ["L1"]


def test_a_source_grounded_theorem_dependency_is_resolved() -> None:
    proof = {"lines": [_line("L1", dependencies=[_theorem_dep()])]}
    assert (
        unresolved_proof_dependency_lines(
            proof, trusted_source_grounded_theorem_refs={"Some Theorem 3.2"}
        )
        == []
    )


def test_a_trusted_ref_still_needs_every_provenance_field() -> None:
    # Being in the trusted set is necessary, not sufficient: dropping any one of
    # the recorded provenance fields must fail closed.
    for missing in (
        {"source_grounded": False},
        {"authority": "something_else"},
        {"machine_verified": True},
        {"source_locator": "   "},
    ):
        proof = {"lines": [_line("L1", dependencies=[_theorem_dep(**missing)])]}
        assert unresolved_proof_dependency_lines(
            proof, trusted_source_grounded_theorem_refs={"Some Theorem 3.2"}
        ) == ["L1"], missing


def test_every_unresolved_line_is_reported_not_just_the_first() -> None:
    proof = {
        "lines": [
            _line("L1"),
            _line("L2", gaps=["gap"]),
            _line("L3"),
            _line("L4", gaps=["gap"]),
        ]
    }
    assert unresolved_proof_dependency_lines(proof) == ["L2", "L4"]


def test_malformed_lines_are_skipped_not_treated_as_resolved() -> None:
    # A non-dict line carries no checkable dependency; it must not silently
    # count as satisfied for some other line.
    proof = {"lines": ["not a line", _line("L2", gaps=["gap"])]}
    assert unresolved_proof_dependency_lines(proof) == ["L2"]


def test_missing_or_empty_lines_yields_nothing_to_report() -> None:
    for proof in ({}, {"lines": []}, {"lines": None}):
        assert unresolved_proof_dependency_lines(proof) == []


def test_a_line_without_line_id_still_gets_reported_by_position() -> None:
    proof = {"lines": [{"statement": "s", "gaps": ["gap"]}]}
    assert unresolved_proof_dependency_lines(proof) == ["line_1"]


def test_dependency_resolution_is_exposed_for_reuse() -> None:
    # Both the seal helper and the validator must be able to ask the same
    # question; a second private copy is how the two drifted apart before.
    assert callable(is_resolved_dependency)


def test_orchestration_uses_the_kernel_implementation() -> None:
    import espresso_proof.kernel.proof_dependencies as kernel_rule
    import espresso_proof.orchestration.agent_consensus_seal as seal

    assert (
        seal._unresolved_proof_dependency_lines
        is kernel_rule.unresolved_proof_dependency_lines
    )


def test_validator_enforces_the_rule_itself() -> None:
    # The regression that mattered: the check must be reachable from the
    # validator module, not only from orchestration.
    import inspect

    from espresso_proof.kernel.validators import agent_artifact_gates

    source = inspect.getsource(agent_artifact_gates)
    assert "unresolved_proof_dependency_lines" in source
    assert "cannot seal a line-numbered proof with" in source
