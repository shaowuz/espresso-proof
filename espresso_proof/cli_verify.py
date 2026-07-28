"""Machine-check verify-* commands (developer/verification surface).

Registered on the shared Typer ``app`` from ``espresso_proof.cli_app``
(flat command names, identical behavior). Imported for its side effects at
the bottom of cli.py."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from espresso_proof.kernel.io import read_yaml
# NOTE: literature_retrieval.retrieve_and_ground (corpus_dir/query, older
# retrieve-sources --ground path) is a DIFFERENT function from
# Retrieval_grounding.retrieve_and_ground (candidate/local_corpus). Alias
# This one so it does not shadow the import above (which the
# `retrieve-and-ground` command depends on).
from espresso_proof.verification.finite_group_verifier import verify_identity_on_table
from espresso_proof.verification.proof_verification import (
    auto_wire_scoped_verifications,
    verify_tensor_left_exact_counterexample_in_run,
    verify_finite_groups,
    verify_gl2_f3_solvable_in_run,
    verify_identity_table_in_run,
    verify_matrix_rank_in_run,
    verify_ring_identity_in_run,
)
from espresso_proof.verification.symbolic_identity import SUPPORTED_RING_IDENTITIES
from espresso_proof.verification.source_verification import ground_run
from espresso_proof.verification.external_tool_adapters import run_exact_modexp_adapter
from espresso_proof.verification.verifier import verify_computation_checks
from espresso_proof.kernel.validators import validate_run

from espresso_proof.cli_app import app
from espresso_proof.cli_shared import (
    _refresh_derived_views,
    _require_claim_clear_for_cli,
)


@app.command("verify-exact-modexp")
def verify_exact_modexp_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    base: int = typer.Option(..., "--base"),
    exp: int = typer.Option(..., "--exp"),
    mod: int = typer.Option(..., "--mod"),
    expected: int = typer.Option(..., "--expected"),
    entry_id: str = typer.Option(..., "--id", help="Unique id for this tool-run record."),
) -> None:
    """Allowlisted deterministic adapter: run the exact modular-exponentiation tool
    in the sandbox and, IF it confirms base^exp mod m == expected, earn a SCOPED
    computation_checked proof-verification entry THROUGH the kernel bridge (this
    one computation only; never a universal proof). A non-confirming or failed run
    earns nothing.
    """
    _require_claim_clear_for_cli(run)
    try:
        result = run_exact_modexp_adapter(
            run, base=base, exp=exp, mod=mod, expected=expected, entry_id=entry_id
        )
    except ValueError as exc:
        typer.echo(f"Refused: {exc}")
        raise typer.Exit(code=2) from exc
    _refresh_derived_views(run)
    if result["earned"]:
        typer.echo(
            f"Confirmed {base}^{exp} mod {mod} == {expected}: earned a SCOPED "
            "computation_checked entry (this computation only; NOT a universal proof)."
        )
        typer.echo(f"-> {run / 'proof_verification.yaml'}")
    else:
        typer.echo(f"Not confirmed: earned no status (fail-closed). {result['detail']}")
    typer.echo(f"-> {run / 'external_tool_runs.yaml'}")


@app.command("verify-finite-groups")
def verify_finite_groups_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    prime: list[int] | None = typer.Option(None, "--prime", help="Prime p; checks all groups of order p^2. Repeatable."),
) -> None:
    """Machine-verify (deterministically) that every group of order p^2 is abelian,
    for the given small primes -- a SCOPED finite check, not a universal proof.

    Earns instance_checked for the enumerated groups only. There is no 'proved'
    status: checking p=2,3 does not prove the claim for all primes p. Natural-
    language prover/reviewer approval is not proof verification.
    """
    _require_claim_clear_for_cli(run)
    primes = prime or [2, 3]
    report = verify_finite_groups(run, primes=primes)
    _refresh_derived_views(run)
    validate_run(run)
    checked = sum(1 for e in report["entries"] if e["earned_status"] == "instance_checked")
    typer.echo(
        f"Verified {checked}/{len(report['entries'])} finite group(s) abelian "
        f"for primes {primes} (checked in this finite case only; NOT a universal proof)."
    )
    typer.echo(f"-> {run / 'proof_verification.yaml'}")


@app.command("verify-gl2-f3-solvable-in-run")
def verify_gl2_f3_solvable_in_run_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    entry_id: str = typer.Option(
        "gl2_f3_solvable",
        "--id",
        help="Unique id for this verification entry.",
    ),
) -> None:
    """Build the explicit GL_2(F_3) finite-object witness and record it.

    Earns instance_checked only for the scoped statement that this explicitly
    enumerated finite group is solvable. It does not prove the inverse Galois
    conclusion and does not act as a general group-theory verifier.
    """
    _require_claim_clear_for_cli(run)
    entry = verify_gl2_f3_solvable_in_run(run, entry_id=entry_id)
    validate_run(run)
    _refresh_derived_views(run)
    typer.echo(
        f"Entry {entry['id']}: {entry['earned_status']} -- {entry['result']} "
        "(finite-object witness only; NOT a proof of the full claim)."
    )
    typer.echo(f"Scope: {entry['scope_note']}")
    typer.echo(f"-> {run / 'finite_object_witness.yaml'}")
    typer.echo(f"-> {run / 'proof_verification.yaml'}")


@app.command("verify-identity")
def verify_identity_command(
    table: Path = typer.Option(..., "--table", exists=True, dir_okay=False, help="YAML file: a finite operation table (list of lists of ints)."),
    identity: str = typer.Option(..., "--identity", help="commutative | associative | idempotent | involutive"),
) -> None:
    """Exhaustively check an algebraic identity on an EXPLICITLY-GIVEN finite
    operation table -- a SCOPED finite/computational check, never a universal
    proof. Read-only; fails closed on a malformed table or unsupported identity.
    """
    data = read_yaml(table)
    result = verify_identity_on_table(data, identity)
    verdict = (
        f"holds on this finite table (order {result['order']}, {result['checked']} cases checked)"
        if result["holds"]
        else f"NOT established: {result['detail']}"
    )
    typer.echo(f"Identity '{identity}': {verdict}")
    typer.echo(f"Scope: {result['scope_note']}")


@app.command("verify-identity-in-run")
def verify_identity_in_run_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    table: Path = typer.Option(..., "--table", exists=True, dir_okay=False, help="YAML file: a finite operation table (list of lists of ints)."),
    identity: str = typer.Option(..., "--identity", help="commutative | associative | idempotent | involutive"),
    entry_id: str = typer.Option(..., "--id", help="Unique id for this verification entry."),
    target: str = typer.Option("", "--target", help="Human description of what was checked."),
) -> None:
    """Connect an explicit finite-table identity check into the run's proof-
    verification artifact. Earns instance_checked ONLY if the identity holds on
    this finite table; otherwise fails closed to unverified. Re-validates the run.
    There is no 'proved' status: this is a finite/computational check, not a
    universal proof.
    """
    _require_claim_clear_for_cli(run)
    data = read_yaml(table)
    entry = verify_identity_table_in_run(run, table=data, identity=identity, entry_id=entry_id, target=target)
    validate_run(run)
    _refresh_derived_views(run)
    typer.echo(
        f"Entry {entry['id']}: {entry['earned_status']} -- {entry['result']} "
        f"(checked in this finite case only; NOT a universal proof)."
    )
    typer.echo(f"Scope: {entry['scope_note']}")


@app.command("verify-ring-identity-in-run")
def verify_ring_identity_in_run_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    identity: str = typer.Option(..., "--identity", help=f"One of: {', '.join(SUPPORTED_RING_IDENTITIES)}"),
    modulus: int = typer.Option(..., "--modulus", help="The modulus n; the identity is checked over all residues of Z/n."),
    entry_id: str = typer.Option(..., "--id", help="Unique id for this verification entry."),
    target: str = typer.Option("", "--target", help="Human description of what was checked."),
) -> None:
    """Connect an exhaustive Z/n algebraic-identity sanity check into the run's
    proof-verification artifact. Earns instance_checked ONLY if the identity holds
    for every residue mod n; otherwise fails closed to unverified. Re-validates the
    run. This is a finite/computational sanity check over Z/n, never a universal
    proof over a general ring or for all moduli.
    """
    _require_claim_clear_for_cli(run)
    entry = verify_ring_identity_in_run(run, identity=identity, modulus=modulus, entry_id=entry_id, target=target)
    validate_run(run)
    _refresh_derived_views(run)
    typer.echo(
        f"Entry {entry['id']}: {entry['earned_status']} -- {entry['result']} "
        f"(checked over Z/{modulus} only; NOT a universal proof)."
    )
    typer.echo(f"Scope: {entry['scope_note']}")


@app.command("verify-matrix-rank-in-run")
def verify_matrix_rank_in_run_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    matrix: str = typer.Option(..., "--matrix", help='Integer matrix as JSON, e.g. "[[1,2],[2,4]]".'),
    entry_id: str = typer.Option(..., "--id", help="Unique id for this verification entry."),
    expected_rank: int | None = typer.Option(None, "--expected-rank", help="Expected rank of the matrix."),
    expected_determinant: int | None = typer.Option(None, "--expected-determinant", help="Expected determinant (square matrix only)."),
    target: str = typer.Option("", "--target", help="Human description of what was checked."),
) -> None:
    """Machine-check the rank and/or determinant of ONE explicit integer matrix and
    record it in the run's proof-verification artifact. Earns instance_checked ONLY
    when every provided expectation matches; otherwise fails closed to unverified.
    This is a finite/computational check of this specific matrix (e.g. witnessing a
    concrete rank/dimension jump), NEVER a universal proof about any family.
    """
    _require_claim_clear_for_cli(run)
    try:
        parsed = json.loads(matrix)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--matrix must be valid JSON: {exc}")
    if expected_rank is None and expected_determinant is None:
        raise typer.BadParameter("provide --expected-rank and/or --expected-determinant")
    entry = verify_matrix_rank_in_run(
        run, matrix=parsed, entry_id=entry_id,
        expected_rank=expected_rank, expected_determinant=expected_determinant, target=target,
    )
    validate_run(run)
    _refresh_derived_views(run)
    typer.echo(
        f"Entry {entry['id']}: {entry['earned_status']} -- {entry['result']} "
        "(checked for this specific matrix only; NOT a universal proof)."
    )
    typer.echo(f"Scope: {entry['scope_note']}")


@app.command("verify-tensor-left-exact-counterexample-in-run")
def verify_tensor_left_exact_counterexample_in_run_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    entry_id: str = typer.Option(
        "tensor_left_exact_z_mod_2",
        "--id",
        help="Unique id for this verification entry.",
    ),
) -> None:
    """Machine-check the standard Z/2Z witness that tensoring need not preserve
    left exactness. Earns only a scoped computation_checked entry for this
    concrete witness; never proves a universal theorem.
    """

    _require_claim_clear_for_cli(run)
    entry = verify_tensor_left_exact_counterexample_in_run(run, entry_id=entry_id)
    validate_run(run)
    _refresh_derived_views(run)
    typer.echo(
        f"Entry {entry['id']}: {entry['earned_status']} -- {entry['result']} "
        "(checked for this concrete tensor witness only; NOT a universal proof)."
    )
    typer.echo(f"Scope: {entry['scope_note']}")


@app.command("auto-wire-verifications")
def auto_wire_verifications_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Run the strategy/prover stage's EXPLICIT scoped-verification requests
    (from scoped_verification_requests.yaml) through the trusted backend and append
    the results to proof_verification.yaml. A confirming result earns
    instance_checked; a non-confirming/malformed one fails closed to unverified; a
    structurally unusable / too-expensive / claim-mismatched request is skipped.

    Natural-language prose is never read -- only explicit structured requests -- so
    this cannot manufacture a checked status from text. There is no 'proved'
    status: every result is a finite/computational check, not a universal proof.
    """
    _require_claim_clear_for_cli(run)
    _earned_phrase = {
        "instance_checked": "checked in this finite/computational scope only",
        "unverified": "not established (no checked status)",
    }
    summary = auto_wire_scoped_verifications(run)
    _refresh_derived_views(run)
    for item in summary["appended"]:
        phrase = _earned_phrase.get(item["earned_status"], item["earned_status"])
        typer.echo(f"[run] {item['id']}: {phrase} [{item['earned_status']}]")
    for item in summary["skipped"]:
        typer.echo(f"[skip] {item['id']}: {item['reason']}")
    typer.echo(
        f"Auto-wired {len(summary['appended'])} scoped check(s), skipped "
        f"{len(summary['skipped'])} (checked in this finite/computational scope only; "
        "NOT a universal proof)."
    )


