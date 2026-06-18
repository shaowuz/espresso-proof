"""Deterministic finite-group micro-verifier (Six-Hour goal, Phase 4).

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
