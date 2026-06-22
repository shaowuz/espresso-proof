"""Lightweight task-intent classification for claim-facing orchestration.

This is process guidance only. It never changes mathematical status; it helps
advisory stages distinguish "prove X" from "find a witness/counterexample to X"
so pressure-lane artifacts are worded and scored honestly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

TaskIntent = Literal["prove", "find_counterexample", "construct_witness", "evaluate"]


def infer_task_intent(claim: Mapping[str, object] | None) -> TaskIntent:
    claim = claim or {}
    raw = _field(claim, "raw_text")
    normalized = _field(claim, "normalized_statement")
    conclusion = _field(claim, "conclusion")
    blob = " ".join(part for part in (raw, normalized, conclusion) if part).lower()
    raw_lower = raw.lower()
    normalized_lower = normalized.lower()

    if raw_lower.startswith("prove ") or " prove " in f" {raw_lower} ":
        return "prove"

    if (
        "find a counterexample" in raw_lower
        or "find counterexample" in raw_lower
        or "give a counterexample" in raw_lower
        or "counterexample that" in raw_lower
        or "counterexample to" in raw_lower
    ):
        return "find_counterexample"

    if (
        raw_lower.startswith(("exhibit ", "construct ", "give an example"))
        or normalized_lower.startswith(("exhibit ", "construct "))
        or (
            "there exists" in blob
            and any(marker in blob for marker in ("such that", "witness", "example"))
        )
    ):
        return "construct_witness"

    return "evaluate"


def is_witness_intent(intent: TaskIntent | str) -> bool:
    return intent in {"find_counterexample", "construct_witness"}


def _field(claim: Mapping[str, object], key: str) -> str:
    value = claim.get(key)
    return str(value or "").strip()


__all__ = ["TaskIntent", "infer_task_intent", "is_witness_intent"]
