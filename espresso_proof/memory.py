from __future__ import annotations

import re
from pathlib import Path

from .io import read_yaml
from .models import MemoryEntry, MemoryIndex, MemoryIndexEntry


_SUMMARY_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Previous failed routes", ("failed_attempt",)),
    ("Source gaps", ("source_gap",)),
    ("Tested examples", ("tested_example",)),
    ("Counterexamples", ("counterexample",)),
    ("User decisions", ("user_decision",)),
    ("Repaired claims", ("repaired_claim",)),
)

_TYPE_ORDER = {
    entry_type: order
    for order, (_, entry_types) in enumerate(_SUMMARY_SECTIONS)
    for entry_type in entry_types
}

_STOP_WORDS = {
    "all",
    "and",
    "are",
    "for",
    "has",
    "have",
    "that",
    "the",
    "then",
    "this",
    "with",
}


def load_memory_index(project_dir: Path | str) -> MemoryIndex:
    """Load and validate a project's memory index.

    Missing memory is represented as an empty index so callers can use memory
    retrieval before a project has produced any remembered runs.
    """

    project_path = Path(project_dir)
    index_path = _memory_dir(project_path) / "index.yaml"
    if not index_path.exists():
        return MemoryIndex(
            project_id=_project_id(project_path),
            last_updated="",
            lookup_keys=[],
            entries=[],
        )

    return MemoryIndex.model_validate(read_yaml(index_path))


def summarize_memory(project_dir: Path | str, claim_text: str | None = None) -> str:
    """Return a grouped, provenance-aware memory summary for prompt context."""

    project_path = Path(project_dir)
    index = load_memory_index(project_path)
    if not index.entries:
        return ""

    ranked_entries = _rank_entries(index.entries, claim_text)
    hydrated_entries = [(entry, _load_memory_entry(project_path, entry)) for entry in ranked_entries]

    sections: list[str] = []
    for title, entry_types in _SUMMARY_SECTIONS:
        lines = [
            _format_memory_line(index_entry, memory_entry)
            for index_entry, memory_entry in hydrated_entries
            if index_entry.type in entry_types
        ]
        if lines:
            sections.append(f"{title}:\n" + "\n".join(lines))

    return "\n\n".join(sections)


def _memory_dir(project_path: Path) -> Path:
    if project_path.name == "memory":
        return project_path
    return project_path / "memory"


def _project_id(project_path: Path) -> str:
    if project_path.name == "memory":
        return project_path.parent.name
    return project_path.name


def _rank_entries(entries: list[MemoryIndexEntry], claim_text: str | None) -> list[MemoryIndexEntry]:
    tokens = _tokens(claim_text or "")
    if not tokens:
        return sorted(entries, key=_entry_sort_key)

    return sorted(entries, key=lambda entry: (-_score_entry(entry, tokens), *_entry_sort_key(entry)))


def _entry_sort_key(entry: MemoryIndexEntry) -> tuple[int, str, str]:
    return (_TYPE_ORDER.get(entry.type, len(_TYPE_ORDER)), entry.claim_id, entry.id)


def _score_entry(entry: MemoryIndexEntry, claim_tokens: set[str]) -> int:
    entry_text = " ".join(
        [
            entry.claim_id,
            entry.type,
            entry.summary,
            " ".join(entry.tags),
            " ".join(entry.provenance),
        ]
    )
    return len(claim_tokens.intersection(_tokens(entry_text)))


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9_]+", text.lower())
        if len(token) > 2 and token not in _STOP_WORDS
    }


def _load_memory_entry(project_path: Path, index_entry: MemoryIndexEntry) -> MemoryEntry | None:
    for candidate in _entry_path_candidates(project_path, index_entry.entry_path):
        if not candidate.exists():
            continue
        try:
            return MemoryEntry.model_validate(read_yaml(candidate))
        except (OSError, TypeError, ValueError):
            return None
    return None


def _entry_path_candidates(project_path: Path, entry_path: str) -> list[Path]:
    raw_path = Path(entry_path)
    if raw_path.is_absolute():
        return [raw_path]

    project_root = project_path.parent if project_path.name == "memory" else project_path
    candidates = [
        _memory_dir(project_path) / raw_path.name,
        project_root / raw_path,
        project_root.parent / raw_path,
    ]
    if len(project_root.parents) >= 2:
        candidates.append(project_root.parents[1] / raw_path)

    unique_candidates: list[Path] = []
    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)
    return unique_candidates


def _format_memory_line(index_entry: MemoryIndexEntry, memory_entry: MemoryEntry | None) -> str:
    lines = [f"- [{index_entry.id}] {index_entry.summary}"]

    if memory_entry is not None and memory_entry.reusable_lesson:
        lines.append(f"  lesson: {memory_entry.reusable_lesson}")
    if index_entry.tags:
        lines.append(f"  tags: {', '.join(index_entry.tags)}")
    if index_entry.provenance:
        lines.append(f"  provenance: {', '.join(index_entry.provenance)}")

    return "\n".join(lines)


__all__ = ["load_memory_index", "summarize_memory"]