@app.command("verify-sources")
def verify_sources(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
    theorem_extraction: Path | None = typer.Option(None, "--theorem-extraction", exists=True, dir_okay=False),
) -> None:
    """Run conservative Referencing verification from extracted theorem statements."""
    validate_run(run)
    _require_claim_clear_for_cli(run)
    manifest = read_yaml(run / "run_manifest.yaml")
    extraction_path = theorem_extraction
    if extraction_path is None:
        extraction_name = manifest.get("optional_artifacts", {}).get("theorem_extraction")
        if not extraction_name:
            raise typer.BadParameter("run has no theorem_extraction optional artifact")
        extraction_path = run / extraction_name
    ground_run(run_dir=run, theorem_extraction_path=extraction_path)
    typer.echo(f"Wrote source verification to {run / 'source_grounding.yaml'}")
    typer.echo(f"Wrote theorem applicability audit to {run / 'theorem_applicability.yaml'}")


@app.command("verify-computations")
def verify_computations_command(
    run: Path = typer.Option(..., "--run", exists=True, file_okay=False),
) -> None:
    """Execute machine_check specs with a real backend and write computation_verification.yaml.

    Does not upgrade status. It records confirmed/refuted/error/unsupported per
    check; validation then forbids a refuted check from claiming checked.
    """
    _require_claim_clear_for_cli(run)
    report = verify_computation_checks(run)
    validate_run(run)
    confirmed = sum(1 for r in report["results"] if r["verdict"] == "confirmed")
    refuted = sum(1 for r in report["results"] if r["verdict"] == "refuted")
    typer.echo(
        f"Verified {len(report['results'])} machine check(s): "
        f"{confirmed} confirmed, {refuted} refuted "
        f"-> {run / 'computation_verification.yaml'}"
    )
