"""The trust boundary as a machine-checked import contract.

The project's central architectural claim is that agents propose and the kernel
decides. That claim has a structural half: the kernel must not depend on the
packages that run models, or the boundary is a convention rather than a fact of
the import graph. It held by convention and behavioral tests only; this pins it
in CI so a refactor cannot silently invert an edge.

The contract (checked on module-level AND function-level imports — a deferred
import is still a dependency):

- ``kernel/`` imports nothing from ``agents``, ``orchestration``, ``ui``,
  ``front_half``, ``server``, ``literature``, ``strategy``, ``memory``.
- ``validation/`` (the other status-deciding package) has the same ceiling.
- ``verification/`` may not import ``agents`` or ``orchestration`` — with the
  single recorded exception of the LLM theorem-applicability review, which is
  explicitly advisory and is listed here so removing it (see the roadmap) turns
  into deleting a line, and adding a second one turns into a failing test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "espresso_proof"

# package under test -> packages it must never import (module- or function-level)
_FORBIDDEN: dict[str, set[str]] = {
    "kernel": {
        "agents", "orchestration", "ui", "front_half", "server",
        "literature", "strategy", "memory",
    },
    "validation": {
        "agents", "orchestration", "ui", "front_half", "server",
        "literature", "strategy", "memory",
    },
    "verification": {"agents", "orchestration"},
}

# (file relative to espresso_proof/, forbidden package) pairs that are known,
# documented, and tolerated FOR NOW. Every entry here is debt with a name on it.
_RECORDED_EXCEPTIONS: set[tuple[str, str]] = {
    # The LLM applicability review itself moved to
    # agents/theorem_applicability_review.py; what remains here is a one-function
    # deferred re-export kept so existing imports do not break. Remove the shim
    # (and this entry) once callers import from the agents module directly.
    ("verification/theorem_applicability.py", "agents"),
}


def _imported_espresso_packages(path: Path) -> set[str]:
    """Top-level espresso_proof subpackages imported anywhere in the file."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    packages: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for name in names:
            parts = name.split(".")
            if parts[0] == "espresso_proof" and len(parts) > 1:
                packages.add(parts[1])
    return packages


def _violations(package: str, forbidden: set[str]) -> list[str]:
    found: list[str] = []
    for path in sorted((PACKAGE_ROOT / package).rglob("*.py")):
        rel = str(path.relative_to(PACKAGE_ROOT))
        for target in sorted(_imported_espresso_packages(path) & forbidden):
            if (rel, target) in _RECORDED_EXCEPTIONS:
                continue
            found.append(f"{rel} imports espresso_proof.{target}")
    return found


@pytest.mark.parametrize("package", sorted(_FORBIDDEN))
def test_layer_never_imports_upward(package: str) -> None:
    violations = _violations(package, _FORBIDDEN[package])
    assert violations == [], (
        f"{package}/ must not depend on model-running or product packages; "
        "either the edge is wrong or it needs a recorded exception with a "
        f"removal plan:\n" + "\n".join(violations)
    )


def test_recorded_exceptions_still_exist() -> None:
    # An exception whose import is gone is a stale entry: delete it so the
    # contract tightens automatically when the debt is paid.
    for rel, target in sorted(_RECORDED_EXCEPTIONS):
        path = PACKAGE_ROOT / rel
        assert path.exists(), f"stale exception: {rel} no longer exists"
        assert target in _imported_espresso_packages(path), (
            f"stale exception: {rel} no longer imports espresso_proof.{target}; "
            "remove the entry so the contract tightens"
        )


def test_the_contract_covers_real_files() -> None:
    # Guard against a silent rename making the walk vacuous.
    for package in _FORBIDDEN:
        assert any((PACKAGE_ROOT / package).rglob("*.py")), package
