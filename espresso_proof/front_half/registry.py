"""Front-half stage registry (Phase 04 split).

Stage registration + the canonical FRONT_HALF_STAGES order and shared status
constants. The concrete normalizers live under ``stages``/``_agents``; this is
the registration surface they share.
"""

from __future__ import annotations

from ._agents import (
    FRONT_HALF_STAGES,
    StageNormalizer,
    _STAGE_REGISTRY,
    register_stage,
    registered_stages,
)

__all__ = [
    "FRONT_HALF_STAGES",
    "StageNormalizer",
    "_STAGE_REGISTRY",
    "register_stage",
    "registered_stages",
]
