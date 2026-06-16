from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from .io import read_yaml, write_yaml
from .models import MemoryEntry, MemoryIndex, MemoryIndexEntry
from .models import _has_run_artifact_provenance, _validate_memory_text_boundary
from .proof_language import scrub_proof_language
from .research_bank import bankable_artifact_path, load_bankable_dict


_SUMMARY_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Previous failed routes", ("failed_attempt",)),
    ("Source gaps", ("source_gap",)),
    ("Tested examples", ("tested_example",)),
    ("Counterexamples", ("counterexample",)),
    ("Theorem applicability failures", ("theorem_applicability_failure",)),
    ("Reviewer objections", ("reviewer_objection",)),
    ("Human example feedback", ("human_example_feedback",)),
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

_MEMORY_INDEX_REQUIRED_ENTRY_FIELDS = {
    "id",
    "entry_path",
    "claim_id",
    "type",
    "summary",
    "provenance",
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

    raw = read_yaml(index_path)
    try:
        return MemoryIndex.model_validate(raw)
    except ValueError:
        return _adapt_legacy_memory_index(project_path, raw)


def _adapt_legacy_memory_index(project_dir: Path, raw: object) -> MemoryIndex:
    if not isinstance(raw, dict):
        raise ValueError("malformed memory index")
    raw_entries = raw.get("entries", [])
    if not isinstance(raw_entries, list):
        raise ValueError("malformed memory index entries")

    entries: list[MemoryIndexEntry] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("malformed memory index entry")
        missing = sorted(_MEMORY_INDEX_REQUIRED_ENTRY_FIELDS.difference(raw_entry))
        if missing:
            raise ValueError(f"malformed memory index entry missing fields: {missing}")
        entry = dict(raw_entry)
        entry.setdefault("tags", [])
        if not isinstance(entry["provenance"], list):
            raise ValueError("malformed memory index entry provenance")
        if not isinstance(entry["tags"], list):
            raise ValueError("malformed memory index entry tags")
        try:
            _validate_memory_text_boundary(
                "legacy memory index entry",
                [
                    str(entry.get("summary") or ""),
                    str(entry.get("entry_path") or ""),
                    *[str(tag) for tag in (entry.get("tags") or [])],
                ],
            )
        except ValueError:
            continue
        provenance = entry.get("provenance") or []
        if not _has_run_artifact_provenance([str(item) for item in provenance]):
            entry["provenance"] = [
                f"projects/{_project_id(project_dir)}/runs/__legacy_memory_index__/index.yaml"
            ]
        try:
            entries.append(MemoryIndexEntry.model_validate(entry))
        except ValueError:
            continue

    return MemoryIndex(
        project_id=str(raw.get("project_id") or _project_id(project_dir)),
        last_updated=str(raw.get("last_updated") or ""),
        lookup_keys=[str(item) for item in (raw.get("lookup_keys") or [])],
        entries=entries,
    )


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

    # Display-layer safety: memory summaries echo prior-run free text (lessons,
    # objections); redact any global proof-complete overclaim. Advisory text only.
    return scrub_proof_language("\n\n".join(sections))


def write_memory_entry(
    project_dir: Path | str,
    entry: dict,
    *,
    filename: str | None = None,
    tags: list[str] | None = None,
) -> Path:
    """Write a validated memory entry and update the project memory index."""

    project_path = Path(project_dir)
    project_id = _project_id(project_path)
    MemoryEntry.model_validate(entry)
    memory_dir = _memory_dir(project_path)
    entry_filename = filename or f"{entry['id']}.yaml"
    if Path(entry_filename).name != entry_filename:
        raise ValueError("memory entry filename must not contain directories")
    index = _build_updated_memory_index(
        memory_dir,
        project_id,
        entry,
        entry_filename,
        tags=tags or [],
    )
    MemoryIndex.model_validate(index)
    entry_path = memory_dir / entry_filename
    write_yaml(entry_path, entry)
    _write_memory_index(memory_dir, index)
    return entry_path


def build_grade_memory_entry(
    idea_grade: dict,
    strategy_brief: dict,
    *,
    project_id: str,
    run_id: str,
) -> dict:
    """Build the primary advisory memory entry for a run grade."""

    def prov(name: str) -> str:
        return f"projects/{project_id}/runs/{run_id}/{name}"

    source_gaps = idea_grade.get("source_gaps") or []
    top_strategies = strategy_brief.get("top_strategies") or []
    top_strategy_id = (
        top_strategies[0].get("strategy_id") or top_strategies[0].get("id") or "none"
        if top_strategies
        else "none"
    )
    return {
        "id": f"memory_{run_id}",
        "timestamp": datetime.now(UTC).isoformat(),
        "project": project_id,
        "type": "source_gap" if source_gaps else "failed_attempt",
        "claim_id": idea_grade["claim_id"],
        "summary": f"grade score {idea_grade['score']}; top strategy id: {top_strategy_id}",
        "provenance": [
            prov("idea_grade.yaml"),
            prov("strategy_synthesis.yaml"),
            prov("strategy_brief.yaml"),
        ],
        "reusable_lesson": idea_grade["next_cheapest_test"],
    }


def write_run_memory(
    project_dir: Path | str,
    run_dir: Path | str,
    *,
    project_id: str,
    run_id: str,
) -> list[Path]:
    """Write advisory memory entries derived from a completed run's artifacts.

    This is the single, unified memory writer (callers must not reimplement index
    logic). Every entry is purely advisory: ``MemoryEntry`` has no status field,
    so nothing written here can set source_backed, mark examples/computations
    checked, or upgrade proof status. Entries are reusable lessons/ranking hints
    only.

    Categories produced when their source artifact is present:

    - grade summary -> ``source_gap`` or ``failed_attempt``
    - ``review.yaml`` strongest objection / failed reviewers -> ``reviewer_objection``
    - ``counterexamples.yaml`` refuting/step-only pressure -> ``counterexample``
    - ``theorem_applicability.yaml`` blocking usages -> ``theorem_applicability_failure``
    - ``human_example_feedback.yaml`` items -> ``human_example_feedback``
    """

    project_path = Path(project_dir)
    run_path = Path(run_dir)
    written: list[Path] = []

    def prov(name: str) -> str:
        return f"projects/{project_id}/runs/{run_id}/{name}"

    def emit(suffix: str, entry: dict) -> None:
        filename = f"{run_id}.yaml" if not suffix else f"{run_id}_{suffix}.yaml"
        written.append(write_memory_entry(project_path, entry, filename=filename))

    idea_grade = read_yaml(run_path / "idea_grade.yaml")
    claim_id = idea_grade["claim_id"]
    timestamp = datetime.now(UTC).isoformat()

    strategy_brief = read_yaml(run_path / "strategy_brief.yaml")
    emit(
        "",
        build_grade_memory_entry(
            idea_grade,
            strategy_brief,
            project_id=project_id,
            run_id=run_id,
        ),
    )

    review_path = run_path / "review.yaml"
    if review_path.exists():
        review = read_yaml(review_path)
        objection = (review.get("strongest_objection") or "").strip()
        failed = [r["reviewer_id"] for r in review.get("reviewers", []) if r.get("verdict") == "fail"]
        if objection or failed:
            failed_label = ", ".join(str(item) for item in failed) if failed else "none"
            emit(
                "reviewer",
                {
                    "id": f"memory_{run_id}_reviewer",
                    "timestamp": timestamp,
                    "project": project_id,
                    "type": "reviewer_objection",
                    "claim_id": claim_id,
                    "summary": f"reviewer objection recorded; failed reviewers: {failed_label}",
                    "provenance": [prov("review.yaml")],
                    "reusable_lesson": "inspect review.yaml before reusing this route",
                },
            )

    # Resolve via the manifest + bank-aware loader so a materialized
    # (bank-directory) run still contributes counterexample-pressure memory.
    cx_path = bankable_artifact_path(run_path, "counterexamples")
    if cx_path.exists():
        counterexamples = load_bankable_dict(run_path, "counterexamples")
        pressure = [
            test
            for test in counterexamples.get("tests", [])
            if test.get("refutes_claim") or test.get("refutes_step_only")
        ]
        if pressure:
            first = pressure[0]
            emit(
                "counterexample",
                {
                    "id": f"memory_{run_id}_counterexample",
                    "timestamp": timestamp,
                    "project": project_id,
                    "type": "counterexample",
                    "claim_id": claim_id,
                    "summary": f"counterexample pressure recorded: {first.get('id')}",
                    "provenance": [prov("counterexamples.yaml")],
                    "reusable_lesson": "retest this counterexample pressure in future runs",
                },
            )

    ta_path = run_path / "theorem_applicability.yaml"
    if ta_path.exists():
        theorem_applicability = read_yaml(ta_path)
        blocking = [
            usage
            for usage in theorem_applicability.get("usages", [])
            if usage.get("blocks_source_backed") or usage.get("applicability_status") != "applicable"
        ]
        if blocking:
            emit(
                "applicability",
                {
                    "id": f"memory_{run_id}_applicability",
                    "timestamp": timestamp,
                    "project": project_id,
                    "type": "theorem_applicability_failure",
                    "claim_id": claim_id,
                    "summary": f"{len(blocking)} blocking theorem usage(s) recorded",
                    "provenance": [prov("theorem_applicability.yaml")],
                    "reusable_lesson": "pin and check theorem hypotheses before reusing this theorem",
                },
            )

    hef_path = run_path / "human_example_feedback.yaml"
    if hef_path.exists():
        feedback = read_yaml(hef_path)
        items = feedback.get("feedback", [])
        if items:
            emit(
                "feedback",
                {
                    "id": f"memory_{run_id}_feedback",
                    "timestamp": timestamp,
                    "project": project_id,
                    "type": "human_example_feedback",
                    "claim_id": claim_id,
                    "summary": f"{len(items)} human example feedback item(s) recorded",
                    "provenance": [prov("human_example_feedback.yaml")],
                    "reusable_lesson": "reuse human taste as a ranking hint only, never as checked evidence",
                },
            )

    return written


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


def _update_memory_index(
    memory_dir: Path,
    project_id: str,
    entry: dict,
    entry_filename: str,
    *,
    tags: list[str],
) -> None:
    index = _build_updated_memory_index(
        memory_dir,
        project_id,
        entry,
        entry_filename,
        tags=tags,
    )
    _write_memory_index(memory_dir, index)


def _build_updated_memory_index(
    memory_dir: Path,
    project_id: str,
    entry: dict,
    entry_filename: str,
    *,
    tags: list[str],
) -> dict:
    index_path = memory_dir / "index.yaml"
    if index_path.exists():
        existing = load_memory_index(memory_dir)
        index = existing.model_dump(mode="json")
    else:
        index = {
            "project_id": project_id,
            "last_updated": datetime.now(UTC).isoformat(),
            "lookup_keys": [],
            "entries": [],
        }

    entry_tags = _memory_tags(entry, extra=tags)
    index["entries"] = [item for item in index.get("entries", []) if item.get("id") != entry["id"]]
    index["entries"].append(
        {
            "id": entry["id"],
            "entry_path": f"projects/{project_id}/memory/{entry_filename}",
            "claim_id": entry["claim_id"],
            "type": entry["type"],
            "tags": entry_tags,
            "summary": entry["summary"],
            "provenance": entry["provenance"],
        }
    )
    lookup_keys = set(index.get("lookup_keys", []))
    lookup_keys.update(entry_tags)
    lookup_keys.update(item["claim_id"] for item in index["entries"])
    index["lookup_keys"] = sorted(lookup_keys)
    index["last_updated"] = datetime.now(UTC).isoformat()
    MemoryIndex.model_validate(index)
    return index


def _write_memory_index(memory_dir: Path, index: dict) -> None:
    index_path = memory_dir / "index.yaml"
    write_yaml(index_path, index)


def _memory_tags(entry: dict, *, extra: list[str]) -> list[str]:
    summary = str(entry.get("summary", "")).lower()
    tags = {entry["type"], *extra}
    if "source" in summary or entry["type"] == "source_gap":
        tags.add("source_gap")
    if "fragile" in summary:
        tags.add("fragile")
    if "unclear" in summary:
        tags.add("unclear")
    if entry["type"] == "user_decision":
        tags.add("human_choice")
    return sorted(tag for tag in tags if tag)


__all__ = ["load_memory_index", "summarize_memory", "write_memory_entry", "write_run_memory"]
