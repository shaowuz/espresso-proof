"""The canonical front-half stage order — the CONTRACT, not the implementation.

Defined in the kernel because the kernel-side validator enforces it
(validation/front_half.py) and the kernel must not import the package it
validates. front_half/registry.py re-exports this list and binds implementations
to it; everything else keeps importing it from there.
"""

from __future__ import annotations

FRONT_HALF_STAGES: list[str] = [
    "understanding_claim",  # S1: locked_claim + hypothesis_ledger (+ claim_precision)
    "literature_review",    # S2: candidate references only
    "examples",             # S3: examples + computation_checks
    "counterexamples",      # S4: counterexamples + admissibility tests
    "source_theorem",       # S7: source matching + theorem applicability (candidate)
    "check_generator",      # Rail-A machine_check proposals bound to source steps
    "regrade",              # evidence-aware grade recompute (before strategy)
    "strategy",             # S5: strategy generator + grader + brief
    "attacker",             # S6: example/counterexample attacker
    "grade_memory_memo",    # S8: grade refresh + memory + concise answer
]

__all__ = ["FRONT_HALF_STAGES"]
