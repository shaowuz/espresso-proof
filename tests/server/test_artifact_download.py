"""Polish: per-panel artifact download.

A read-only export of a VALIDATED artifact — path-confined, allowlisted by
panel, and it changes no status. An unknown panel or an unproduced artifact is
an honest 404; an invalid run is refused.
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

from espresso_proof.server import Workbench, _make_handler

CLAIM = "Every group of order p^2, where p is prime, is abelian."


@pytest.fixture()
def client(tmp_path: Path):
    wb = Workbench(tmp_path / "root", provider="stub")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(wb))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            base + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

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
    raise AssertionError("create did not finish")


@pytest.mark.parametrize(
    "panel,artifact",
    [
        ("examples", "examples"),
        ("counterexamples", "counterexamples"),
        ("computations", "computation_checks"),
        ("literature", "literature_review"),
        ("strategies", "strategy_brief"),
    ],
)
def test_download_serves_the_validated_artifact(client, panel, artifact) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    grade_before = (run_dir / "idea_grade.yaml").read_bytes()
    status, headers, body = call("GET", f"/api/runs/{run_id}/artifact/{panel}")
    assert status == 200
    assert "attachment" in headers.get("Content-Disposition", "")
    assert f"{run_id}-{artifact}.yaml" in headers.get("Content-Disposition", "")
    # the bytes are exactly the on-disk validated artifact.
    assert body == (run_dir / f"{artifact}.yaml").read_bytes()
    # a download never changes status.
    assert (run_dir / "idea_grade.yaml").read_bytes() == grade_before


def test_unknown_panel_is_404(client) -> None:
    wb, call = client
    run_id = _create(call)
    status, _, _ = call("GET", f"/api/runs/{run_id}/artifact/secrets")
    assert status == 404


def test_download_rejects_bad_run_id(client) -> None:
    wb, call = client
    status, _, _ = call("GET", "/api/runs/%2e%2e%2fx/artifact/examples")
    assert status in (400, 404)


def test_download_refuses_invalid_run(client) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    (run_dir / "idea_grade.yaml").write_text("status: proved\n", encoding="utf-8")
    status, _, body = call("GET", f"/api/runs/{run_id}/artifact/examples")
    assert status == 409
    assert b"safety validation" in body
