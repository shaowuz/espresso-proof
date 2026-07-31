"""Symbolic algebraic-identity sanity check over Z/n (Proof Verification V2).

A small, zero-dependency, auditable backend that EXHAUSTIVELY checks a NAMED
algebraic identity over the finite ring Z/n (all residues 0..n-1) by brute force.
There is no expression parser and no ``eval``: each identity is a fixed Python
predicate, so the input surface is just ``(identity_name, modulus)``.

This earns a SCOPED, instance-level status: the identity was checked for THIS
finite modulus only. It can NEVER earn a universal ``proved`` -- in particular
``freshmans_dream`` ((a+b)^p = a^p + b^p) holds over Z/n only when n is prime, so
a confirming run for n=5 says nothing about a general ring or another modulus.
Fails closed: an unsupported identity or a bad modulus yields ``holds=False``.
"""

from __future__ import annotations

# identity name -> (arity, predicate over Z/n). Each predicate takes the modulus
# and a tuple of variable values and returns whether the identity holds there.
def _square_of_sum(n: int, vals: tuple[int, ...]) -> bool:
    a, b = vals
    return ((a + b) ** 2) % n == (a * a + 2 * a * b + b * b) % n


def _difference_of_squares(n: int, vals: tuple[int, ...]) -> bool:
    a, b = vals
    return ((a + b) * (a - b)) % n == (a * a - b * b) % n


def _freshmans_dream(n: int, vals: tuple[int, ...]) -> bool:
    # (a+b)^n == a^n + b^n in Z/n. Holds for every prime n (Frobenius in
    # char p) but NOT only for primes: any n with a^n == a for all a — the
    # Carmichael numbers 561, 1105, ... — satisfies it too, so confirming this
    # identity is never evidence of primality. (The first Carmichael number
    # sits above the current modulus cap, which is why the false "iff prime"
    # reading survived testing.)
    a, b = vals
    return pow(a + b, n, n) == (pow(a, n, n) + pow(b, n, n)) % n


def _distributive(n: int, vals: tuple[int, ...]) -> bool:
    a, b, c = vals
    return (a * (b + c)) % n == (a * b + a * c) % n


_RING_IDENTITIES: dict[str, tuple[int, object]] = {
    "square_of_sum": (2, _square_of_sum),
    "difference_of_squares": (2, _difference_of_squares),
    "freshmans_dream": (2, _freshmans_dream),
    "distributive": (3, _distributive),
}

SUPPORTED_RING_IDENTITIES = tuple(_RING_IDENTITIES)

# Guard the exhaustive sweep so a huge modulus cannot hang the kernel.
_MAX_MODULUS = 200


def verify_ring_identity_mod(identity: str, modulus: object) -> dict:
    """Exhaustively check a named algebraic identity over Z/n.

    Supported: ``square_of_sum``, ``difference_of_squares``, ``freshmans_dream``,
    ``distributive``. Returns a SCOPED result: ``holds`` is true only when the
    identity holds for EVERY residue assignment mod n; ``scope_note`` states this
    is a finite/computational sanity check over Z/n, not a universal proof. Fails
    closed: an unsupported identity, a non-int / out-of-range modulus, or a
    modulus above the safety cap yields ``holds=False`` and never raises. Earns no
    ``proved`` status."""

    supported = identity in _RING_IDENTITIES
    n = modulus if isinstance(modulus, int) and not isinstance(modulus, bool) else 0
    scope_note = (
        f"checked exhaustively for all residues modulo {n}; a finite/computational "
        f"sanity check over Z/{n}, not a proof over a general ring or for all moduli"
    )
    result = {
        "identity": identity,
        "modulus": n,
        "supported": supported,
        "holds": False,
        "checked": 0,
        "scope_note": scope_note,
        "detail": "",
    }
    if not supported:
        result["detail"] = f"unsupported identity {identity!r} (fail closed)"
        return result
    if not isinstance(modulus, int) or isinstance(modulus, bool) or modulus < 1:
        result["detail"] = "modulus must be an integer >= 1 (fail closed)"
        return result
    if modulus > _MAX_MODULUS:
        result["detail"] = f"modulus {modulus} exceeds safety cap {_MAX_MODULUS} (fail closed)"
        return result

    arity, predicate = _RING_IDENTITIES[identity]
    checked = 0

    def sweep(prefix: tuple[int, ...]) -> tuple[bool, tuple[int, ...] | None]:
        nonlocal checked
        if len(prefix) == arity:
            checked += 1
            return (predicate(n, prefix), prefix)
        for v in range(n):
            ok, witness = sweep(prefix + (v,))
            if not ok:
                return (False, witness)
        return (True, None)

    holds, witness = sweep(())
    if holds:
        result.update(holds=True, checked=checked, detail=f"holds for all residues mod {n}")
    else:
        result.update(checked=checked, detail=f"fails at {witness} (mod {n})")
    return result
