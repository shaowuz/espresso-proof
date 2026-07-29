"""Deterministic finite-group micro-verifier.

A small, zero-dependency, auditable backend that checks a property of an
EXPLICITLY-GIVEN finite group (as a Cayley table) by brute force:

- ``is_group``: closure + identity + inverses + associativity over the table;
- ``is_abelian``: commutativity of the table.

This earns a SCOPED, instance-level machine status (the specific group, or the
specific finite set of groups, that was checked). It can NEVER earn a universal
``proved``: checking the groups of order 4 and 9 does not prove a statement for
all primes p. Natural-language prover/reviewer text is not a verifier and cannot
reach this backend.
"""

from __future__ import annotations

import hashlib
import json
from itertools import permutations, product


def verify_group_abelian(cayley: list[list[int]]) -> dict:
    """Deterministically check whether ``cayley`` is the table of an abelian group.

    Elements are 0..n-1; ``cayley[a][b]`` is the product a*b. Returns a dict with
    ``order``, ``is_group``, ``is_abelian``, and a ``detail`` string. Fails closed:
    any malformed table yields is_group=False (never raises).
    """
    n = len(cayley)
    # shape + entry-range check (closure into 0..n-1)
    if n == 0 or any(len(row) != n for row in cayley):
        return {"order": n, "is_group": False, "is_abelian": False, "detail": "non-square table"}
    if any(not isinstance(x, int) or x < 0 or x >= n for row in cayley for x in row):
        return {"order": n, "is_group": False, "is_abelian": False, "detail": "entry out of range (closure fails)"}
    # latin-square (each row/col a permutation) -> quasigroup
    for i in range(n):
        if len(set(cayley[i])) != n:
            return {"order": n, "is_group": False, "is_abelian": False, "detail": f"row {i} not a permutation"}
        if len({cayley[r][i] for r in range(n)}) != n:
            return {"order": n, "is_group": False, "is_abelian": False, "detail": f"column {i} not a permutation"}
    # identity element
    identity = None
    for e in range(n):
        if all(cayley[e][x] == x and cayley[x][e] == x for x in range(n)):
            identity = e
            break
    if identity is None:
        return {"order": n, "is_group": False, "is_abelian": False, "detail": "no identity element"}
    # inverses
    for a in range(n):
        if not any(cayley[a][b] == identity and cayley[b][a] == identity for b in range(n)):
            return {"order": n, "is_group": False, "is_abelian": False, "detail": f"element {a} has no inverse"}
    # associativity
    for a in range(n):
        for b in range(n):
            ab = cayley[a][b]
            for c in range(n):
                if cayley[ab][c] != cayley[a][cayley[b][c]]:
                    return {
                        "order": n, "is_group": False, "is_abelian": False,
                        "detail": f"associativity fails at ({a},{b},{c})",
                    }
    is_abelian = all(cayley[a][b] == cayley[b][a] for a in range(n) for b in range(n))
    return {
        "order": n,
        "is_group": True,
        "is_abelian": is_abelian,
        "detail": "verified abelian group" if is_abelian else "verified group, non-abelian",
    }


SUPPORTED_IDENTITIES = ("commutative", "associative", "idempotent", "involutive")


def _table_is_malformed(table: object) -> bool:
    # Treat ANY non list-of-lists input as malformed (fail closed, never raise):
    # the CLI feeds arbitrary user YAML here (null / scalar / flat list).
    if not isinstance(table, list) or len(table) == 0:
        return True
    n = len(table)
    if any(not isinstance(row, list) or len(row) != n for row in table):
        return True
    return any(not isinstance(x, int) or x < 0 or x >= n for row in table for x in row)


