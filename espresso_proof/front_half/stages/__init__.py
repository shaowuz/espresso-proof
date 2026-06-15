"""Front-half stage normalizers (one module per stage).

Importing this package imports every stage module, whose ``@register_stage``
decorators populate the registry so all FRONT_HALF_STAGES entries are registered.
"""

from __future__ import annotations

from . import (  # noqa: F401 - import side effect registers stages
    attacker,
    check_generator,
    counterexamples,
    examples,
    grade,
    literature,
    source,
    strategy,
    understanding,
)
