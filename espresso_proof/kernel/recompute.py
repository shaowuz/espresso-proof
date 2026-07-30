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

import re
from typing import Any, Callable, Mapping

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

    Returns {"confirmed", "transcript_sha256", "canonical_target"}.

    ``canonical_target`` is what the adapter says it checked, and it is the part
    that binds an earned status to real work. Hash agreement alone is not
    enough: two shipped witnesses take no input and are fully deterministic, so
    anyone can compute their public hash and write an entry carrying that hash
    with a ``target`` of their choosing — a true check under a false
    description. The validator therefore requires the recorded target to match
    what the adapter itself reports.
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
    canonical = result.get("canonical_target")
    return {
        "confirmed": bool(result.get("confirmed")),
        "transcript_sha256": digest if isinstance(digest, str) and digest else None,
        "canonical_target": canonical if isinstance(canonical, str) and canonical else None,
    }


# What a builder is allowed to append after the machine-authored target: the
# requester's own wording, inside one labelled parenthetical, containing no
# closing paren of its own. Prefix matching alone was not enough — it let
# arbitrary false mathematics ride along after a true machine-authored prefix
# ("...table of order 4 — hence every group of order p^2 is abelian").
REQUESTED_FOR_SUFFIX = re.compile(r" \(requested for: [^)]*\)\Z", re.DOTALL)


def checked_entry_rejection_reason(entry: Mapping[str, Any]) -> str | None:
    """Why this proof-verification entry may NOT hold a checked status, or None.

    One implementation, two callers: the validator raises with the reason, and
    every consumer that acts on checked evidence asks the same question. A
    consumer that read ``trusted`` / ``confirmed`` / ``earned_status`` straight
    out of the file was reading a declaration — `_resolve_reviewer_blockers` did
    exactly that, by path, so an UNREGISTERED forged ``proof_verification.yaml``
    silenced reviewer blockers without ever passing the validator.

    Returns a human-readable reason string on rejection. None means the kernel
    re-derived the result itself and the recorded target is what the adapter says
    it checked.
    """

    if not isinstance(entry, Mapping):
        return "entry is not a mapping"
    if not (entry.get("trusted") and entry.get("confirmed")):
        return "it carries no trusted + confirming verifier result"
    adapter_id = str(entry.get("adapter_id") or "")
    if not is_recomputable(adapter_id):
        return (
            f"adapter_id {adapter_id!r} is not a kernel-recomputable deterministic "
            "verifier; a checked status cannot be earned from a result the kernel "
            "cannot re-derive"
        )
    recompute_input = entry.get("recompute_input")
    recomputed = recompute_result(
        adapter_id, recompute_input if isinstance(recompute_input, Mapping) else None
    )
    if recomputed is None:
        return (
            f"the kernel could not re-derive it with {adapter_id!r} (missing or "
            "malformed recompute_input)"
        )
    if not recomputed["confirmed"]:
        return "the kernel's own recomputation does not confirm it"
    canonical = recomputed["canonical_target"]
    if not canonical:
        # Opt-in binding is not binding. An adapter that does not say what it
        # checked cannot have its result attached to any target, so it must not
        # be able to earn a status at all — otherwise registering an adapter
        # without this field silently reopens the borrowed-hash bypass.
        return (
            f"{adapter_id!r} does not report what it checked; an adapter must state "
            "a canonical target to earn a status"
        )
    target = str(entry.get("target") or "")
    if not target.startswith(canonical):
        return (
            f"it names target {target[:60]!r}, but {adapter_id!r} checked "
            f"{canonical[:60]!r}; a checked status cannot be earned for a target the "
            "verifier did not check"
        )
    remainder = target[len(canonical) :]
    if remainder and not REQUESTED_FOR_SUFFIX.fullmatch(remainder):
        return (
            f"it appends {remainder[:60]!r} to the machine-authored target; only a "
            "'(requested for: ...)' note may follow, so unchecked mathematics cannot "
            "ride along behind a checked prefix"
        )
    # Hash comparison only when the adapter produced one: several shipped
    # verifiers emit no transcript hash, and the re-derived confirming boolean
    # above is the part a forged entry cannot fake.
    digest = recomputed["transcript_sha256"]
    recorded = str(entry.get("output_hash") or "")
    if digest and digest != recorded:
        return (
            "the kernel's recomputation does not reproduce the recorded output_hash "
            f"(recorded {recorded[:16]!r}, recomputed {digest[:16]!r})"
        )
    return None


# A machine check is re-derived from its own recorded (kind, expression,
# expected) — all three are already in the artifact — so the cache key is the
# whole input and caching is sound: the backend is pure and deterministic.
# Without it, validate_run (called after every stage) would re-run the same
# capped-but-real computation dozens of times per run.
_COMPUTATION_CACHE: dict[tuple[str, str, str], dict[str, str] | None] = {}


