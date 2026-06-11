"""Cross-process per-run writer lock (Goal 35B).

The Product Workbench server already serializes mutating actions on one run
with in-process ``threading.Lock``s, but nothing stops a second PROCESS (a CLI
``orchestrate --resume`` next to the server, or two CLI invocations) from
mutating the same run concurrently and interleaving prover rounds or manifest
writes. This module adds an advisory ``flock`` on a lock file inside the run
directory, acquired by the shared mutating choke points (resume, single-agent
runs, prover-loop continuation, verification appends).

Plumbing only: the lock never interprets or upgrades proof/source/evidence
status, and it guards writers only — read paths stay lock-free. Contention
fails closed with a friendly, product-copy error instead of corrupting the run.
"""

from __future__ import annotations

import fcntl
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .run_events import append_event

LOCK_FILENAME = ".run_writer_lock"
DEFAULT_TIMEOUT_SECONDS = 30.0

_held = threading.local()


class RunLockedError(RuntimeError):
    """Another process is currently updating this run (fail closed, friendly)."""

    def __init__(self, action: str) -> None:
        super().__init__(
            "Another action is currently updating this run. The run was not "
            f"changed by this {action}. Please wait a moment and try again."
        )


def _held_keys() -> set[str]:
    keys = getattr(_held, "keys", None)
    if keys is None:
        keys = set()
        _held.keys = keys
    return keys


@contextmanager
def run_writer_lock(
    run_dir: Path,
    *,
    action: str = "action",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
):
    """Hold the per-run writer lock while mutating ``run_dir``.

    Re-entrant per thread, so an outer caller (CLI resume) and the inner choke
    points it reaches (prover-loop continuation, verification appends) share one
    acquisition instead of deadlocking. Waits up to ``timeout_seconds`` for a
    holder in another process/thread, then raises :class:`RunLockedError`
    without touching the run.
    """

    run_dir = Path(run_dir)
    key = str(run_dir.resolve())
    held = _held_keys()
    if key in held:
        yield
        return

    lock_path = run_dir / LOCK_FILENAME
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    append_event(run_dir, "lock_denied", detail=action)
                    raise RunLockedError(action) from None
                time.sleep(0.05)
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


__all__ = ["run_writer_lock", "RunLockedError", "LOCK_FILENAME", "DEFAULT_TIMEOUT_SECONDS"]
