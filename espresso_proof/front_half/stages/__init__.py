"""Front-half stage normalizers.

Importing the implementation module runs the ``@register_stage`` decorators so
every FRONT_HALF_STAGES entry is registered. The concrete normalizers currently
live in ``espresso_proof.front_half._agents`` (moved verbatim in the Phase 04
split); this package is the import surface that guarantees registration.
"""

from __future__ import annotations

from .. import _agents  # noqa: F401 - import side effect registers stages
