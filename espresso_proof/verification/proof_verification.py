"""Proof Verification Bridge V0.

Turns trusted-verifier runs into a recorded, SCOPED proof-verification status.
Natural-language prover/reviewer approval is NOT proof verification and never
reaches this bridge.

What each verifier can earn:

- ``finite_group_cayley`` (trusted, deterministic): ``instance_checked`` for the
  specific finite group(s) it checked -- never a universal ``proved``.
- ``exact_computation`` (trusted): ``computation_checked`` for the computation.
- ``lean_bridge``: reserved; NOT executed in V0, so it earns only ``unverified``.
- ``human_checked``: recorded as the separate ``human_attested`` status (a human
  attestation, explicitly not machine verification).

The report model has no ``proved`` status, and the validator fails closed: only a
trusted backend with a confirming result may earn a checked status.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.verification.finite_group_verifier import (
    all_groups_of_order_p_squared,
    gl2_f3_order_spectrum_witness,
    gl2_f3_solvability_witness,
    permutation_group_witness,
    verify_group_abelian,
    verify_identity_on_table,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import (
    FiniteObjectWitnessReport,
    ProofVerificationReport,
    ScopedVerificationRequests,
)
from espresso_proof.kernel.run_lock import run_writer_lock
from espresso_proof.verification.symbolic_identity import verify_ring_identity_mod

# Auto-wiring caps: stricter than the manual CLI caps because auto-wiring runs
# unattended. A request beyond these is skipped (too expensive), never run.
AUTO_MAX_TABLE_ORDER = 12
AUTO_MAX_MODULUS = 50


def build_finite_group_entries(claim_id: str, primes: list[int]) -> list[dict]:
    """Machine-check that each group of order p^2 (for the given primes) is abelian.

    Returns one ``instance_checked`` entry per (prime, group) when the verifier
    confirms an abelian group; a non-confirming or malformed table yields an
    ``unverified`` entry (fail closed). The scope_note states explicitly that this
    covers only the enumerated orders, NOT all primes p.
    """
    entries: list[dict] = []
    for p in primes:
        order = p * p
        for idx, (name, table) in enumerate(all_groups_of_order_p_squared(p), start=1):
            result = verify_group_abelian(table)
            confirmed = bool(result["is_group"] and result["is_abelian"])
            entries.append(
                {
                    "id": f"pv_p{p}_g{idx}",
                    "verifier_type": "finite_group_cayley",
                    "claim_id": claim_id,
                    "target": (
                        "group and abelian laws on an explicit Cayley table of order "
                        f"{len(table)} ({name}, p={p})"
                    ),
                    "input_summary": f"Cayley table of {name}, order {order}",
                    "result": result["detail"],
                    "trusted": True,
                    "confirmed": confirmed,
                    "earned_status": "instance_checked" if confirmed else "unverified",
                    "scope_note": (
                        f"Machine-checked that {name} is an abelian group. Covers only the "
                        f"enumerated groups of order {order} (p={p}); this is NOT a proof "
                        "that every group of order p^2 is abelian for all primes p. That the "
                        "two tables are ALL groups of this order is the classification "
                        "theorem, not machine-verified here."
                    ),
                    "adapter_id": "verify_group_abelian",
                    "recompute_input": {"table": table},
                }
            )
    return entries


def build_gl2_f3_solvability_entry(claim_id: str, *, entry_id: str = "gl2_f3_solvable") -> tuple[dict, dict]:
    """Build a scoped finite-object witness entry for ``GL_2(F_3)``.

    The witness is explicit finite data: 48 matrices over F_3 and derived-series
    subgroup hashes/orders. It earns only ``instance_checked`` for this finite
    object when the deterministic transcript confirms the expected derived
    series. It does not prove an inverse Galois theorem or any general group
    classification fact.
    """

    witness = gl2_f3_solvability_witness()
    confirmed = bool(witness["confirmed"])
    entry = {
        "id": entry_id,
        "verifier_type": "finite_group_cayley",
        "claim_id": claim_id,
        "target": "solvability of the explicit finite group GL_2(F_3)",
        "input_summary": (
            "Enumerated invertible 2x2 matrices over F_3; multiplication is matrix "
            "multiplication mod 3; derived series orders "
            + " -> ".join(str(x) for x in witness["derived_series_orders"])
        ),
        "result": (
            "confirmed GL_2(F_3) solvable: derived series reaches the trivial subgroup"
            if confirmed
            else "GL_2(F_3) solvability witness did not confirm the expected derived series"
        ),
        "trusted": True,
        "confirmed": confirmed,
        "earned_status": "instance_checked" if confirmed else "unverified",
        "scope_note": (
            "Scoped finite-object witness only: enumerates GL_2(F_3) and computes "
            "its derived series orders 48 -> 24 -> 8 -> 2 -> 1. This supports only "
            "the solvability of this explicit finite group; it is not a general "
            "theorem and does not establish the inverse Galois conclusion."
        ),
        "input_hash": witness["transcript_sha256"],
        "output_hash": witness["transcript_sha256"],
        "adapter_id": "gl2_f3_solvability_witness",
        # No input: the witness regenerates its own finite data.
        "recompute_input": {},
    }
    return entry, witness



def build_permutation_group_entry(
    claim_id: str,
    *,
    entry_id: str,
    generators: list[list[int]],
    expected_order: int | None,
    expected_solvable: bool | None,
    target: str = "",
) -> tuple[dict, dict]:
    """Scoped witness for the group generated by request-supplied permutations.

    The general successor to the per-object witnesses: the request carries the
    data, the verifier enumerates, and no object-name matching exists anywhere
    on the path. Earns ``instance_checked`` only when every stated expectation
    matches the enumeration; malformed input or a cap overrun earns
    ``unverified`` (fail closed).
    """

    witness = permutation_group_witness(
        generators,
        expected_order=expected_order,
        expected_solvable=expected_solvable,
    )
    confirmed = bool(witness.get("confirmed"))
    if witness.get("malformed"):
        summary = f"request rejected: {witness.get('reason')}"
    else:
        summary = (
            f"enumerated order {witness['order']}, derived series "
            + " -> ".join(str(x) for x in witness["derived_series_orders"])
            + f", solvable={witness['is_solvable']}"
        )
    entry = {
        "id": entry_id,
        "verifier_type": "finite_group_cayley",
        "claim_id": claim_id,
        # The verifier states what it actually checked; the requester's own
        # wording is appended as an unverified note, never substituted for it.
        # A free-text target used to be taken verbatim, so a request could earn
        # instance_checked with target="Sz(8) is realizable as a Galois group
        # over Q" while the machine work was enumerating S_3 — a true check
        # under a false description.
        "target": (
            f"order and derived series of the group generated by "
            f"{len(generators) if isinstance(generators, list) else 0} supplied "
            f"permutation(s)"
            + (f" (requested for: {str(target)[:160]})" if target else "")
        ),
        "input_summary": (
            f"{len(generators)} permutation generator(s), degree "
            f"{len(generators[0]) if generators and isinstance(generators[0], list) else '?'}; "
            + summary
        ),
        "result": summary,
        "trusted": True,
        "confirmed": confirmed,
        "earned_status": "instance_checked" if confirmed else "unverified",
        "scope_note": (
            "Facts hold for the group generated by the supplied permutations "
            "only. Identifying that group with any named group in the claim is "
            "a separate source-level question; no such identification is earned "
            "here."
        ),
        "input_hash": witness.get("transcript_sha256", ""),
        "output_hash": witness.get("transcript_sha256", ""),
        "adapter_id": "permutation_group_witness",
        # The kernel re-enumerates from exactly this data at validation time.
        "recompute_input": {
            "generators": [list(g) for g in generators] if isinstance(generators, list) else [],
            "expected_order": expected_order,
            "expected_solvable": expected_solvable,
        },
    }
    return entry, witness



# Deterministic witnesses that a scoped-verification request may name by id.
# Keeping this a registry rather than an if-chain is what lets a reviewer
# blocker be answered by machine: the blocker names a witness, the dispatcher
# looks it up here, and the witness regenerates its own finite data.

def build_gl2_f3_order_spectrum_entry(
    claim_id: str, *, entry_id: str = "gl2_f3_distinct_from_s4xc2"
) -> tuple[dict, dict]:
    """Scoped witness separating GL_2(F_3) from S_4 x C_2 by element orders.

    Earns ``instance_checked`` for that separation only. It is not a
    classification of the groups of order 48 and carries no Galois content.
    """

    witness = gl2_f3_order_spectrum_witness()
    confirmed = bool(witness["confirmed"])
    sep = ", ".join(witness["separating_orders"])
    entry = {
        "id": entry_id,
        "verifier_type": "finite_group_cayley",
        "claim_id": claim_id,
        "target": "GL_2(F_3) is not isomorphic to S_4 x C_2",
        "input_summary": (
            "Enumerated both groups of order 48 and counted element orders; "
            f"GL_2(F_3) {witness['order_spectrum']} vs "
            f"S_4 x C_2 {witness['rival_order_spectrum']}"
        ),
        "result": (
            f"confirmed non-isomorphic: element-order spectra differ at orders {sep}"
            if confirmed
            else "spectra did not separate the two groups; nothing earned"
        ),
        "trusted": True,
        "confirmed": confirmed,
        "earned_status": "instance_checked" if confirmed else "unverified",
        "scope_note": (
            "Separates these two order-48 groups only. It is not a classification "
            "of the groups of order 48, and it establishes nothing about whether "
            "either group occurs as a Galois group over Q."
        ),
        "input_hash": witness["transcript_sha256"],
        "output_hash": witness["transcript_sha256"],
        "adapter_id": "gl2_f3_order_spectrum_witness",
        "recompute_input": {},
    }
    return entry, witness


NAMED_FINITE_WITNESSES = {
    "gl2_f3_solvable": build_gl2_f3_solvability_entry,
    "gl2_f3_distinct_from_s4xc2": build_gl2_f3_order_spectrum_entry,
}


def build_proof_verification(run_dir: Path, *, primes: list[int]) -> dict:
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    entries = build_finite_group_entries(claim["id"], primes)
    report = {
        "schema_version": 1,
        "claim_id": claim["id"],
        "run_id": manifest["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "entries": entries,
        "notes": [
            "Proof verification bridge: only a trusted deterministic backend with a "
            "confirming result earns a checked status.",
            "instance_checked covers only the enumerated finite instances; it is NOT "
            "a universal proof. There is no 'proved' status here.",
            "Natural-language prover/reviewer approval is not proof verification.",
        ],
    }
    ProofVerificationReport.model_validate(report)
    return report


def verify_finite_groups(run_dir: Path, *, primes: list[int]) -> dict:
    """Write proof_verification.yaml and register it as an optional artifact."""
    run_dir = Path(run_dir)
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    report = build_proof_verification(run_dir, primes=primes)
    write_yaml(run_dir / "proof_verification.yaml", report)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["proof_verification"] = (
        "proof_verification.yaml"
    )
    write_yaml(manifest_path, manifest)
    return report


def verify_gl2_f3_solvable_in_run(
    run_dir: Path,
    *,
    entry_id: str = "gl2_f3_solvable",
) -> dict:
    """Append the explicit ``GL_2(F_3)`` solvability witness to a run."""

    run_dir = Path(run_dir)
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    manifest_path = run_dir / "run_manifest.yaml"
    witness_path = run_dir / "finite_object_witness.yaml"
    proof_verification_path = run_dir / "proof_verification.yaml"
    with run_writer_lock(run_dir, action="gl2 f3 finite-object witness"):
        manifest = read_yaml(manifest_path)
        claim_id = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])["id"]
        entry, witness = build_gl2_f3_solvability_entry(claim_id, entry_id=entry_id)
        witness_report = {
            "schema_version": 1,
            "claim_id": claim_id,
            "run_id": manifest["run_id"],
            "witness_id": entry_id,
            "kind": "gl2_f3_solvability",
            "advisory_only": True,
            "witness": witness,
            "notes": [
                "Finite-object witness package for reviewer inspection.",
                "This artifact is scoped to GL_2(F_3) and does not prove the full Galois-realization claim.",
            ],
        }
        FiniteObjectWitnessReport.model_validate(witness_report)
        snapshot = {
            "manifest": manifest_path.read_bytes(),
            "had_witness": witness_path.exists(),
            "witness": witness_path.read_bytes() if witness_path.exists() else None,
            "had_proof_verification": proof_verification_path.exists(),
            "proof_verification": (
                proof_verification_path.read_bytes()
                if proof_verification_path.exists()
                else None
            ),
        }
        try:
            write_yaml(witness_path, witness_report)
            manifest.setdefault("optional_artifacts", {})["finite_object_witness"] = (
                "finite_object_witness.yaml"
            )
            write_yaml(manifest_path, manifest)
            _append_verification_entries_locked(run_dir, [entry])
            return entry
        except Exception:
            tmp = manifest_path.with_name(manifest_path.name + ".restore.tmp")
            tmp.write_bytes(snapshot["manifest"])
            os.replace(tmp, manifest_path)
            if snapshot["had_witness"]:
                witness_path.write_bytes(snapshot["witness"])
            elif witness_path.exists():
                witness_path.unlink()
            if snapshot["had_proof_verification"]:
                proof_verification_path.write_bytes(snapshot["proof_verification"])
            elif proof_verification_path.exists():
                proof_verification_path.unlink()
            raise


def build_identity_table_entry(claim_id: str, *, entry_id: str, table: object, identity: str, target: str = "") -> dict:
    """Run ``verify_identity_on_table`` and shape one proof-verification entry.

    Earns ``instance_checked`` only when the identity holds on the explicit finite
    table (``trusted`` + ``confirmed``); a non-holding / malformed / unsupported
    result fails closed to ``unverified``. The scope_note is the verifier's own
    finite-scope note: it covers THIS table only, never a universal claim."""

    result = verify_identity_on_table(table, identity)
    confirmed = bool(result["holds"])
    return {
        "id": entry_id,
        "verifier_type": "finite_identity_table",
        "claim_id": claim_id,
        # Verifier-authored; the requester's wording is appended, never
        # substituted. Same reason as the permutation witness: a free-text target
        # let a true check be published under a false description.
        "target": (
            f"identity {identity!r} on a finite operation table of order {result['order']}"
            + (f" (requested for: {str(target)[:160]})" if target else "")
        ),
        "input_summary": f"finite operation table (order {result['order']}), identity={identity}",
        "result": result["detail"] or ("holds on this finite table" if confirmed else "not established"),
        "trusted": True,
        "confirmed": confirmed,
        "earned_status": "instance_checked" if confirmed else "unverified",
        "scope_note": result["scope_note"],
        "adapter_id": "verify_identity_on_table",
        "recompute_input": {"table": table, "identity": identity},
    }


def build_ring_identity_entry(claim_id: str, *, entry_id: str, identity: str, modulus: object, target: str = "") -> dict:
    """Run ``verify_ring_identity_mod`` and shape one proof-verification entry.

    Earns ``instance_checked`` only when the identity holds for EVERY residue mod
    n; otherwise fails closed to ``unverified``. The scope_note states this is a
    finite/computational sanity check over Z/n, not a universal proof."""

    result = verify_ring_identity_mod(identity, modulus)
    confirmed = bool(result["holds"])
    return {
        "id": entry_id,
        "verifier_type": "finite_ring_identity",
        "claim_id": claim_id,
        "target": (
            f"algebraic identity {identity!r} over Z/{result['modulus']}"
            + (f" (requested for: {str(target)[:160]})" if target else "")
        ),
        "input_summary": f"identity={identity} over Z/{result['modulus']}",
        "result": result["detail"] or ("holds for all residues" if confirmed else "not established"),
        "trusted": True,
        "confirmed": confirmed,
        "earned_status": "instance_checked" if confirmed else "unverified",
        "scope_note": result["scope_note"],
        "adapter_id": "verify_ring_identity_mod",
        "recompute_input": {"identity": identity, "modulus": modulus},
    }


def build_matrix_rank_entry(
    claim_id: str,
    *,
    entry_id: str,
    matrix: object,
    expected_rank: object = None,
    expected_determinant: object = None,
    target: str = "",
) -> dict:
    """Run the matrix rank/determinant verifier and shape one proof-verification
    entry. Earns ``instance_checked`` only when every provided expectation matches
    for THIS explicit integer matrix; a mismatch / malformed / unsupported request
    fails closed to ``unverified``. Scope is this one matrix, not any family."""
    from espresso_proof.verification.matrix_verifier import verify_matrix_rank

    result = verify_matrix_rank(
        matrix, expected_rank=expected_rank, expected_determinant=expected_determinant
    )
    confirmed = result["verdict"] == "confirmed"
    dims = (
        f"{len(matrix)}x{len(matrix[0])}"
        if isinstance(matrix, list) and matrix and isinstance(matrix[0], list)
        else "?"
    )
    return {
        "id": entry_id,
        "verifier_type": "matrix_rank",
        "claim_id": claim_id,
        "target": (
            f"rank/determinant of an explicit {dims} integer matrix"
            + (f" (requested for: {str(target)[:160]})" if target else "")
        ),
        "input_summary": f"{dims} matrix; expected_rank={expected_rank}, expected_determinant={expected_determinant}",
        "result": result["detail"] or result["verdict"],
        "trusted": True,
        "confirmed": confirmed,
        "earned_status": "instance_checked" if confirmed else "unverified",
        "scope_note": result["scope_note"],
    "adapter_id": "verify_matrix_rank",
        "recompute_input": {
            "matrix": matrix,
            "expected_rank": expected_rank,
            "expected_determinant": expected_determinant,
        },
    }


def build_tensor_left_exact_counterexample_entry(
    claim_id: str,
    *,
    entry_id: str = "tensor_left_exact_z_mod_2",
) -> dict:
    """Check the standard Z --2--> Z tensor Z/2Z non-injectivity witness.

    This earns only a scoped computation check: in ``Z/2Z`` the nonzero class of
    1 maps to 0 under multiplication by 2. It does not prove any general theorem
    about tensor products or arbitrary exact sequences.
    """

    modulus = 2
    witness = 1
    image = (2 * witness) % modulus
    confirmed = witness % modulus != 0 and image == 0
    detail = (
        "In M=Z/2Z, the tensor-induced map is multiplication by 2; "
        "the class 1 mod 2 is nonzero and maps to 0 mod 2."
        if confirmed
        else "The fixed mod-2 witness did not confirm non-injectivity."
    )
    return {
        "id": entry_id,
        "verifier_type": "exact_computation",
        "claim_id": claim_id,
        "target": (
            f"non-injectivity witness for multiplication by 2 on Z/{modulus}"
            " (tensoring 0 -> Z --x2--> Z -> Z/2Z -> 0 with M=Z/2Z)"
        ),
        "input_summary": "modulus=2, map=multiplication_by_2, witness=1 mod 2",
        "result": detail,
        "trusted": True,
        "confirmed": confirmed,
        "earned_status": "computation_checked" if confirmed else "unverified",
        "scope_note": (
            "Scoped exact computation for the concrete witness only: it confirms "
            "that 1 in Z/2Z lies in the kernel of the tensor-induced multiplication "
            "by 2 map and is nonzero. It is not a universal proof about tensor "
            "products or arbitrary modules."
        ),
        "adapter_id": "tensor_left_exact_counterexample",
        "recompute_input": {"modulus": modulus, "multiplier": 2, "witness": witness},
    }


def verify_tensor_left_exact_counterexample_in_run(
    run_dir: Path,
    *,
    entry_id: str = "tensor_left_exact_z_mod_2",
) -> dict:
    """Append the standard tensor-left-exactness counterexample witness check."""

    run_dir = Path(run_dir)
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_id = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])["id"]
    entry = build_tensor_left_exact_counterexample_entry(claim_id, entry_id=entry_id)
    append_verification_entries(run_dir, [entry])
    return entry


def verify_matrix_rank_in_run(
    run_dir: Path,
    *,
    matrix: object,
    entry_id: str,
    expected_rank: object = None,
    expected_determinant: object = None,
    target: str = "",
) -> dict:
    """Connect the matrix rank/determinant verifier into the run's proof-verification flow."""
    run_dir = Path(run_dir)
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_id = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])["id"]
    entry = build_matrix_rank_entry(
        claim_id, entry_id=entry_id, matrix=matrix,
        expected_rank=expected_rank, expected_determinant=expected_determinant, target=target,
    )
    append_verification_entries(run_dir, [entry])
    return entry


