"""Kernel data models, grouped by theme.

Public surface unchanged: every model/enum/alias is re-exported here, so
`from espresso_proof.kernel.models import X` keeps working. A model_rebuild
completion pass resolves cross-module forward references (annotations are
strings under `from __future__ import annotations`; runtime dependencies are
explicit imports, and the only runtime edges are theme -> foundations and
registry -> everything)."""

from __future__ import annotations

from espresso_proof.kernel.models.foundations import *  # noqa: F401,F403
from espresso_proof.kernel.models.evidence import *  # noqa: F401,F403
from espresso_proof.kernel.models.literature import *  # noqa: F401,F403
from espresso_proof.kernel.models.machine_verification import *  # noqa: F401,F403
from espresso_proof.kernel.models.source_grounding import *  # noqa: F401,F403
from espresso_proof.kernel.models.review_strategy import *  # noqa: F401,F403
from espresso_proof.kernel.models.human_interaction import *  # noqa: F401,F403
from espresso_proof.kernel.models.agent_reports import *  # noqa: F401,F403
from espresso_proof.kernel.models.subgoals import *  # noqa: F401,F403
from espresso_proof.kernel.models.line_proof import *  # noqa: F401,F403
from espresso_proof.kernel.models.run_lifecycle import *  # noqa: F401,F403
from espresso_proof.kernel.models.registry import *  # noqa: F401,F403

# Private helpers consumed outside the package (memory boundary checks); a star
# import does not carry underscore names, so they are re-exported explicitly.
from espresso_proof.kernel.models.run_lifecycle import (  # noqa: F401
    _has_run_artifact_provenance,
    _validate_memory_text_boundary,
)

# Resolve cross-module forward references now that every model class is
# imported. Rebuilds with the fully-populated namespace; a no-op for models that
# are already complete. Restricted to models defined in this package.
from pydantic import BaseModel as _BaseModel
_MODELS_NAMESPACE = dict(globals())
for _model in list(globals().values()):
    if (
        isinstance(_model, type)
        and issubclass(_model, _BaseModel)
        and _model is not _BaseModel
        and getattr(_model, '__module__', '').startswith('espresso_proof.kernel.models')
    ):
        _model.model_rebuild(force=False, _types_namespace=_MODELS_NAMESPACE)

del _BaseModel, _MODELS_NAMESPACE, _model
