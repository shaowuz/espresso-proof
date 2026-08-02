"""A run gets as far as it can; a person steers it whenever they want.

The cockpit used to stop every run at the prover gate and wait for a click,
while the CLI's --auto-continue ran the same work to a finished memo. The two
front ends disagreed about whether a person was REQUIRED — and the gate was not
what kept the system honest anyway: no amount of running can reach a human
status, because the kernel decides that, not the pause.

Turning this on is only safe with the other half, pinned below: a finished run
must still accept a person's direction. Without it, automation would replace "a
run that waits for you" with "a run you cannot steer".
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from espresso_proof.kernel.validators import validate_run
from espresso_proof.server import Workbench, _make_handler
from tests.support_jobs import wait_for_job, wait_for_run_idle

CLAIM = "Every group of order 3 is cyclic."


@pytest.fixture()
def client(tmp_path: Path):
    wb = Workbench(tmp_path / "root", provider="stub", auto_continue=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(wb))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    yield wb, call
    httpd.shutdown()


def _create(call) -> str:
    status, body = call("POST", "/api/runs", {"text": CLAIM})
    assert status == 202
    job = wait_for_job(lambda: call("GET", f"/api/jobs/{body['job_id']}")[1])
    return job["result"]["run_id"]


def test_a_browser_run_finishes_without_anyone_clicking(client) -> None:
    wb, call = client
    run_id = _create(call)
    wait_for_run_idle(call, run_id)

    run_dir = wb.resolve_run(run_id)
    manifest = yaml.safe_load((run_dir / "run_manifest.yaml").read_text())
    assert manifest["current_state"] == "COMPLETED_MEMO"
    validate_run(run_dir)

    # And it says who decided. An auto choice can never claim human confirmation.
    choice = yaml.safe_load((run_dir / "human_choice.yaml").read_text())
    assert choice["selected_by"] == "auto"
    assert choice["human_confirmed"] is False


def test_a_person_can_still_direct_a_finished_run(client) -> None:
    """The half that makes the other half safe. Before this, a Continue click on
    a finished run reported success and did nothing: resume took the
    idempotent-memo branch, so the person had no lever left at all."""

    wb, call = client
    run_id = _create(call)
    wait_for_run_idle(call, run_id)
    run_dir = wb.resolve_run(run_id)
    before = yaml.safe_load((run_dir / "prover_loop.yaml").read_text())

    _, panel = call("GET", f"/api/runs/{run_id}/panel/strategies")
    strategy = panel["strategies"][0]["id"]
    status, body = call(
        "POST", f"/api/runs/{run_id}/continue",
        {"strategy": strategy, "effort": "quick_test"},
    )
    assert status == 202
    wait_for_job(lambda: call("GET", f"/api/jobs/{body['job_id']}")[1])
    wait_for_run_idle(call, run_id)

    after = yaml.safe_load((run_dir / "prover_loop.yaml").read_text())
    # A real round ran — not a re-rendered memo.
    assert len(after["attempts"]) == len(before["attempts"]) + 1
    # The extra spend is recorded, not hidden inside the original budget.
    assert after["budget"]["max_rounds"] > before["budget"]["max_rounds"]
    assert "a person authorized" in after["audit_note"]
    # And the choice is now the person's.
    choice = yaml.safe_load((run_dir / "human_choice.yaml").read_text())
    assert choice["selected_by"] == "human" and choice["human_confirmed"] is True
    validate_run(run_dir)


def test_the_run_reports_that_it_is_working(client) -> None:
    wb, call = client
    run_id = _create(call)
    # Whether or not we catch it mid-flight, the field exists and carries only
    # process state — never a mathematical one.
    _, summary = call("GET", f"/api/runs/{run_id}/status")
    active = summary.get("active_job")
    assert active is None or set(active) == {"job_id", "action", "state", "started_at"}
    wait_for_run_idle(call, run_id)
    _, summary = call("GET", f"/api/runs/{run_id}/status")
    assert summary["active_job"] is None