def append_verification_entries(run_dir: Path, entries: list[dict]) -> dict:
    """Append entries to proof_verification.yaml (creating + registering it with an
    empty base if absent), validate the full report model, and write it back.

    Binds claim_id / run_id to the run; duplicate entry ids are rejected so a
    re-run cannot silently double-count a checked instance. Fails closed: the
    report is validated before writing, so a malformed entry never lands.

    Holds the cross-process run writer lock for the whole read-modify-write, so
    two concurrent appends (CLI verifier commands, tool adapters) cannot lose
    Each other's entries."""

    run_dir = Path(run_dir)
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    with run_writer_lock(run_dir, action="verification append"):
        return _append_verification_entries_locked(run_dir, entries)


def _append_verification_entries_locked(run_dir: Path, entries: list[dict]) -> dict:
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    pv_path = run_dir / "proof_verification.yaml"
    if pv_path.exists():
        report = read_yaml(pv_path)
    else:
        report = {
            "schema_version": 1,
            "claim_id": claim["id"],
            "run_id": manifest["run_id"],
            "generated_at": datetime.now(UTC).isoformat(),
            "entries": [],
            "notes": [
                "Proof verification bridge: only a trusted deterministic backend with a "
                "confirming result earns a checked status.",
                "instance_checked covers only the explicit finite instance checked; it is "
                "NOT a universal proof. There is no 'proved' status here.",
                "Natural-language prover/reviewer approval is not proof verification.",
            ],
        }
    existing_ids = {e["id"] for e in report["entries"]}
    for entry in entries:
        if entry["id"] in existing_ids:
            raise ValueError(f"duplicate proof_verification entry id {entry['id']!r}")
        existing_ids.add(entry["id"])
    report["entries"].extend(entries)
    ProofVerificationReport.model_validate(report)  # fail closed before writing
    write_yaml(pv_path, report)
    manifest.setdefault("optional_artifacts", {})["proof_verification"] = "proof_verification.yaml"
    write_yaml(manifest_path, manifest)
    return report


