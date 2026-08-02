"""One deadline policy for every background-job poll loop in the tests.

The mechanism behind the suite's recurring flakes: 14 test files each hand-roll
"start a run job, poll with a hard 20-40s wall-clock deadline, assert done".
Under a fully loaded machine (16 xdist workers all creating stub runs and
validating), a legitimate creation can exceed a small deadline, so the same
handful of server/cache tests failed in full-suite runs and always passed on a
solo rerun. A deadline in a test exists to catch a HANG, not to benchmark run
creation, so it must be far above any legitimate duration under load; one
shared constant keeps that judgment in one place instead of thirteen.
"""

from __future__ import annotations

import time

# Generous by design: a hang still fails (the suite-level timeout backstops the
# absolute worst case), while a loaded machine no longer produces false alarms.
JOB_DEADLINE_SECONDS = 120.0
POLL_INTERVAL_SECONDS = 0.2


def wait_for_job(
    poll, *, expect: str = "done", deadline_seconds: float = JOB_DEADLINE_SECONDS
) -> dict:
    """Poll ``poll()`` (a callable returning the job dict) until terminal.

    Returns the terminal job dict; asserts its state is ``expect`` ("done" for
    the happy path, "failed" for tests that pin an honest failure). Raises
    AssertionError with the last observed state if the deadline passes — the
    hang-detection property every hand-rolled loop was trying to keep.
    """

    deadline = time.time() + deadline_seconds
    job: dict = {}
    while time.time() < deadline:
        job = poll() or {}
        if job.get("state") in ("done", "failed"):
            assert job["state"] == expect, job
            return job
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"background job did not finish before the deadline: {job}")


def wait_for_run_idle(call, run_id: str, *, deadline_seconds: float = JOB_DEADLINE_SECONDS) -> None:
    """Block until no job is queued/running on ``run_id``.

    A run now continues itself past the prover gate, so "the create job
    finished" no longer means "the run has stopped changing". Tests that poke a
    run's endpoints need it quiescent; the cockpit uses the same signal to show
    that it is working and to offer Stop.
    """

    deadline = time.time() + deadline_seconds
    last: object = None
    while time.time() < deadline:
        status, summary = call("GET", f"/api/runs/{run_id}/status")
        # A non-200 is NOT idleness: reading an error body as "no active job"
        # is how this helper silently returned while the run was still being
        # written, which is exactly the race it exists to prevent.
        if status != 200 or not isinstance(summary, dict):
            last = f"status {status}"
        else:
            last = summary.get("active_job")
            if not last:
                return
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"run {run_id} never went idle; last active job: {last}")
