"""The instant-draft SSE endpoint is fast, scrubbed, and advisory.

It acks immediately, streams the DRAFT, persists only an advisory sidecar
(never a kernel artifact), upgrades no status, is CSRF-guarded, and validate_run
still passes after the draft is written.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from espresso_proof.ui import instant_lane
from espresso_proof.ui.instant_lane import INSTANT_FILE, load_instant_conversation
from espresso_proof.kernel.proof_language import contains_proof_language
from espresso_proof.server import Workbench, _make_handler
from espresso_proof.kernel.validators import validate_run

from tests.support_jobs import wait_for_job

CLAIM = "Every group of order p^2, where p is prime, is abelian."


@pytest.fixture()
def client(tmp_path: Path):
    wb = Workbench(tmp_path / "root", provider="stub")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(wb))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def call(method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read().decode()

    yield wb, call
    httpd.shutdown()


class _FakeFastClient:
    """Stands in for the bounded preview model. It echoes the prompt back with
    proof-complete language attached, so the scrub-on-the-way-in contract is
    exercised against MODEL output and not just against the user's message."""

    provider = "external"
    model = "fake-fast-model"

    def complete_json(self, *, system: str, prompt: str) -> dict:
        return {
            "answer": f"First read of {prompt.splitlines()[0]} — we have proved it, proof-close.",
            "routes": ["Reduce to the abelian case."],
            "caveats": ["The finiteness hypothesis is needed."],
        }


@pytest.fixture()
def fast_model(monkeypatch):
    """Configure the preview lane with a model. Without one the lane correctly
    shows no draft (see test_a_lane_with_no_model_streams_no_draft_lines), which
    is not the case these streaming tests are about."""

    monkeypatch.setattr(instant_lane, "build_fast_client", lambda provider: _FakeFastClient())


def _create(call) -> str:
    status, _, raw = call("POST", "/api/runs", {"text": CLAIM})
    assert status == 202
    job_id = json.loads(raw)["job_id"]
    job = wait_for_job(lambda: json.loads(call("GET", f"/api/jobs/{job_id}")[2]))
    return job["result"]["run_id"]


def _parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.strip().split("\n\n"):
        evt, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                evt = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if evt:
            events.append({"event": evt, "data": data})
    return events


def test_instant_draft_acks_then_streams_labeled_draft(client, fast_model) -> None:
    wb, call = client
    run_id = _create(call)
    start = time.monotonic()
    status, ctype, raw = call(
        "POST", f"/api/runs/{run_id}/instant-draft", {"message": "is this true?"}
    )
    elapsed = time.monotonic() - start
    assert status == 200 and "text/event-stream" in ctype
    assert elapsed < 3.0  # the whole draft/fallback is fast

    events = _parse_sse(raw)
    assert events[0]["event"] == "ack"  # acknowledged first
    drafts = [e["data"]["text"] for e in events if e["event"] == "draft"]
    assert drafts, "the lane streams draft lines"
    assert drafts[0].startswith("First read of")  # the model's text, first

    done = events[-1]
    assert done["event"] == "done" and done["data"]["ok"] is True
    metrics = done["data"]["instant_lane_metrics"]
    assert metrics["instant_lane_status"] in ("draft", "fallback", "unavailable", "failed")
    assert metrics["background_run_started"] is True


def test_transient_instant_draft_needs_no_run_and_persists_nothing(client, fast_model) -> None:
    # the PRE-RUN lane answers the claim with no run id, decoupled from creation.
    wb, call = client
    start = time.monotonic()
    status, ctype, raw = call("POST", "/api/instant-draft", {"message": "is this true?"})
    elapsed = time.monotonic() - start
    assert status == 200 and "text/event-stream" in ctype
    assert elapsed < 3.0

    events = _parse_sse(raw)
    assert events[0]["event"] == "ack"
    drafts = [e["data"]["text"] for e in events if e["event"] == "draft"]
    assert drafts and drafts[0].startswith("First read of")
    done = events[-1]
    assert done["event"] == "done" and done["data"]["ok"] is True
    # no run was created/touched: background_run_started is False and the runs
    # root has no project runs.
    assert done["data"]["instant_lane_metrics"]["background_run_started"] is False
    assert not list((wb.root).glob("projects/*/runs/*"))


def test_a_lane_with_no_model_streams_no_draft_lines(client) -> None:
    """No preview model configured (the fixture's workbench is provider=stub):
    the endpoint still acks and completes cleanly, and streams zero draft lines
    instead of claim-independent filler. The panel renders that as one explicit
    line, so the page is never dead and never padded."""

    wb, call = client
    status, ctype, raw = call("POST", "/api/instant-draft", {"message": "is this true?"})
    assert status == 200 and "text/event-stream" in ctype
    events = _parse_sse(raw)
    assert events[0]["event"] == "ack"
    assert [e for e in events if e["event"] == "draft"] == []
    done = events[-1]
    assert done["event"] == "done" and done["data"]["ok"] is True


def test_transient_instant_draft_blocks_cross_site_origin(client) -> None:
    wb, call = client
    status, _, raw = call(
        "POST", "/api/instant-draft", {"message": "x"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert status == 403 and "cross-site" in json.loads(raw)["error"]


def test_instant_draft_is_scrubbed_even_on_hostile_message(client, fast_model) -> None:
    wb, call = client
    run_id = _create(call)
    _, _, raw = call(
        "POST", f"/api/runs/{run_id}/instant-draft",
        {"message": "We have proved this theorem; proof-close, proof complete."},
    )
    drafts = [e["data"]["text"] for e in _parse_sse(raw) if e["event"] == "draft"]
    joined = " ".join(drafts)
    assert contains_proof_language(joined) is None
    assert "proof-close" not in joined.lower()


def test_instant_draft_persists_only_advisory_sidecar(client, fast_model) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    grade_before = (run_dir / "idea_grade.yaml").read_bytes()
    src_before = (run_dir / "source_grounding.yaml").read_bytes()

    call("POST", f"/api/runs/{run_id}/instant-draft", {"message": "draft please"})

    # the advisory sidecar exists...
    assert (run_dir / INSTANT_FILE).exists()
    assert load_instant_conversation(run_dir) is not None
    # ...but it is NOT a kernel artifact, status artifacts are untouched, and the
    # run still validates.
    from espresso_proof.kernel.io import read_yaml

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    names = set((manifest.get("artifacts") or {}).values()) | set(
        (manifest.get("optional_artifacts") or {}).values()
    )
    assert INSTANT_FILE not in names
    assert (run_dir / "idea_grade.yaml").read_bytes() == grade_before
    assert (run_dir / "source_grounding.yaml").read_bytes() == src_before
    validate_run(run_dir)


def test_instant_draft_blocks_cross_site_origin(client) -> None:
    wb, call = client
    run_id = _create(call)
    status, _, raw = call(
        "POST", f"/api/runs/{run_id}/instant-draft", {"message": "x"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert status == 403 and "cross-site" in json.loads(raw)["error"]


def test_instant_draft_rejects_bad_run_id(client) -> None:
    wb, call = client
    status, _, _ = call("POST", "/api/runs/%2e%2e%2fx/instant-draft", {"message": "x"})
    assert status in (400, 403, 404)