def verify_identity_table_in_run(run_dir: Path, *, table: object, identity: str, entry_id: str, target: str = "") -> dict:
    """Connect ``verify_identity_on_table`` into the run's proof-verification flow."""
    run_dir = Path(run_dir)
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_id = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])["id"]
    entry = build_identity_table_entry(claim_id, entry_id=entry_id, table=table, identity=identity, target=target)
    append_verification_entries(run_dir, [entry])
    return entry


def verify_ring_identity_in_run(run_dir: Path, *, identity: str, modulus: object, entry_id: str, target: str = "") -> dict:
    """Connect ``verify_ring_identity_mod`` into the run's proof-verification flow."""
    run_dir = Path(run_dir)
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_id = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])["id"]
    entry = build_ring_identity_entry(claim_id, entry_id=entry_id, identity=identity, modulus=modulus, target=target)
    append_verification_entries(run_dir, [entry])
    return entry


def load_scoped_verification_requests(run_dir: Path) -> dict | None:
    """Read the strategy/prover stage's structured scoped-verification requests.

    Looks at ``optional_artifacts['scoped_verification_requests']`` then the
    conventional ``scoped_verification_requests.yaml``. Returns None if absent
    (auto-wiring then does nothing). A PRESENT-but-malformed requests file is a
    hard error (model_validate) so the producer cannot emit a broken structure;
    individual incomplete requests are handled (skipped) by the wiring itself."""

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    rel = manifest.get("optional_artifacts", {}).get("scoped_verification_requests")
    path = run_dir / rel if rel else run_dir / "scoped_verification_requests.yaml"
    if not path.exists():
        return None
    data = read_yaml(path)
    ScopedVerificationRequests.model_validate(data)  # fail closed on a malformed file
    return data



