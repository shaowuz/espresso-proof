"""Front-half stage registry + canonical stage order (Phase 04 physical split)."""

from __future__ import annotations

from collections.abc import Callable

# Front-half stages in Core-Flow order. Names are stable identifiers used by the
# registry, the CLI ``--front-half`` plumbing, and the orchestrator.
# Canonical order lives in the kernel (the validator enforces it and must not
# import this package); re-exported here so implementations and callers keep
# one import site.
from espresso_proof.kernel.front_half_contract import FRONT_HALF_STAGES

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
