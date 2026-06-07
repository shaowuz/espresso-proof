"""Shared front-half safety surface (Phase 04 split).

Status vocabularies and the conservative guards that keep model output from
self-certifying source/evidence/proof status. Imported by stages, never copied.
These do not change behavior; they centralize the audit surface.
"""

from __future__ import annotations

from ._agents import (
    _CANDIDATE_SOURCE_STATUSES,
    _HYPOTHESIS_STATUSES,
    _candidate_status,
    _looks_imprecise,
    _neutralize_review_if_failed,
    _safe_lock_route,
)

__all__ = [
    "_CANDIDATE_SOURCE_STATUSES",
    "_HYPOTHESIS_STATUSES",
    "_candidate_status",
    "_looks_imprecise",
    "_neutralize_review_if_failed",
    "_safe_lock_route",
]
