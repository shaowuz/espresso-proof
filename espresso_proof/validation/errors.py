"""Shared exception type for Espresso Proof kernel validation.

Defined here (rather than in validators.py) so focused validation submodules
can raise it without importing the top-level validators module, which would
create a circular import.
"""

from __future__ import annotations


class KernelValidationError(Exception):
    """Raised when a run artifact violates a kernel rule."""