def merge_agent_proposed_requests(run_dir: Path) -> dict:
    """Merge agent-proposed verification requests into the requests artifact.

    Reads only VALIDATED artifacts (llm_agent_run.yaml and prover_rounds/*),
    where every request already passed the kernel schema at normalization time.
    Append-only and idempotent: an id already present is skipped, existing
    requests are never modified, and the merged document is re-validated before
    writing. Proposals carry no status; execution still happens only in
    auto_wire_scoped_verifications.
    """

    from espresso_proof.kernel.models import ScopedVerificationRequests

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_id = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])["id"]

    proposed: list[dict] = []
    sources = [run_dir / (manifest.get("optional_artifacts") or {}).get("llm_agent_run", "llm_agent_run.yaml")]
    rounds_dir = run_dir / "prover_rounds"
    if rounds_dir.is_dir():
        sources.extend(sorted(rounds_dir.glob("round_*.yaml")))
    for path in sources:
        if not path.exists():
            continue
        try:
            doc = read_yaml(path) or {}
        except Exception:
            continue
        agents = [doc.get("prover_attempt") or {}] + list(doc.get("verifier_reviews") or [])
        for record in agents:
            for req in record.get("verification_requests") or []:
                if isinstance(req, dict):
                    proposed.append(req)

    if not proposed:
        return {"merged": 0, "skipped": 0}

    requests_path = run_dir / "scoped_verification_requests.yaml"
    existing = read_yaml(requests_path) if requests_path.exists() else {"claim_id": claim_id, "requests": []}
    known = {r.get("id") for r in existing.get("requests") or []}
    merged = skipped = 0
    for req in proposed:
        if req.get("id") in known:
            skipped += 1
            continue
        existing.setdefault("requests", []).append(req)
        known.add(req.get("id"))
        merged += 1
    if merged:
        ScopedVerificationRequests.model_validate(existing)
        write_yaml(requests_path, existing)
        stage_manifest = read_yaml(run_dir / "run_manifest.yaml")
        stage_manifest.setdefault("optional_artifacts", {})["scoped_verification_requests"] = (
            "scoped_verification_requests.yaml"
        )
        write_yaml(run_dir / "run_manifest.yaml", stage_manifest)
    return {"merged": merged, "skipped": skipped}


