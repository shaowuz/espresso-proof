"""Compatibility shim — front-half agents.

The implementation was split into the ``espresso_proof.front_half`` package in
Phase 04 (Front-Half Module Split). This module is kept so existing imports
``from espresso_proof.front_half_agents import ...`` keep working unchanged; it
re-exports the public API and preserves stage registration and runtime behavior.

New code should import from ``espresso_proof.front_half`` (or its ``registry`` /
``safety`` / ``runner`` / ``stages`` submodules).
"""

from __future__ import annotations

from .front_half._agents import *  # noqa: F401,F403 - preserve full public surface
from .front_half._agents import (  # explicit, stable public re-exports
    FRONT_HALF_RUN_FILENAME,
    FRONT_HALF_STAGES,
    StageNormalizer,
    build_front_half_run_report,
    register_stage,
    registered_stages,
    run_front_half_agent_pass,
    write_front_half_run,
)
from .front_half import stages as _stages  # noqa: F401 - ensure stages registered

__all__ = [
    "FRONT_HALF_RUN_FILENAME",
    "FRONT_HALF_STAGES",
    "StageNormalizer",
    "build_front_half_run_report",
    "register_stage",
    "registered_stages",
    "run_front_half_agent_pass",
    "write_front_half_run",
]
