"""espresso_proof.front_half — front-half agent package (Phase 04 split).

The implementation was moved verbatim from the former
``espresso_proof/front_half_agents.py`` module into this package; that module is
now a thin compatibility shim. Public API is re-exported here and from the shim.
"""

from __future__ import annotations

from ._agents import (  # noqa: F401
    FRONT_HALF_RUN_FILENAME,
    FRONT_HALF_STAGES,
    StageNormalizer,
    build_front_half_run_report,
    register_stage,
    registered_stages,
    run_front_half_agent_pass,
    write_front_half_run,
)
from . import stages  # noqa: F401 - ensure stage registration

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