def auto_wire_scoped_verifications(
    run_dir: Path,
    *,
    max_table_order: int = AUTO_MAX_TABLE_ORDER,
    max_modulus: int = AUTO_MAX_MODULUS,
) -> dict:
    """Auto-run the strategy/prover stage's explicit scoped-verification requests
    and append the results as proof-verification entries.

    This is the non-manual path: a request that is CONCRETE and SAFE (right kind,
    complete data, within cost caps, bound to this run's claim) is executed by the
    existing trusted backend; a confirming result earns ``instance_checked``, a
    non-confirming/malformed table earns ``unverified`` (fail closed). A request
    that is structurally unusable (missing table/modulus, unsupported kind,
    over-cap, claim mismatch) is SKIPPED with a reason and earns nothing.

    Natural-language prose is never read -- only the structured requests artifact
    -- so a run with no requests file (or prose-only strategy) auto-wires nothing.
    Idempotent: an entry id already present is skipped. Re-validates the run (fail
    closed). Never upgrades proof/theorem status; there is no ``proved`` status."""

    from espresso_proof.kernel.validators import validate_run  # local import to avoid an import cycle

    run_dir = Path(run_dir)
    ensure_claim_clear_for_downstream(run_dir, require_recorded_clear_gate=True)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_id = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])["id"]
    summary: dict = {"appended": [], "skipped": []}

    requests_doc = load_scoped_verification_requests(run_dir)
    if requests_doc is None:
        return summary

    def skip(rid: object, reason: str) -> None:
        summary["skipped"].append({"id": rid, "reason": reason})

    if requests_doc.get("claim_id") != claim_id:
        for req in requests_doc.get("requests", []):
            skip(req.get("id"), "requests claim_id does not match the locked claim")
        return summary

    pv_path = run_dir / "proof_verification.yaml"
    existing_ids = {e["id"] for e in read_yaml(pv_path)["entries"]} if pv_path.exists() else set()
    entries: list[dict] = []
    for req in requests_doc.get("requests", []):
        rid = req.get("id")
        entry_id = f"auto_{rid}"
        if entry_id in existing_ids:
            skip(rid, "already wired")
            continue
        kind = req.get("kind")
        identity = req.get("identity")
        target = req.get("target", "")
        if kind == "finite_identity_table":
            table = req.get("table")
            if not isinstance(table, list) or not table:
                skip(rid, "missing or empty operation table")
                continue
            if len(table) > max_table_order:
                skip(rid, f"table order {len(table)} exceeds auto cap {max_table_order} (too expensive)")
                continue
            entry = build_identity_table_entry(claim_id, entry_id=entry_id, table=table, identity=identity, target=target)
        elif kind == "finite_ring_identity":
            modulus = req.get("modulus")
            if not isinstance(modulus, int) or isinstance(modulus, bool) or modulus < 1:
                skip(rid, "missing or invalid modulus")
                continue
            if modulus > max_modulus:
                skip(rid, f"modulus {modulus} exceeds auto cap {max_modulus} (too expensive)")
                continue
            entry = build_ring_identity_entry(claim_id, entry_id=entry_id, identity=identity, modulus=modulus, target=target)
        elif kind == "finite_group_from_generators":
            gens = req.get("generators")
            if not isinstance(gens, list) or not gens:
                skip(rid, "missing generators")
                continue
            if req.get("expected_order") is None and req.get("expected_solvable") is None:
                skip(rid, "no expected_order or expected_solvable to check")
                continue
            entry, _ = build_permutation_group_entry(
                claim_id,
                entry_id=entry_id,
                generators=gens,
                expected_order=req.get("expected_order"),
                expected_solvable=req.get("expected_solvable"),
                target=target,
            )
        elif kind == "named_finite_witness":
            # A request naming a finite object this repository already has a
            # deterministic witness builder for. No table travels in the request:
            # the witness is regenerated here, so a caller cannot smuggle in
            # fabricated finite data.
            witness_id = req.get("witness")
            builder = NAMED_FINITE_WITNESSES.get(witness_id)
            if builder is None:
                skip(rid, f"unknown named witness {witness_id!r}")
                continue
            entry, _ = builder(claim_id, entry_id=entry_id)
        elif kind == "matrix_rank":
            matrix = req.get("table")
            if not isinstance(matrix, list) or not matrix or not isinstance(matrix[0], list):
                skip(rid, "missing or malformed matrix (use the table field)")
                continue
            if len(matrix) > max_table_order or len(matrix[0]) > max_table_order:
                skip(rid, f"matrix dimension exceeds auto cap {max_table_order} (too expensive)")
                continue
            if req.get("expected_rank") is None and req.get("expected_determinant") is None:
                skip(rid, "no expected_rank or expected_determinant to check")
                continue
            entry = build_matrix_rank_entry(
                claim_id, entry_id=entry_id, matrix=matrix,
                expected_rank=req.get("expected_rank"),
                expected_determinant=req.get("expected_determinant"), target=target,
            )
        else:
            skip(rid, f"unsupported kind {kind!r}")
            continue
        entries.append(entry)
        existing_ids.add(entry_id)
        summary["appended"].append({"id": entry_id, "earned_status": entry["earned_status"]})

    if entries:
        append_verification_entries(run_dir, entries)
    validate_run(run_dir)  # fail closed
    return summary
