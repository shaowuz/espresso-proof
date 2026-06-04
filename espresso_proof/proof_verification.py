"""Proof Verification Bridge V0 (Six-Hour goal, Phase 4).

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

from datetime import UTC, datetime
from pathlib import Path

from .finite_group_verifier import all_groups_of_order_p_squared, verify_group_abelian
from .io import read_yaml, write_yaml
from .models import ProofVerificationReport


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
                    "target": f"group {name} of order {order} (p={p})",
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
                }
            )
    return entries


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
    report = build_proof_verification(run_dir, primes=primes)
    write_yaml(run_dir / "proof_verification.yaml", report)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["proof_verification"] = (
        "proof_verification.yaml"
    )
    write_yaml(manifest_path, manifest)
    return report
