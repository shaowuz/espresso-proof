"""Front-half stage registry + canonical stage order (Phase 04 physical split)."""

from __future__ import annotations

from collections.abc import Callable

# Front-half stages in Core-Flow order. Names are stable identifiers used by the
# registry, the CLI ``--front-half`` plumbing, and the orchestrator.
FRONT_HALF_STAGES: list[str] = [
    "understanding_claim",  # S1: locked_claim + hypothesis_ledger (+ claim_precision)
    "literature_review",    # S2: candidate references only
    "examples",             # S3: examples + computation_checks
    "counterexamples",      # S4: counterexamples + admissibility tests
    "source_theorem",       # S7: source matching + theorem applicability (candidate)
    "regrade",              # evidence-aware grade recompute (before strategy)
    "strategy",             # S5: strategy generator + grader + brief
    "attacker",             # S6: example/counterexample attacker
    "grade_memory_memo",    # S8: grade refresh + memory + concise answer
]

# stage name -> normalizer(run_dir=..., client=..., context=...) -> dict
# A normalizer returns an outcome dict; recognized keys: "unsafe_language" (list)
# and "notes" (list). Slices populate this registry via ``register_stage``.
StageNormalizer = Callable[..., dict]
_STAGE_REGISTRY: dict[str, StageNormalizer] = {}


def register_stage(name: str) -> Callable[[StageNormalizer], StageNormalizer]:
    """Decorator: register a normalizer for a front-half stage."""

    if name not in FRONT_HALF_STAGES:
        raise ValueError(f"unknown front-half stage: {name}")

    def _decorator(fn: StageNormalizer) -> StageNormalizer:
        _STAGE_REGISTRY[name] = fn
        return fn

    return _decorator


def registered_stages() -> list[str]:
    """Stages that currently have a normalizer, in Core-Flow order."""

    return [stage for stage in FRONT_HALF_STAGES if stage in _STAGE_REGISTRY]


__all__ = ["FRONT_HALF_STAGES", "StageNormalizer", "_STAGE_REGISTRY", "register_stage", "registered_stages"]
