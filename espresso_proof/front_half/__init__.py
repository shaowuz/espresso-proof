"""espresso_proof.front_half — front-half agent package (Phase 04 physical split).

registry.py = stage registry + order; safety.py = shared vocab/guards;
stages/<name>.py = one module per stage normalizer; runner.py = the pass runner +
durable audit artifact. front_half_agents.py remains a compatibility shim.
"""

from __future__ import annotations

from .registry import (  # noqa: F401
    FRONT_HALF_STAGES,
    StageNormalizer,
    register_stage,
    registered_stages,
)
from .runner import (  # noqa: F401
    FRONT_HALF_RUN_FILENAME,
    build_front_half_run_report,
    run_front_half_agent_pass,
    write_front_half_run,
)
from . import stages  # noqa: F401 - ensure stage registration

__all__ = [
    "FRONT_HALF_STAGES",
    "FRONT_HALF_RUN_FILENAME",
    "StageNormalizer",
    "build_front_half_run_report",
    "register_stage",
    "registered_stages",
    "run_front_half_agent_pass",
    "write_front_half_run",
]
