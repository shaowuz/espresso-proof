"""Cross-process per-run writer lock.

The Product Workbench server already serializes mutating actions on one run
with in-process ``threading.Lock``s, but nothing stops a second PROCESS (a CLI
``orchestrate --resume`` next to the server, or two CLI invocations) from
mutating the same run concurrently and interleaving prover rounds or manifest
writes. This module adds an advisory ``flock`` on a lock file inside the run
directory, acquired by the shared mutating choke points (resume, single-agent
runs, prover-loop continuation, verification appends). POSIX uses
``fcntl.flock``; Windows uses ``msvcrt.locking`` over a single byte in the same
lock file.

Plumbing only: the lock never interprets or upgrades proof/source/evidence
status, and it guards writers only — read paths stay lock-free. Contention
fails closed with a friendly, product-copy error instead of corrupting the run.
"""

from __future__ import annotations

import errno
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:  # pragma: no cover - exercised on POSIX runners.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on Windows.
    _fcntl = None

try:  # pragma: no cover - exercised on Windows.
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX runners.
    _msvcrt = None

from espresso_proof.kernel.run_events import append_event

LOCK_FILENAME = ".run_writer_lock"
DEFAULT_TIMEOUT_SECONDS = 30.0
LOCK_REGION_BYTES = 1

_held = threading.local()

_LOCK_RETRY_ERRNOS = {
    errno.EAGAIN,
    errno.EACCES,
    errno.EWOULDBLOCK,
    getattr(errno, "EDEADLK", errno.EACCES),
}


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


def _try_lock_fd(fd: int) -> None:
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        return
    if _msvcrt is None:
        raise RuntimeError("run_writer_lock requires fcntl or msvcrt")
    os.lseek(fd, 0, os.SEEK_SET)
    _msvcrt.locking(fd, _msvcrt.LK_NBLCK, LOCK_REGION_BYTES)


def _unlock_fd(fd: int) -> None:
    if _fcntl is not None:
        _fcntl.flock(fd, _fcntl.LOCK_UN)
        return
    if _msvcrt is None:
        raise RuntimeError("run_writer_lock requires fcntl or msvcrt")
    os.lseek(fd, 0, os.SEEK_SET)
    _msvcrt.locking(fd, _msvcrt.LK_UNLCK, LOCK_REGION_BYTES)


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
                _try_lock_fd(fd)
                break
            except OSError as exc:
                # Only genuine contention is retried; a real flock failure
                # (e.g. ENOLCK on an NFS mount) must surface, not masquerade
                # as "another action is updating this run" for 30 seconds.
                if exc.errno not in _LOCK_RETRY_ERRNOS:
                    raise
                if time.monotonic() >= deadline:
                    append_event(run_dir, "lock_denied", detail=action)
                    raise RunLockedError(action) from None
                time.sleep(0.05)
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)
            _unlock_fd(fd)
    finally:
        os.close(fd)


__all__ = ["run_writer_lock", "RunLockedError", "LOCK_FILENAME", "DEFAULT_TIMEOUT_SECONDS"]
