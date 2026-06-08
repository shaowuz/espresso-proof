"""Compatibility shim — front-half agents.

The implementation lives in the ``espresso_proof.front_half`` package (Phase 04
module split; stages physically split into ``front_half/stages/<name>.py``).
This module keeps ``from espresso_proof.front_half_agents import ...`` working
unchanged: it re-exports the public API and preserves stage registration and
runtime behavior.

New code should import from ``espresso_proof.front_half`` (or its ``registry`` /
``safety`` / ``runner`` / ``stages`` submodules).
"""

from __future__ import annotations

from .front_half import (  # noqa: F401
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
