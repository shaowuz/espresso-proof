"""Canonical mainline identity (Goal 35A).

The authoritative ``espresso_proof`` package is the ``espresso_proof/``
directory that sits next to this ``tests/`` directory. CI installs the package
with ``pip install -e .`` from this checkout, so ``import espresso_proof`` must
resolve here. If it resolves anywhere else (for example a sibling editable
install such as ``../espresso-proof-run`` left on ``sys.path``), every test and gate
in this run would silently validate code that is not the code under review.
These tests make that failure loud instead of silent.
"""

from __future__ import annotations

from pathlib import Path

import espresso_proof

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_espresso_proof_resolves_to_this_checkout() -> None:
    package_init = Path(espresso_proof.__file__).resolve()
    expected = REPO_ROOT / "espresso_proof" / "__init__.py"
    assert package_init == expected, (
        "import espresso_proof resolved to a different checkout: "
        f"{package_init} (expected {expected}). The gate is running against "
        "the wrong package; fix the environment before trusting any result."
    )


def test_espresso_proof_is_a_single_unsplit_package() -> None:
    # A namespace-package split across two checkouts could mix modules from
    # different versions; the canonical package must have exactly one location.
    locations = [Path(p).resolve() for p in espresso_proof.__path__]
    assert locations == [REPO_ROOT / "espresso_proof"], (
        f"espresso_proof package path is not the canonical checkout: {locations}"
    )
