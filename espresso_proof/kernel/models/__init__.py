"""Kernel data models (split into file-order slices).

Public surface unchanged: every model/enum/alias is re-exported here, so
`from espresso_proof.kernel.models import X` keeps working. A model_rebuild
completion pass resolves the few cross-slice forward references."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from espresso_proof.kernel.models._part1 import *  # noqa: F401,F403

from espresso_proof.kernel.models._part2 import *  # noqa: F401,F403

from espresso_proof.kernel.models._part3 import *  # noqa: F401,F403

from espresso_proof.kernel.models._part4 import *  # noqa: F401,F403

from espresso_proof.kernel.models._part5 import *  # noqa: F401,F403

from espresso_proof.kernel.models._part6 import *  # noqa: F401,F403

from espresso_proof.kernel.models._part7 import *  # noqa: F401,F403

from espresso_proof.kernel.models._part8 import *  # noqa: F401,F403

# Resolve cross-slice forward references (e.g. PolynomialWitness -> CounterexampleTest,
# Literature* -> Review) now that every model class is imported. Rebuilds with the
# fully-populated namespace; a no-op for models that are already complete. Restricted
# to models defined in this package (never the imported pydantic BaseModel root).
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

# Drop the rebuild-pass temporaries so they do not leak as package attributes.
del _BaseModel, _MODELS_NAMESPACE, _model
