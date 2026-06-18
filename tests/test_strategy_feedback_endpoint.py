"""Goal 43C: the strategy-feedback endpoint is advisory, CSRF-guarded, and
fail-closed.

A score is not proof, source, refutation, or human confirmation: it is written
to a sidecar the kernel never reads as evidence, the grade/source/proof status
is byte-identical before and after, a cross-site request is blocked, an unknown
strategy or a status-smuggling payload is rejected, and the prior valid run is
never corrupted. The next continue receives the feedback only as advisory
ranking context, and the latest-round ledger is explanatory only.
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

from espresso_proof.kernel.io import read_yaml
from espresso_proof.server import Workbench, _make_handler

CLAIM = "Every group of order p^2, where p is prime, is abelian."


@pytest.fixture()
def client(tmp_path: Path):
    wb = Workbench(tmp_path / "root", provider="stub")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(wb))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def call(method: str, path: str, body=None, headers=None):
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

    yield wb, call, port
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


def _first_strategy(call, run_id: str) -> str:
    _, panel = call("GET", f"/api/runs/{run_id}/panel/strategies")
    return panel["strategies"][0]["id"]


def _digest(run_dir: Path, name: str) -> bytes:
    return (run_dir / name).read_bytes()


# --------------------------------------------------------------------------- #
# happy path + advisory display
# --------------------------------------------------------------------------- #


def test_feedback_records_and_reads_back_advisory(client) -> None:
    wb, call, _ = client
    run_id = _create(call)
    sid = _first_strategy(call, run_id)
    status, body = call(
        "POST",
        f"/api/runs/{run_id}/strategy-feedback",
        {"strategy_id": sid, "score": 82, "priority": "focus", "note": "center argument"},
    )
    assert status == 200 and body["ok"] is True
    assert body["feedback"]["score"] == 82 and body["feedback"]["advisory_only"] is True
    # the strategies panel now carries my_feedback for that route (display only).
    _, panel = call("GET", f"/api/runs/{run_id}/panel/strategies")
    mine = next(s for s in panel["strategies"] if s["id"] == sid)["my_feedback"]
    assert mine["score"] == 82 and mine["priority"] == "focus"
    assert "ledger_context" in panel  # the two ledgers' shared context


def test_feedback_does_not_upgrade_any_status(client) -> None:
    wb, call, _ = client
    run_id = _create(call)
    sid = _first_strategy(call, run_id)
    run_dir = wb.resolve_run(run_id)
    before = {n: _digest(run_dir, n) for n in ("idea_grade.yaml", "source_grounding.yaml")}
    call(
        "POST",
        f"/api/runs/{run_id}/strategy-feedback",
        {"strategy_id": sid, "score": 100, "priority": "focus"},
    )
    after = {n: _digest(run_dir, n) for n in ("idea_grade.yaml", "source_grounding.yaml")}
    # a maximal score changes NOTHING about the earned status artifacts.
    assert before == after
    # the strategy's source tone is still not earned-green.
    _, panel = call("GET", f"/api/runs/{run_id}/panel/strategies")
    assert next(s for s in panel["strategies"] if s["id"] == sid)["source_tone"] != "earned"


# --------------------------------------------------------------------------- #
# CSRF guard
# --------------------------------------------------------------------------- #


def test_cross_site_origin_is_blocked(client) -> None:
    wb, call, _ = client
    run_id = _create(call)
    sid = _first_strategy(call, run_id)
    status, body = call(
        "POST",
        f"/api/runs/{run_id}/strategy-feedback",
        {"strategy_id": sid, "score": 50},
        headers={"Origin": "http://evil.example.com"},
    )
    assert status == 403 and "cross-site" in body["error"]


def test_non_loopback_host_is_blocked(client) -> None:
    wb, call, _ = client
    run_id = _create(call)
    sid = _first_strategy(call, run_id)
    status, body = call(
        "POST",
        f"/api/runs/{run_id}/strategy-feedback",
        {"strategy_id": sid, "score": 50},
        headers={"Host": "evil.example.com"},
    )
    assert status == 403 and "host" in body["error"].lower()


def test_loopback_origin_is_allowed(client) -> None:
    wb, call, port = client
    run_id = _create(call)
    sid = _first_strategy(call, run_id)
    status, _ = call(
        "POST",
        f"/api/runs/{run_id}/strategy-feedback",
        {"strategy_id": sid, "score": 40},
        headers={"Origin": f"http://127.0.0.1:{port}"},
    )
    assert status == 200


# --------------------------------------------------------------------------- #
# validation + fail-closed
# --------------------------------------------------------------------------- #


def test_unknown_strategy_is_rejected(client) -> None:
    wb, call, _ = client
    run_id = _create(call)
    status, body = call(
        "POST",
        f"/api/runs/{run_id}/strategy-feedback",
        {"strategy_id": "no_such_route", "score": 50},
    )
    assert status == 400 and "unknown route" in body["error"]


@pytest.mark.parametrize(
    "payload",
    [
        {"score": 999},  # out of bounds
        {"score": 50, "priority": "promote_to_proved"},  # bad enum
        {"score": 50, "status": "proved"},  # smuggled status field
        {"score": 50, "source_backed": True},  # smuggled evidence field
    ],
)
def test_invalid_payload_rejected_and_run_preserved(client, payload) -> None:
    wb, call, _ = client
    run_id = _create(call)
    sid = _first_strategy(call, run_id)
    run_dir = wb.resolve_run(run_id)
    before = _digest(run_dir, "idea_grade.yaml")
    status, body = call(
        "POST",
        f"/api/runs/{run_id}/strategy-feedback",
        {"strategy_id": sid, **payload},
    )
    assert status == 400 and "invalid feedback" in body["error"]
    # the run is untouched and the sidecar was not partially written.
    assert _digest(run_dir, "idea_grade.yaml") == before
    from espresso_proof.strategy.strategy_feedback import load_feedback

    assert load_feedback(run_dir).items == []


def test_feedback_rejects_bad_run_id(client) -> None:
    wb, call, _ = client
    status, _ = call("POST", "/api/runs/%2e%2e%2fx/strategy-feedback", {"strategy_id": "s", "score": 1})
    assert status in (400, 403, 404)


# --------------------------------------------------------------------------- #
# next continue receives feedback; latest-round ledger
# --------------------------------------------------------------------------- #


def test_continue_receives_feedback_and_writes_round_ledger(client) -> None:
    wb, call, _ = client
    run_id = _create(call)
    sid = _first_strategy(call, run_id)
    call(
        "POST",
        f"/api/runs/{run_id}/strategy-feedback",
        {"strategy_id": sid, "score": 90, "priority": "focus", "note": "press here"},
    )
    status, d = call("POST", f"/api/runs/{run_id}/continue", {"strategy": sid, "effort": "quick_test"})
    assert status == 202
    deadline = time.time() + 40
    while time.time() < deadline:
        _, jd = call("GET", f"/api/jobs/{d['job_id']}")
        if jd.get("state") in ("done", "failed"):
            assert jd["state"] == "done", jd
            break
        time.sleep(0.3)
    run_dir = wb.resolve_run(run_id)
    # the continuation received the advisory feedback summary (human_choice).
    hc = read_yaml(run_dir / "human_choice.yaml")
    assert "ADVISORY" in hc["taste_judgment"]
    assert "90/100" in hc["taste_judgment"] and "press here" in hc["taste_judgment"]
    # the prover round context exposes it as advisory (never evidence).
    from espresso_proof.agents.llm_agents import _human_feedback_advisory

    assert "ADVISORY" in _human_feedback_advisory(run_dir)
    # the latest-round ledger is present, explanatory, advisory-only.
    _, snap = call("GET", f"/api/runs/{run_id}/snapshot")
    lr = snap["latest_round"]
    assert lr is not None and lr["advisory_only"] is True
    assert lr["selected_strategy"] == sid and lr["stop_reason"]
    assert isinstance(lr["graph_changed"], bool)


def test_continue_coerces_disallowed_effort_and_run_stays_valid(client) -> None:
    # the lock-claim style route allows only a subset of efforts; a one-click
    # continue that sends a disallowed effort must be coerced, never writing a
    # human_choice the kernel rejects (a continue must not corrupt the run).
    wb, call, _ = client
    run_id = _create(call)
    _, panel = call("GET", f"/api/runs/{run_id}/panel/strategies")
    # find a strategy whose allowed efforts do NOT include normal_pass.
    restricted = next(
        (s for s in panel["strategies"] if "normal_pass" not in s["allowed_efforts"]),
        None,
    )
    if restricted is None:
        pytest.skip("no effort-restricted strategy in this run")
    status, d = call(
        "POST",
        f"/api/runs/{run_id}/continue",
        {"strategy": restricted["id"], "effort": "normal_pass"},
    )
    assert status == 202
    deadline = time.time() + 40
    while time.time() < deadline:
        _, jd = call("GET", f"/api/jobs/{d['job_id']}")
        if jd.get("state") in ("done", "failed"):
            assert jd["state"] == "done", jd
            break
        time.sleep(0.3)
    # the run still validates (the snapshot would 409 otherwise).
    status, _ = call("GET", f"/api/runs/{run_id}/snapshot")
    assert status == 200


def test_opaque_null_origin_is_blocked(client) -> None:
    # a sandboxed iframe / data: URL sends Origin: null; a mutating call must
    # not be honored from an opaque origin even though Host is loopback.
    wb, call, _ = client
    run_id = _create(call)
    sid = _first_strategy(call, run_id)
    status, body = call(
        "POST",
        f"/api/runs/{run_id}/strategy-feedback",
        {"strategy_id": sid, "score": 50},
        headers={"Origin": "null"},
    )
    assert status == 403 and "cross-site" in body["error"]


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1.evil.com",      # suffix trick
        "http://localhost.evil.com",        # suffix trick
        "http://127.0.0.1:9/@evil.com",    # userinfo @ trick
        "http://0.0.0.0",                   # not loopback
        "https://evil.com",
    ],
)
def test_loopback_lookalike_origins_blocked(client, origin) -> None:
    wb, call, _ = client
    run_id = _create(call)
    sid = _first_strategy(call, run_id)
    status, body = call(
        "POST",
        f"/api/runs/{run_id}/strategy-feedback",
        {"strategy_id": sid, "score": 50},
        headers={"Origin": origin},
    )
    assert status == 403, (origin, status, body)


def test_concurrent_feedback_posts_preserve_all_items(client) -> None:
    # the append is a load->append->write cycle; under the per-run lock,
    # concurrent POSTs must not lose an item.
    import concurrent.futures as cf

    wb, call, _ = client
    run_id = _create(call)
    sid = _first_strategy(call, run_id)

    def post(i: int):
        return call(
            "POST",
            f"/api/runs/{run_id}/strategy-feedback",
            {"strategy_id": sid, "score": i, "note": f"n{i}"},
        )[0]

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        statuses = list(ex.map(post, range(12)))
    assert all(s == 200 for s in statuses)
    from espresso_proof.strategy.strategy_feedback import load_feedback

    run_dir = wb.resolve_run(run_id)
    assert len(load_feedback(run_dir).items) == 12  # none dropped
