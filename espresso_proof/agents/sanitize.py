"""Agent-output sanitization helpers (redaction, normalization, parsing).

Split out of ``espresso_proof.agents.llm_agents``; that module remains the
compatibility facade and re-exports every name defined here. The canonical
redaction primitives (``safe_text`` / ``safe_list`` / ``forbidden_pattern`` /
``FORBIDDEN_LANGUAGE`` / ``RESERVED_STATUS_LANGUAGE``) live in
``espresso_proof.kernel.proof_language`` and are imported from there.
"""

from __future__ import annotations

import re

from espresso_proof.kernel.proof_language import (
    FORBIDDEN_LANGUAGE,
    RESERVED_STATUS_LANGUAGE,
    forbidden_pattern,
    safe_text,
)


# Phrases redacted from live agent text by safe_text/safe_list. This MUST stay a
# superset of the proof-complete phrases the kernel forbids in
# validators._first_forbidden_proof_language: the prover-round language guard
# scans the (already redacted) content fields, so any phrase the validator
# detects but redaction misses would let a live reviewer/prover sentence (e.g.
# "... is proven complete") fail the run closed instead of being contained.
# test_forbidden_language_redaction_covers_validator pins this invariant.


def safe_prover_list(value: object) -> tuple[list[str], bool]:
    if not isinstance(value, list):
        return [], False
    out: list[str] = []
    changed = False
    for item in value:
        if not str(item).strip():
            continue
        text, item_changed = safe_prover_text(item)
        out.append(text)
        changed = changed or item_changed
        if len(out) >= 12:
            break
    return out, changed


def safe_prover_text(value: object) -> tuple[str, bool]:
    text = safe_text(value)
    neutralized = _neutralize_unchecked_pressure_support(text)
    return neutralized, neutralized != text


def _neutralize_unchecked_pressure_support(text: str) -> str:
    """Keep live prover prose from treating pressure-lane context as evidence.

    The kernel validator remains authoritative and still rejects hand-edited or
    missed misuse. This adapter layer only rewrites common live-agent phrasing
    before the advisory artifact is committed.
    """

    replacements = [
        (
            r"\b(unchecked\s+(?:examples?|counterexamples?))\s+"
            r"(?:support|supports|establish|establishes|prove|proves)\b",
            r"\1 suggests pressure for",
        ),
        (
            r"\b(unchecked\s+(?:citations?|sources?|references?))\s+"
            r"(?:support|supports|establish|establishes|prove|proves)\b",
            r"\1 suggests a source request for",
        ),
        (
            r"\b(pending\s+theorem\s+applicability)\s+"
            r"(?:support|supports|establish|establishes|prove|proves)\b",
            r"\1 remains an unresolved condition for",
        ),
        (
            r"\b(line-level\s+gap)\s+"
            r"(?:support|supports|establish|establishes|prove|proves)\b",
            r"\1 remains an unresolved condition for",
        ),
    ]
    out = text
    for pattern, replacement in replacements:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return out


def collect_unsafe_language(raw: dict) -> list[str]:
    text = " ".join(
        [
            str(raw.get("summary", "")),
            " ".join(str(item) for item in raw.get("steps", []) if item),
            " ".join(str(item) for item in raw.get("risks", []) if item),
        ]
    ).lower()
    return [
        phrase
        for phrase in [*FORBIDDEN_LANGUAGE, *RESERVED_STATUS_LANGUAGE]
        if re.search(forbidden_pattern(phrase), text, flags=re.IGNORECASE)
    ]


def normalize_verdict(value: object) -> str:
    raw = str(value).strip()
    if raw in {"plausible_direction", "needs_revision", "needs_sources", "blocked"}:
        return raw
    if "source" in raw:
        return "needs_sources"
    if "block" in raw or "stop" in raw:
        return "blocked"
    return "needs_revision"


def normalize_action(value: object) -> str:
    raw = str(value).strip()
    if raw in {"continue", "revise", "stop"}:
        return raw
    if "stop" in raw or "block" in raw:
        return "stop"
    if "continue" in raw:
        return "continue"
    return "revise"


def extract_response_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    chunks = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(content.get("text", ""))
    return "\n".join(chunks).strip()


def strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    return stripped
