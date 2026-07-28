"""Proof Verification Bridge V1.

Scoped machine checks without Lean: a finite-structure check (existing) plus an
exhaustive algebraic-identity check over an EXPLICITLY-GIVEN finite operation
table. Every result is finite/computational scope only; no `proved` status; fails
closed on malformed input.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.verification.finite_group_verifier import (
    cyclic_group_table,
    elementary_abelian_table,
    verify_group_abelian,
    verify_identity_on_table,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.genesis import run_mock_critique

FIXTURES = Path("tests/fixtures")
_FORBIDDEN = ("proved", "proof-close", "proof complete", "we proved")


# ---------------------------------------------------------------------------
# 1) Finite structure check passes for a known valid example.
# ---------------------------------------------------------------------------


def test_finite_structure_check_passes_for_valid_group() -> None:
    res = verify_group_abelian(cyclic_group_table(4))
    assert res["is_group"] is True and res["is_abelian"] is True
    # the algebraic-identity check agrees: C_4 is commutative.
    comm = verify_identity_on_table(cyclic_group_table(4), "commutative")
    assert comm["holds"] is True
    assert comm["checked"] == 16  # exhaustive over the 4x4 table
    assert "not a universal proof" in comm["scope_note"]


def test_associativity_and_involution_on_klein_four() -> None:
    klein = elementary_abelian_table(2, 2)  # C_2 x C_2: every element is its own inverse
    assert verify_identity_on_table(klein, "associative")["holds"] is True
    assert verify_identity_on_table(klein, "involutive")["holds"] is True


# ---------------------------------------------------------------------------
# 2) Finite structure check rejects malformed operation tables (fail closed).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        [[0, 1], [0]],  # non-square
        [[0, 5], [1, 0]],  # entry out of range
        [],  # empty
    ],
)
def test_rejects_malformed_tables(bad: list[list[int]]) -> None:
    assert verify_group_abelian(bad)["is_group"] is False
    res = verify_identity_on_table(bad, "commutative")
    assert res["holds"] is False
    assert "fail closed" in res["detail"] or res["order"] == 0


@pytest.mark.parametrize("junk", [None, 5, "abc", [1, 2, 3], [[0, 1], "x"], [{"a": 1}]])
def test_non_list_of_lists_input_fails_closed_without_raising(junk: object) -> None:
    # arbitrary YAML (null / scalar / flat list / ragged) must fail closed, never raise.
    res = verify_identity_on_table(junk, "commutative")  # type: ignore[arg-type]
    assert res["holds"] is False
    assert "fail closed" in res["detail"]


def test_cli_verify_identity_non_list_yaml_fails_closed(tmp_path: Path) -> None:
    table_path = tmp_path / "junk.yaml"
    write_yaml(table_path, [1, 2, 3])  # flat list, not a table
    result = CliRunner().invoke(
        app, ["verify-identity", "--table", str(table_path), "--identity", "commutative"]
    )
    assert result.exit_code == 0, result.output  # reports, does not crash with a traceback
    assert "not established" in result.output.lower()


def test_unsupported_identity_fails_closed() -> None:
    res = verify_identity_on_table(cyclic_group_table(3), "distributive")
    assert res["holds"] is False and res["supported"] is False
    assert "unsupported" in res["detail"]


def test_involution_without_identity_element_fails_closed() -> None:
    # A commutative non-group table (no identity element) -> involution undefined.
    no_identity = [[1, 1], [1, 1]]
    res = verify_identity_on_table(no_identity, "involutive")
    assert res["holds"] is False
    assert "no identity element" in res["detail"]


# ---------------------------------------------------------------------------
# 3) Algebraic identity sanity check reports scoped result only (no `proved`).
# ---------------------------------------------------------------------------


def test_identity_result_is_scoped_and_has_no_proof_complete_language() -> None:
    res = verify_identity_on_table(cyclic_group_table(5), "commutative")
    assert res["holds"] is True
    blob = (res["scope_note"] + " " + res["detail"]).lower()
    assert "finite" in blob
    for phrase in _FORBIDDEN:
        assert phrase not in blob
    # a true non-commutative table (S_3) reports holds=False, still scoped.
    # S_3 as a 6-element table: use a known non-abelian group table.
    s3 = _s3_table()
    nc = verify_identity_on_table(s3, "commutative")
    assert nc["holds"] is False
    assert verify_group_abelian(s3)["is_group"] is True and verify_group_abelian(s3)["is_abelian"] is False


def _s3_table() -> list[list[int]]:
    # S_3 via permutations of {0,1,2}; element i is a permutation, product = compose.
    import itertools

    perms = list(itertools.permutations(range(3)))
    index = {p: i for i, p in enumerate(perms)}

    def compose(p: tuple[int, ...], q: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(p[q[k]] for k in range(3))

    return [[index[compose(perms[a], perms[b])] for b in range(6)] for a in range(6)]


# ---------------------------------------------------------------------------
# 4) Exhaustive concrete check does not upgrade theorem status.
# ---------------------------------------------------------------------------


def test_identity_check_does_not_upgrade_run_status(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "linear_algebra", FIXTURES / "all_matrices_diagonalizable" / "input.md", tmp_path
    )
    grade_before = read_yaml(run_dir / "idea_grade.yaml")
    # running the scoped check writes nothing to the run and earns no proof status.
    res = verify_identity_on_table(cyclic_group_table(4), "commutative")
    assert res["holds"] is True
    assert "earned_status" not in res and "proof_status" not in res
    assert read_yaml(run_dir / "idea_grade.yaml") == grade_before


# ---------------------------------------------------------------------------
# 5) CLI wording preserves finite/computational scope.
# ---------------------------------------------------------------------------


def test_cli_verify_identity_preserves_scope_wording(tmp_path: Path) -> None:
    table_path = tmp_path / "table.yaml"
    write_yaml(table_path, cyclic_group_table(4))
    result = CliRunner().invoke(
        app, ["verify-identity", "--table", str(table_path), "--identity", "commutative"]
    )
    assert result.exit_code == 0, result.output
    lower = result.output.lower()
    assert "finite" in lower and "not a universal proof" in lower
    for phrase in _FORBIDDEN:
        assert phrase not in lower


def test_cli_verify_identity_malformed_table_fails_closed(tmp_path: Path) -> None:
    table_path = tmp_path / "bad.yaml"
    write_yaml(table_path, [[0, 1], [0]])  # non-square
    result = CliRunner().invoke(
        app, ["verify-identity", "--table", str(table_path), "--identity", "commutative"]
    )
    assert result.exit_code == 0, result.output  # reports, does not crash
    assert "not established" in result.output.lower()