def verify_identity_on_table(table: list[list[int]], identity: str) -> dict:
    """Exhaustively check a single algebraic identity on an EXPLICITLY-GIVEN
    finite operation table (``table[a][b]`` = a*b over elements 0..n-1).

    Supported identities: ``commutative`` (a*b=b*a), ``associative``
    ((a*b)*c=a*(b*c)), ``idempotent`` (a*a=a), ``involutive`` (a*a=e for the
    table's identity element). Returns a SCOPED result: ``holds`` is true only
    for THIS finite table; ``scope_note`` states it is a finite/computational
    check, never a universal proof. Fails closed: an unsupported identity, a
    malformed table, or (for ``involutive``) a missing identity element yields
    ``holds=False`` and never raises. Earns no ``proved`` status."""

    n = len(table) if isinstance(table, list) else 0
    scope_note = (
        f"checked exhaustively only on this explicit finite operation table of "
        f"order {n}; a finite/computational check, not a universal proof"
    )
    result = {
        "identity": identity,
        "order": n,
        "supported": identity in SUPPORTED_IDENTITIES,
        "holds": False,
        "checked": 0,
        "scope_note": scope_note,
        "detail": "",
    }
    if identity not in SUPPORTED_IDENTITIES:
        result["detail"] = f"unsupported identity {identity!r} (fail closed)"
        return result
    if _table_is_malformed(table):
        result["detail"] = "malformed operation table (fail closed)"
        return result

    checked = 0
    if identity == "commutative":
        for a in range(n):
            for b in range(n):
                checked += 1
                if table[a][b] != table[b][a]:
                    result.update(checked=checked, detail=f"fails at ({a},{b})")
                    return result
    elif identity == "associative":
        for a in range(n):
            for b in range(n):
                ab = table[a][b]
                for c in range(n):
                    checked += 1
                    if table[ab][c] != table[a][table[b][c]]:
                        result.update(checked=checked, detail=f"fails at ({a},{b},{c})")
                        return result
    elif identity == "idempotent":
        for a in range(n):
            checked += 1
            if table[a][a] != a:
                result.update(checked=checked, detail=f"fails at {a}")
                return result
    elif identity == "involutive":
        e = next(
            (x for x in range(n) if all(table[x][y] == y and table[y][x] == y for y in range(n))),
            None,
        )
        if e is None:
            result["detail"] = "no identity element; involution undefined (fail closed)"
            return result
        for a in range(n):
            checked += 1
            if table[a][a] != e:
                result.update(checked=checked, detail=f"fails at {a}")
                return result

    result.update(holds=True, checked=checked, detail=f"holds on this finite table (order {n})")
    return result


def cyclic_group_table(n: int) -> list[list[int]]:
    """Cayley table of the cyclic group C_n (addition mod n)."""
    return [[(a + b) % n for b in range(n)] for a in range(n)]


def elementary_abelian_table(p: int, k: int = 2) -> list[list[int]]:
    """Cayley table of (Z/p)^k, default k=2 -> C_p x C_p (componentwise add mod p)."""
    elements = []
    def gen(prefix: list[int]) -> None:
        if len(prefix) == k:
            elements.append(tuple(prefix))
            return
        for d in range(p):
            gen(prefix + [d])
    gen([])
    index = {e: i for i, e in enumerate(elements)}
    n = len(elements)
    table = [[0] * n for _ in range(n)]
    for i, a in enumerate(elements):
        for j, b in enumerate(elements):
            s = tuple((a[t] + b[t]) % p for t in range(k))
            table[i][j] = index[s]
    return table


def all_groups_of_order_p_squared(p: int) -> list[tuple[str, list[list[int]]]]:
    """Return the (known) complete list of groups of order p^2, up to isomorphism:
    C_{p^2} and C_p x C_p. NOTE: that these are ALL groups of order p^2 is the
    classification theorem (NOT machine-verified here); this helper only supplies
    their tables so the verifier can machine-check each is an abelian group.
    """
    return [
        (f"C_{p*p}", cyclic_group_table(p * p)),
        (f"C_{p} x C_{p}", elementary_abelian_table(p, 2)),
    ]


Matrix2 = tuple[int, int, int, int]


def gl2_f3_solvability_witness() -> dict:
    """Explicit finite-object witness for ``GL_2(F_3)`` solvability.

    This is deliberately scoped to one finite group. It enumerates the invertible
    2x2 matrices over F_3, computes the commutator-derived series, and records a
    transcript/hash that an LLM or human reviewer can inspect. It is not a
    general group-theory rule checker.
    """

    elements = sorted(m for m in product(range(3), repeat=4) if _det_f3(m) != 0)
    element_set = set(elements)
    identity: Matrix2 = (1, 0, 0, 1)
    closure_ok = all(_matmul_f3(a, b) in element_set for a in elements for b in elements)
    inverse_ok = all(_matmul_f3(a, _inv_f3(a)) == identity for a in elements)

    series: list[list[Matrix2]] = [elements]
    current = set(elements)
    while len(current) > 1:
        commutators = {_commutator_f3(a, b) for a in current for b in current}
        current = _subgroup_generated(commutators)
        series.append(sorted(current))
        if len(series) > 10:  # defensive fail-closed guard; should never happen.
            break
    orders = [len(level) for level in series]
    transcript = {
        "field": "F_3",
        "element_encoding": "matrix (a,b,c,d) represents [[a,b],[c,d]] with entries mod 3",
        "multiplication": "[[a,b],[c,d]] * [[e,f],[g,h]] over F_3",
        "determinant_rule": "ad-bc mod 3; invertible iff determinant is nonzero",
        "order": len(elements),
        "closure_ok": closure_ok,
        "inverse_ok": inverse_ok,
        "derived_series_orders": orders,
        "derived_series_subgroup_hashes": [
            hashlib.sha256(json.dumps(level, sort_keys=True).encode("utf-8")).hexdigest()
            for level in series
        ],
    }
    transcript_hash = hashlib.sha256(
        json.dumps(transcript, sort_keys=True).encode("utf-8")
    ).hexdigest()
    solvable = bool(closure_ok and inverse_ok and orders == [48, 24, 8, 2, 1])
    return {
        **transcript,
        "is_solvable": solvable,
        "confirmed": solvable,
        "transcript_sha256": transcript_hash,
        "review_required": (
            "LLM/human reviewer should inspect that the explicit derived-series "
            "computation supports only GL_2(F_3), not a general theorem."
        ),
    }



