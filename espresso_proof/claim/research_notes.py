"""User research/chat notes with provenance — advisory only.

A note may bind to a strategy, source, example, counterexample, or subgoal so a
human observation has a home. Like strategy feedback it lives in a SIDECAR the
kernel never reads as evidence:

- the strict model rejects any status/evidence field;
- the note text is proof-language-scrubbed before it is stored or shown;
- binding a note never sets or upgrades proof/source/evidence/grade/claim
  status — its only effect is display, and (when it influences a future round)
  that influence shows up in the advisory time-allocation / prompt-context
  ledger, never as earned evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.proof_language import scrub_proof_language

NOTES_FILE = "research_notes.yaml"

TargetKind = Literal["strategy", "source", "example", "counterexample", "subgoal"]


class ResearchNote(BaseModel):
    """One advisory note bound to a run object. Strict: unknown fields rejected."""

    model_config = ConfigDict(extra="forbid")

    note_id: str
    run_id: str
    target_kind: TargetKind
    target_id: str
    text: str
    created_at: str
    advisory_only: Literal[True] = True


class ResearchNoteLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    notes: list[ResearchNote] = Field(default_factory=list)


def load_notes(run_dir: Path) -> ResearchNoteLog:
    path = Path(run_dir) / NOTES_FILE
    if not path.exists():
        return ResearchNoteLog(run_id=Path(run_dir).name)
    try:
        return ResearchNoteLog.model_validate(read_yaml(path))
    except Exception:  # noqa: BLE001 — advisory sidecar fails closed to empty
        return ResearchNoteLog(run_id=Path(run_dir).name)


def append_note(run_dir: Path, note: ResearchNote) -> ResearchNoteLog:
    """Scrub, then atomically append the note to the advisory sidecar."""

    scrubbed = note.model_copy(update={"text": scrub_proof_language(note.text).strip()[:500]})
    log = load_notes(run_dir)
    log.notes.append(scrubbed)
    log.notes = log.notes[-200:]
    write_yaml(Path(run_dir) / NOTES_FILE, log.model_dump(mode="json"))
    return log


def notes_for(run_dir: Path, target_kind: str, target_id: str) -> list[ResearchNote]:
    return [
        n
        for n in load_notes(run_dir).notes
        if n.target_kind == target_kind and n.target_id == target_id
    ]
