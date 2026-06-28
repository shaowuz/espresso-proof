"""Goal 43F: research/chat notes bind with provenance but are advisory only.

A note can bind to a strategy, source, example, counterexample, or subgoal
without changing any proof/source/evidence/grade/claim status; status/evidence
fields are rejected; proof-complete text is scrubbed; the sidecar is never read
as evidence by the kernel.
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
from pydantic import ValidationError

from espresso_proof.kernel.proof_language import contains_proof_language
from espresso_proof.claim.research_notes import (
    NOTES_FILE,
    ResearchNote,
    append_note,
    load_notes,
    notes_for,
)
from espresso_proof.server import Workbench, _make_handler

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
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    yield wb, call
    httpd.shutdown()


def _create(call) -> str:
    status, d = call("POST", "/api/runs", {"text": CLAIM})
    assert status == 202
    deadline = time.time() + 25
    while time.time() < deadline:
        _, jd = call("GET", f"/api/jobs/{d['job_id']}")
        if jd.get("state") in ("done", "failed"):
            assert jd["state"] == "done", jd
            return jd["result"]["run_id"]
        time.sleep(0.2)
    raise AssertionError("create job did not finish")


def test_note_model_rejects_status_fields() -> None:
    base = dict(
        note_id="n", run_id="r", target_kind="strategy", target_id="s1",
        text="looks promising", created_at="2026-06-13T00:00:00Z",
    )
    ResearchNote.model_validate(base)  # ok
    for field in ("status", "source_backed", "proved", "grade", "evidence"):
        with pytest.raises(ValidationError):
            ResearchNote.model_validate({**base, field: "proved"})
    with pytest.raises(ValidationError):
        ResearchNote.model_validate({**base, "advisory_only": False})
    with pytest.raises(ValidationError):
        ResearchNote.model_validate({**base, "target_kind": "theorem"})


def test_note_text_is_scrubbed(tmp_path: Path) -> None:
    append_note(
        tmp_path,
        ResearchNote(
            note_id="n", run_id="r", target_kind="example", target_id="ex1",
            text="this basically proves the theorem, proof-close",
            created_at="2026-06-13T00:00:00Z",
        ),
    )
    stored = load_notes(tmp_path).notes[0]
    assert contains_proof_language(stored.text) is None


@pytest.mark.parametrize(
    "kind,tid",
    [
        ("strategy", "strategy_lock_claim"),
        ("source", "src_candidate_001"),
        ("example", "ex_1"),
        ("counterexample", "ce_1"),
        ("subgoal", "subgoal_block_001"),
    ],
)
def test_note_binds_to_target_without_changing_status(client, kind, tid) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    grade_before = (run_dir / "idea_grade.yaml").read_bytes()
    src_before = (run_dir / "source_grounding.yaml").read_bytes()
    status, body = call(
        "POST",
        f"/api/runs/{run_id}/research-note",
        {"target_kind": kind, "target_id": tid, "text": "worth a closer look"},
    )
    assert status == 200 and body["ok"] is True
    assert body["target_kind"] == kind and body["target_id"] == tid
    # the note is recorded with provenance...
    assert len(notes_for(run_dir, kind, tid)) == 1
    # ...and NO earned status artifact changed.
    assert (run_dir / "idea_grade.yaml").read_bytes() == grade_before
    assert (run_dir / "source_grounding.yaml").read_bytes() == src_before
    assert (run_dir / NOTES_FILE).exists()  # advisory sidecar, not in the manifest


def test_note_endpoint_rejects_unknown_field_and_blocks_cross_site(client) -> None:
    wb, call = client
    run_id = _create(call)
    status, _ = call(
        "POST",
        f"/api/runs/{run_id}/research-note",
        {"target_kind": "strategy", "target_id": "s1", "text": "x", "status": "proved"},
    )
    assert status == 400
    status, body = call(
        "POST",
        f"/api/runs/{run_id}/research-note",
        {"target_kind": "strategy", "target_id": "s1", "text": "x"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert status == 403 and "cross-site" in body["error"]