def gl2_f3_order_spectrum_witness() -> dict:
    """Distinguish GL_2(F_3) from the other order-48 group it is confused with.

    A reviewer on a real run asked for "enough data to distinguish the abstract
    group", "not just a related order-48 group". Order alone cannot do that:
    there are many groups of order 48. The element-order spectrum can, and it is
    finite data this module can enumerate.

    GL_2(F_3) contains a nonsplit torus, so it has elements of order 8. The
    usual confusion, S_4 x C_2, has none. Differing spectra prove the two groups
    are not isomorphic; agreeing spectra would prove nothing, so this witness
    confirms only when they differ.
    """

    elements = [
        m
        for m in product(range(3), repeat=4)
        if _det_f3((m[0], m[1], m[2], m[3])) != 0
    ]
    identity: Matrix2 = (1, 0, 0, 1)

    def _order(m: Matrix2) -> int:
        current, n = m, 1
        while current != identity:
            current = _matmul_f3(current, m)
            n += 1
            if n > 48:  # cannot happen in a group of order 48; fail closed
                return -1
        return n

    spectrum: dict[int, int] = {}
    for m in elements:
        spectrum[_order(m)] = spectrum.get(_order(m), 0) + 1

    # S_4 x C_2, enumerated the same way rather than asserted from a table.
    perms = list(permutations(range(4)))

    def _pmul(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(a[b[i]] for i in range(4))

    def _porder(p: tuple[int, ...]) -> int:
        current, n, e = p, 1, (0, 1, 2, 3)
        while current != e:
            current = _pmul(current, p)
            n += 1
        return n

    rival: dict[int, int] = {}
    for p in perms:
        po = _porder(p)
        for central in (0, 1):
            o = po if central == 0 else (po * 2 if po % 2 else po)
            rival[o] = rival.get(o, 0) + 1

    order_ok = len(elements) == 48 and sum(rival.values()) == 48
    distinguishes = order_ok and spectrum != rival
    transcript = {
        "group": "GL_2(F_3)",
        "compared_against": "S_4 x C_2",
        "element_encoding": "matrix (a,b,c,d) represents [[a,b],[c,d]] with entries mod 3",
        "method": "enumerate all elements of both groups and count element orders",
        "order": len(elements),
        "rival_order": sum(rival.values()),
        "order_spectrum": {str(k): v for k, v in sorted(spectrum.items())},
        "rival_order_spectrum": {str(k): v for k, v in sorted(rival.items())},
        "separating_orders": sorted(
            str(k) for k in set(spectrum) | set(rival) if spectrum.get(k) != rival.get(k)
        ),
    }
    transcript_hash = hashlib.sha256(
        json.dumps(transcript, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        **transcript,
        "distinguishes": distinguishes,
        "confirmed": distinguishes,
        "transcript_sha256": transcript_hash,
        "review_required": (
            "This separates GL_2(F_3) from S_4 x C_2 only. It is not a "
            "classification of all groups of order 48, and it says nothing about "
            "whether either group occurs as a Galois group over Q."
        ),
    }

def _det_f3(m: Matrix2) -> int:
    a, b, c, d = m
    return (a * d - b * c) % 3


def _matmul_f3(x: Matrix2, y: Matrix2) -> Matrix2:
    a, b, c, d = x
    e, f, g, h = y
    return (
        (a * e + b * g) % 3,
        (a * f + b * h) % 3,
        (c * e + d * g) % 3,
        (c * f + d * h) % 3,
    )


def _inv_f3(m: Matrix2) -> Matrix2:
    a, b, c, d = m
    det = _det_f3(m)
    inv_det = 1 if det == 1 else 2
    return (
        (d * inv_det) % 3,
        (-b * inv_det) % 3,
        (-c * inv_det) % 3,
        (a * inv_det) % 3,
    )


def _commutator_f3(x: Matrix2, y: Matrix2) -> Matrix2:
    return _matmul_f3(_matmul_f3(_matmul_f3(_inv_f3(x), _inv_f3(y)), x), y)


def _subgroup_generated(generators: set[Matrix2]) -> set[Matrix2]:
    identity: Matrix2 = (1, 0, 0, 1)
    subgroup = set(generators)
    subgroup.add(identity)
    changed = True
    while changed:
        changed = False
        items = list(subgroup)
        for a in items:
            for b in items:
                product_ab = _matmul_f3(a, b)
                if product_ab not in subgroup:
                    subgroup.add(product_ab)
                    changed = True
    return subgroup
