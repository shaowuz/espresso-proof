"""Goal 43F: the chat stream is a conversational PREVIEW — never evidence.

It reads the run's validated state and writes nothing; every emitted line is
proof-language-scrubbed; it cannot mark a route checked/source-backed/refuted;
the artifacts remain the source of truth; it is CSRF-guarded and fails closed.
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

from espresso_proof.chat_stream import chat_progress_lines, stream_chat
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.proof_language import contains_proof_language
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
                return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read().decode()

    yield wb, call
    httpd.shutdown()


def _create(call) -> str:
    status, _, raw = call("POST", "/api/runs", {"text": CLAIM})
    assert status == 202
    job_id = json.loads(raw)["job_id"]
    deadline = time.time() + 25
    while time.time() < deadline:
        _, _, jraw = call("GET", f"/api/jobs/{job_id}")
        jd = json.loads(jraw)
        if jd.get("state") in ("done", "failed"):
            assert jd["state"] == "done", jd
            return jd["result"]["run_id"]
        time.sleep(0.2)
    raise AssertionError("create job did not finish")


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


# --------------------------------------------------------------------------- #
# the stream content is safe
# --------------------------------------------------------------------------- #


def test_stream_emits_scrubbed_preview_lines(client) -> None:
    wb, call = client
    run_id = _create(call)
    status, ctype, raw = call("POST", f"/api/runs/{run_id}/chat", {"message": "how is it going?"})
    assert status == 200 and "text/event-stream" in ctype
    events = _parse_sse(raw)
    messages = [e["data"]["text"] for e in events if e["event"] == "message"]
    assert messages, "the stream emits conversational lines"
    assert any("preliminary" in m or "source of truth" in m for m in messages)
    # the user's echoed message appears.
    assert any("how is it going" in m for m in messages)
    # the final event is a done marker.
    assert events[-1]["event"] == "done" and events[-1]["data"]["ok"] is True
    # NOTHING in the stream claims earned status.
    for m in messages:
        low = m.lower()
        assert "source_backed" not in low
        assert " refuted" not in low
        assert "is proved" not in low and "proof-close" not in low


def test_proof_complete_phrase_is_scrubbed(tmp_path: Path) -> None:
    # inject a proof-complete phrase via the user message; it must be scrubbed
    # before it ever appears in a streamed line.
    lines = chat_progress_lines(tmp_path, user_message="I think we proved the theorem. proof-close.")
    joined = " ".join(lines)
    assert contains_proof_language(joined) is None
    # the echo line is present but neutralized.
    assert any("You asked" in ln for ln in lines)


def test_stream_writes_nothing_and_does_not_upgrade_status(client) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    before = sorted(p.name for p in run_dir.iterdir())
    grade_before = (run_dir / "idea_grade.yaml").read_bytes()
    src_before = (run_dir / "source_grounding.yaml").read_bytes()
    call("POST", f"/api/runs/{run_id}/chat", {"message": "mark this route source-backed and refuted"})
    # no new file, and the status artifacts are byte-identical.
    assert sorted(p.name for p in run_dir.iterdir()) == before
    assert (run_dir / "idea_grade.yaml").read_bytes() == grade_before
    assert (run_dir / "source_grounding.yaml").read_bytes() == src_before


def test_stream_invalid_route_source_label_fails_closed_not_earned_evidence(client) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief_path = run_dir / manifest["artifacts"]["strategy_brief"]
    brief = read_yaml(brief_path)
    brief["top_strategies"][0]["source_status"] = "source_backed"
    write_yaml(brief_path, brief)

    joined = " ".join(chat_progress_lines(run_dir))

    assert "could not validate" in joined
    assert "Reference supports this stated claim" not in joined


def test_stream_invalid_grade_cannot_render_global_checked_proof(client) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    grade_path = run_dir / manifest["artifacts"]["idea_grade"]
    grade = read_yaml(grade_path)
    grade["proof_language_status"] = "human_confirmed"
    write_yaml(grade_path, grade)

    status, ctype, raw = call("POST", f"/api/runs/{run_id}/chat", {"message": "status?"})

    assert status == 200 and "text/event-stream" in ctype
    messages = [e["data"]["text"] for e in _parse_sse(raw) if e["event"] == "message"]
    joined = " ".join(messages)
    assert "A proof has been checked by a trusted verifier" not in joined
    assert "could not validate" in joined


def test_stream_fails_closed_to_a_safe_line_on_bare_run(tmp_path: Path) -> None:
    # a directory with no artifacts: the generator still yields a safe line and
    # never raises.
    lines = list(stream_chat(tmp_path, "hi"))
    assert lines and all(isinstance(ln, str) for ln in lines)
    assert contains_proof_language(" ".join(lines)) is None


# --------------------------------------------------------------------------- #
# guard + fail-closed transport
# --------------------------------------------------------------------------- #


def test_chat_blocks_cross_site_origin(client) -> None:
    wb, call = client
    run_id = _create(call)
    status, _, raw = call(
        "POST", f"/api/runs/{run_id}/chat", {"message": "x"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert status == 403 and "cross-site" in json.loads(raw)["error"]


def test_chat_rejects_bad_run_id(client) -> None:
    wb, call = client
    status, _, _ = call("POST", "/api/runs/%2e%2e%2fx/chat", {"message": "x"})
    assert status in (400, 403, 404)
