"""Transaction-oriented stage runner for initial orchestration.

`StageRunner` gives `orchestrate_run` the same snapshot/rollback discipline the
prover-continuation loop already has (`prover_loop_driver._snapshot_run` /
`_restore_run`), but as one generic, CLI-independent transaction boundary:

- snapshot the declared mutable paths (default: the whole run directory),
- run the stage mutation,
- validate (default: `validate_run`),
- advance ``run_manifest.current_state`` ONLY after validation passes,
- restore the snapshot on any mutation/validation failure, and re-raise with
  recorded failure context (never swallow),
- keep every snapshot/restore path confined to the run directory.

It is plumbing only: it never interprets proof/source/evidence status, never
bypasses ``validate_run``, and never deletes anything outside the run directory.
Each stage returns a plain audit outcome (used by Phase 03).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .io import read_yaml, write_yaml
from .observability import AGENT_EVENTS_FILENAME, TIMINGS_FILENAME
from .run_events import EVENTS_FILENAME, append_event
from .run_lock import LOCK_FILENAME

MANIFEST_NAME = "run_manifest.yaml"

# Goal 67B: the advisory prover-sketch sidecar. Defined here (next to the
# exclusion set that must list it) so the capture module can import the name
# without stage_runner importing the heavier capture module (which would pull
# validators into this low-level plumbing). Pure diagnostic sink — never a kernel
# artifact, never consumed by validate_run.
ADVISORY_PROOF_SKETCH_FILENAME = "advisory_proof_sketch.yaml"

# Runtime plumbing files that are never part of a stage transaction: the
# append-only diagnostic timeline + per-run observability logs (rolling them
# back would erase the record of the failed stage they exist to explain — in
# particular a failed agent call's record) and the writer lock file (unlinking
# it would silently break a concurrently-held flock by splitting it across
# inodes). Goal 54A: agent_events.jsonl / timings.yaml join events.jsonl here.
# Goal 67B: advisory_proof_sketch.yaml joins them so a rolled-back prover attempt
# does not erase its (scrubbed, advisory-only) mathematical reasoning.
_SNAPSHOT_EXCLUDED_NAMES = frozenset(
    {EVENTS_FILENAME, AGENT_EVENTS_FILENAME, TIMINGS_FILENAME, LOCK_FILENAME,
     ADVISORY_PROOF_SKETCH_FILENAME}
)


@dataclass
class StageOutcome:
    """A plain, status-free audit record of one stage transaction."""

    name: str
    status: str  # "ok" | "skipped" | "rolled_back"
    state_after: str | None = None
    error: str | None = None


class StageError(RuntimeError):
    """A stage mutation/validation failed; the run was rolled back to its last
    valid state. Carries the failing stage name and original cause."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"stage {stage!r} failed and was rolled back: {message}")
        self.stage = stage


class StageRunner:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.outcomes: list[StageOutcome] = []

    # -- internal: path confinement ------------------------------------------
    def _confined(self, raw: Path | str) -> Path:
        path = Path(raw)
        candidate = path if path.is_absolute() else (self.run_dir / path)
        resolved = candidate.resolve()
        if resolved != self.run_dir and self.run_dir not in resolved.parents:
            raise ValueError(f"snapshot path escapes run directory: {raw!r}")
        return resolved

    # -- internal: snapshot / restore ----------------------------------------
    def _snapshot(self, scope: list[Path]):
        files: dict[Path, bytes | None] = {}
        covered_dirs: list[Path] = []
        for root in scope:
            if root.is_dir():
                covered_dirs.append(root)
                for f in root.rglob("*"):
                    if f.is_file() and f.name not in _SNAPSHOT_EXCLUDED_NAMES:
                        files[f] = f.read_bytes()
            else:
                files[root] = root.read_bytes() if root.exists() else None
        return files, covered_dirs

    def _restore(self, snapshot) -> None:
        files, covered_dirs = snapshot
        # Drop files created inside a covered directory during the failed stage.
        for directory in covered_dirs:
            if directory.exists():
                for f in directory.rglob("*"):
                    if f.is_file() and f not in files and f.name not in _SNAPSHOT_EXCLUDED_NAMES:
                        f.unlink()
        # Restore captured file bytes (or remove files that did not exist before).
        for path, data in files.items():
            if data is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

    # -- internal: lifecycle state -------------------------------------------
    def _advance_state(self, state_after: str) -> None:
        manifest_path = self.run_dir / MANIFEST_NAME
        manifest = read_yaml(manifest_path)
        manifest["current_state"] = state_after
        write_yaml(manifest_path, manifest)

    # -- public API -----------------------------------------------------------
    def run(
        self,
        *,
        name: str,
        mutate: Callable[[], None],
        validate: Callable[[], None],
        state_after: str | None = None,
        snapshot_paths: list[Path | str] | None = None,
    ) -> StageOutcome:
        """Run one stage as a transaction. The run manifest is always part of the
        snapshot. ``snapshot_paths=None`` snapshots the entire run directory (the
        safest default); pass an explicit list to narrow the transaction. All
        paths must stay inside the run directory.
        """

        if snapshot_paths is None:
            scope = [self.run_dir]
        else:
            scope = [self._confined(p) for p in snapshot_paths]
            manifest_path = self.run_dir / MANIFEST_NAME
            if manifest_path not in scope:
                scope.append(manifest_path)

        append_event(self.run_dir, "stage_started", stage=name)
        snapshot = self._snapshot(scope)
        try:
            mutate()
            validate()
            append_event(self.run_dir, "validation_passed", stage=name)
            if state_after is not None:
                self._advance_state(state_after)
        except Exception as exc:  # noqa: BLE001 - restore then re-raise with context
            self._restore(snapshot)
            append_event(self.run_dir, "validation_failed", stage=name, detail=str(exc))
            append_event(self.run_dir, "rollback", stage=name)
            outcome = StageOutcome(
                name=name, status="rolled_back", state_after=None, error=str(exc)
            )
            self.outcomes.append(outcome)
            raise StageError(name, str(exc)) from exc

        append_event(self.run_dir, "stage_ok", stage=name)
        outcome = StageOutcome(name=name, status="ok", state_after=state_after)
        self.outcomes.append(outcome)
        return outcome


__all__ = ["StageRunner", "StageOutcome", "StageError"]
