"""Kernel-side recomputation of checked verifier results.

Why this module exists. `_validate_proof_verification` used to grant
``instance_checked`` / ``computation_checked`` by reading the ``trusted`` and
``confirmed`` booleans out of the artifact. Those booleans are written by
whoever wrote the file, so a hand-authored or agent-authored
``proof_verification.yaml`` earned a checked status with no machine work at all
— reproduced end to end during a hostile pre-publication review, including a
forged entry whose target was "a group that was never built". That contradicted
the file's own docstring and the project's central claim.

The rule now: an entry claiming a checked status must name an ``adapter_id``
this registry knows, and the kernel re-runs that adapter over the entry's
recorded ``recompute_input`` and compares its own transcript hash against the
recorded ``output_hash``. A forged entry cannot survive, because no adapter
reproduces a hash it invented.

Registering an adapter is the general extension point: a new deterministic
verifier ships a recompute function here, and its builder records the input it
needs. An adapter that is absent from this registry cannot earn a checked
status — fail closed, no exceptions carved out for specific problems.
"""

from __future__ import annotations

from typing import Any, Callable

# adapter_id -> callable(recompute_input) -> dict carrying at least "confirmed",
# and "transcript_sha256" when that verifier produces one. Callables must be
# deterministic, offline, and side-effect free: this runs inside validate_run,
# on every validation.
_RECOMPUTERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def register_recomputer(
    adapter_id: str, fn: Callable[[dict[str, Any]], dict[str, Any]]
) -> None:
    """Declare that ``adapter_id`` can be re-derived by the kernel."""

    _RECOMPUTERS[adapter_id] = fn


_INSTALLED = False


def _ensure_installed() -> None:
    """Install the shipped adapters on first use, not at import.

    Some verifier modules import the kernel validators, so wiring them at
    import time makes espresso_proof.kernel.validators circular. Deferring to
    first use keeps the kernel importable on its own.
    """

    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    _install_default_recomputers()


def is_recomputable(adapter_id: str) -> bool:
    _ensure_installed()
    return adapter_id in _RECOMPUTERS


def recompute_result(
    adapter_id: str, recompute_input: dict[str, Any] | None
) -> dict[str, Any] | None:
    """What the kernel derives itself, or None if it cannot derive anything.

    None means "cannot verify" and callers must treat it as failure — never as
    permission. An exception inside an adapter is also None: a verifier that
    crashes has not confirmed anything.

    Returns {"confirmed": bool, "transcript_sha256": str | None}. Not every
    shipped verifier produces a transcript hash, so the hash is compared only
    when both sides have one; the confirming boolean is always re-derived, which
    is the part a forged entry cannot fake.
    """

    _ensure_installed()
    fn = _RECOMPUTERS.get(adapter_id)
    if fn is None:
        return None
    try:
        result = fn(dict(recompute_input or {}))
    except Exception:
        return None
    if not isinstance(result, dict):
        return None
    digest = result.get("transcript_sha256")
    return {
        "confirmed": bool(result.get("confirmed")),
        "transcript_sha256": digest if isinstance(digest, str) and digest else None,
    }


def _install_default_recomputers() -> None:
    """Wire the shipped deterministic verifiers.

    Imported lazily inside the function so importing the kernel does not pull
    in the verification package at module-import time.
    """

    from espresso_proof.verification.finite_group_verifier import (
        gl2_f3_order_spectrum_witness,
        gl2_f3_solvability_witness,
        permutation_group_witness,
    )

    # Witnesses that regenerate their own finite data take no input.
    register_recomputer("gl2_f3_solvability_witness", lambda _: gl2_f3_solvability_witness())
    register_recomputer("gl2_f3_order_spectrum_witness", lambda _: gl2_f3_order_spectrum_witness())

    def _permutation_group(spec: dict[str, Any]) -> dict[str, Any]:
        return permutation_group_witness(
            spec.get("generators") or [],
            expected_order=spec.get("expected_order"),
            expected_solvable=spec.get("expected_solvable"),
        )

    register_recomputer("permutation_group_witness", _permutation_group)

    from espresso_proof.verification.finite_group_verifier import verify_identity_on_table

    def _identity_table(spec: dict[str, Any]) -> dict[str, Any]:
        out = verify_identity_on_table(spec.get("table"), str(spec.get("identity", "")))
        return {"confirmed": bool(out.get("holds"))}

    register_recomputer("verify_identity_on_table", _identity_table)

    from espresso_proof.verification.symbolic_identity import verify_ring_identity_mod

    def _ring_identity(spec: dict[str, Any]) -> dict[str, Any]:
        out = verify_ring_identity_mod(
            str(spec.get("identity", "")), spec.get("modulus")
        )
        return {"confirmed": bool(out.get("holds"))}

    register_recomputer("verify_ring_identity_mod", _ring_identity)

    from espresso_proof.verification.matrix_verifier import verify_matrix_rank

    def _matrix_rank(spec: dict[str, Any]) -> dict[str, Any]:
        out = verify_matrix_rank(
            spec.get("matrix"),
            expected_rank=spec.get("expected_rank"),
            expected_determinant=spec.get("expected_determinant"),
        )
        return {"confirmed": out.get("verdict") == "confirmed"}

    register_recomputer("verify_matrix_rank", _matrix_rank)

    from espresso_proof.verification.finite_group_verifier import verify_group_abelian

    def _group_abelian(spec: dict[str, Any]) -> dict[str, Any]:
        out = verify_group_abelian(spec.get("table") or [])
        return {"confirmed": bool(out.get("is_group") and out.get("is_abelian"))}

    register_recomputer("verify_group_abelian", _group_abelian)

    def _exact_modexp(spec: dict[str, Any]) -> dict[str, Any]:
        # Recomputed with pow() directly rather than through
        # run_exact_modexp_adapter: that adapter takes a run_dir and WRITES
        # artifacts, and validation must stay read-only and side-effect free.
        # The arithmetic is the whole check, so re-deriving it here is exactly
        # as strong and cannot mutate the run being validated.
        base, exp = spec.get("base"), spec.get("exp")
        mod, expected = spec.get("mod"), spec.get("expected")
        for value in (base, exp, mod, expected):
            if not isinstance(value, int) or isinstance(value, bool):
                return {"confirmed": False}
        if mod <= 0 or exp < 0:
            return {"confirmed": False}
        return {"confirmed": pow(base, exp, mod) == expected}

    register_recomputer("exact_modexp", _exact_modexp)

    def _tensor_left_exact(spec: dict[str, Any]) -> dict[str, Any]:
        # Z/m --x n--> Z/m tensored with Z/k: the witness survives iff the
        # multiplication map kills it, i.e. n*w = 0 mod m with w != 0. Pure
        # arithmetic, re-derived here rather than trusted from the artifact.
        m = int(spec.get("modulus", 0))
        n = int(spec.get("multiplier", 0))
        w = int(spec.get("witness", 0))
        if m <= 1:
            return {"confirmed": False}
        return {"confirmed": w % m != 0 and (n * w) % m == 0}

    register_recomputer("tensor_left_exact_counterexample", _tensor_left_exact)



__all__ = [
    "is_recomputable",
    "recompute_result",
    "register_recomputer",
]