def recompute_computation_check(
    kind: object, expression: object, expected: object
) -> dict[str, str] | None:
    """What the kernel derives for one machine check, or None if it cannot.

    ``computation_verification.yaml`` was the second checked-status path, and the
    recompute gate did not cover it: ``_validate_proof_verification`` re-derived
    its entries, but a ``ComputationVerificationResult`` carrying
    ``executed: true, verdict: confirmed`` was believed as written. A hostile
    review reproduced it end to end — a hand-written result naming a backend that
    does not exist, whose expression was the prose sentence "forall p prime:
    every group of order p^2 is abelian", earned the grade-level
    ``computation_checked`` with ``validate_run`` passing and no machine work at
    all.

    The check is re-derived through the same ``verify_check`` the single writer
    uses, so there is one implementation and no second copy to drift. Returns
    ``{"verdict", "backend", "computed"}``; None means the kernel could not
    derive anything and callers must treat that as failure, never as permission.
    """

    key = (str(kind), str(expression), str(expected))
    if key in _COMPUTATION_CACHE:
        return _COMPUTATION_CACHE[key]
    derived: dict[str, str] | None = None
    try:
        from espresso_proof.verification.verifier import verify_check

        out = verify_check(
            {
                "id": "kernel_recompute",
                "claim_id": "kernel_recompute",
                "machine_check": {
                    "kind": key[0],
                    "expression": key[1],
                    "expected": key[2],
                },
            }
        )
    except Exception:
        out = None
    if isinstance(out, dict):
        derived = {
            "verdict": str(out.get("verdict") or ""),
            "backend": str(out.get("backend") or ""),
            "computed": str(out.get("computed") or ""),
        }
    _COMPUTATION_CACHE[key] = derived
    return derived


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
    def _gl2_solvable(_spec: dict[str, Any]) -> dict[str, Any]:
        out = gl2_f3_solvability_witness()
        return {**out, "canonical_target": "solvability of the explicit finite group GL_2(F_3)"}

    def _gl2_spectrum(_spec: dict[str, Any]) -> dict[str, Any]:
        out = gl2_f3_order_spectrum_witness()
        return {**out, "canonical_target": "GL_2(F_3) is not isomorphic to S_4 x C_2"}

    register_recomputer("gl2_f3_solvability_witness", _gl2_solvable)
    register_recomputer("gl2_f3_order_spectrum_witness", _gl2_spectrum)

    def _permutation_group(spec: dict[str, Any]) -> dict[str, Any]:
        gens = spec.get("generators") or []
        out = permutation_group_witness(
            gens,
            expected_order=spec.get("expected_order"),
            expected_solvable=spec.get("expected_solvable"),
        )
        return {
            **out,
            "canonical_target": (
                "order and derived series of the group generated by "
                f"{len(gens) if isinstance(gens, list) else 0} supplied permutation(s)"
            ),
        }

    register_recomputer("permutation_group_witness", _permutation_group)

    from espresso_proof.verification.finite_group_verifier import verify_identity_on_table

    def _identity_table(spec: dict[str, Any]) -> dict[str, Any]:
        table = spec.get("table") or []
        identity = str(spec.get("identity", ""))
        out = verify_identity_on_table(table, identity)
        return {
            "confirmed": bool(out.get("holds")),
            "canonical_target": (
                f"identity {identity!r} on a finite operation table of order "
                f"{len(table) if isinstance(table, list) else 0}"
            ),
        }

    register_recomputer("verify_identity_on_table", _identity_table)

    from espresso_proof.verification.symbolic_identity import verify_ring_identity_mod

    def _ring_identity(spec: dict[str, Any]) -> dict[str, Any]:
        identity = str(spec.get("identity", ""))
        modulus = spec.get("modulus")
        out = verify_ring_identity_mod(identity, modulus)
        return {
            "confirmed": bool(out.get("holds")),
            "canonical_target": f"algebraic identity {identity!r} over Z/{modulus}",
        }

    register_recomputer("verify_ring_identity_mod", _ring_identity)

    from espresso_proof.verification.matrix_verifier import verify_matrix_rank

    def _matrix_rank(spec: dict[str, Any]) -> dict[str, Any]:
        matrix = spec.get("matrix") or []
        rows = len(matrix) if isinstance(matrix, list) else 0
        cols = len(matrix[0]) if rows and isinstance(matrix[0], list) else 0
        out = verify_matrix_rank(
            spec.get("matrix"),
            expected_rank=spec.get("expected_rank"),
            expected_determinant=spec.get("expected_determinant"),
        )
        return {
            "confirmed": out.get("verdict") == "confirmed",
            "canonical_target": f"rank/determinant of an explicit {rows}x{cols} integer matrix",
        }

    register_recomputer("verify_matrix_rank", _matrix_rank)

    from espresso_proof.verification.finite_group_verifier import verify_group_abelian

    def _group_abelian(spec: dict[str, Any]) -> dict[str, Any]:
        table = spec.get("table") or []
        out = verify_group_abelian(table)
        return {
            "confirmed": bool(out.get("is_group") and out.get("is_abelian")),
            "canonical_target": (
                "group and abelian laws on an explicit Cayley table of order "
                f"{len(table) if isinstance(table, list) else 0}"
            ),
        }

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
        return {
            "confirmed": pow(base, exp, mod) == expected,
            "canonical_target": f"exact modular exponentiation {base}^{exp} mod {mod}",
        }

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
        return {
            "confirmed": w % m != 0 and (n * w) % m == 0,
            "canonical_target": (
                f"non-injectivity witness for multiplication by {n} on Z/{m}"
            ),
        }

    register_recomputer("tensor_left_exact_counterexample", _tensor_left_exact)



__all__ = [
    "REQUESTED_FOR_SUFFIX",
    "checked_entry_rejection_reason",
    "is_recomputable",
    "recompute_computation_check",
    "recompute_result",
    "register_recomputer",
]
