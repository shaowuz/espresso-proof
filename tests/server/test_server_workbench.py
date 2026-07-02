"""Product Workbench V1 — local interactive server tests.

Every browser action must go through the existing kernel path and fail closed;
the server must confine run ids, isolate agent writes, and never let a click
upgrade mathematical status. The React cockpit is the only interactive UI;
the static dashboard file must keep working.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from espresso_proof.kernel.agent_run_status import init_agent_run_status
from espresso_proof.claim.claim_clear import evaluate_claim_clear, write_claim_clear_gate
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.agent_runner import skip_agent
from espresso_proof.orchestration.run_dashboard import render_run_dashboard
from espresso_proof.server import Workbench, WorkbenchError, _make_handler
from espresso_proof.kernel.validators import validate_run

CLAIM = "Every group of order p^2, where p is prime, is abelian."
UNCLEAR_CLAIM = "Prove the thing about the nice mirror object."


@pytest.fixture()
def client(tmp_path: Path):
    wb = Workbench(tmp_path / "root", provider="stub")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(wb))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"

    def call(method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            base + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def get_text(path: str) -> str:
        with urllib.request.urlopen(base + path) as resp:
            return resp.read().decode()

    yield wb, call, get_text
    httpd.shutdown()


def _await_job(call, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, jd = call("GET", f"/api/jobs/{job_id}")
        if jd.get("state") in ("done", "failed"):
            return jd
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def _create_text(call, text: str) -> str:
    # Create is async: POST returns a job_id immediately; poll for the run_id.
    status, d = call("POST", "/api/runs", {"text": text})
    assert status == 202, d
    job = _await_job(call, d["job_id"])
    assert job["state"] == "done", job
    return job["result"]["run_id"]


def _create(call) -> str:
    return _create_text(call, CLAIM)


def _mark_claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    manifest["current_state"] = "CLAIM_CLEAR"
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    write_claim_clear_gate(run_dir, evaluate_claim_clear(run_dir))
    init_agent_run_status(run_dir, done={"claim_understanding"})
    validate_run(run_dir)


# --------------------------------------------------------------------------- #
# path safety
# --------------------------------------------------------------------------- #


def test_resolve_run_rejects_traversal(tmp_path: Path) -> None:
    wb = Workbench(tmp_path / "root", provider="stub")
    for bad in ("..", "../etc", "a/b", "/etc/passwd", "....//", ""):
        with pytest.raises(WorkbenchError):
            wb.resolve_run(bad)


def test_status_endpoint_rejects_bad_run_id(client) -> None:
    _, call, _ = client
    status, d = call("GET", "/api/runs/..%2f..%2fetc/status")
    assert status in (400, 404)
    assert "error" in d


# --------------------------------------------------------------------------- #
# create + status
# --------------------------------------------------------------------------- #


def test_create_run_reaches_gate(client) -> None:
    _, call, _ = client
    run_id = _create(call)
    status, d = call("GET", f"/api/runs/{run_id}/status")
    assert status == 200
    assert d["state"]
    agents = {a["agent"]: a["status"] for a in d["agents"]}
    assert agents["claim_understanding"] == "done"
    assert agents["examples"] == "not_run"


def test_create_run_returns_immediately_with_slow_provider(client, monkeypatch) -> None:
    """POST /api/runs must NOT block on model latency. With a deliberately slow
    orchestrate (simulating a real external Claude/Codex provider), the request
    returns a job_id in well under a second while the run continues in the
    background, then completes with a run_id."""
    import espresso_proof.server as server_mod

    real = server_mod.orchestrate_run

    def slow_orchestrate(*args, **kwargs):
        time.sleep(2.0)  # simulate front-half + prover model latency
        return real(*args, **kwargs)

    monkeypatch.setattr(server_mod, "orchestrate_run", slow_orchestrate)

    _, call, _ = client
    t0 = time.time()
    status, d = call("POST", "/api/runs", {"text": CLAIM})
    elapsed = time.time() - t0
    assert status == 202, d
    assert "job_id" in d
    assert elapsed < 1.0, f"POST blocked for {elapsed:.2f}s (should be immediate)"

    # the job is still running while the slow provider works...
    _, j = call("GET", f"/api/jobs/{d['job_id']}")
    assert j["state"] in ("queued", "running")

    # ...and eventually finishes with a usable run_id.
    job = _await_job(call, d["job_id"], timeout=30.0)
    assert job["state"] == "done", job
    run_id = job["result"]["run_id"]
    _, st = call("GET", f"/api/runs/{run_id}/status")
    assert st["state"]


@pytest.mark.parametrize("provider", ["external", "openai"])
def test_cockpit_real_provider_uses_real_front_half_by_default(
    tmp_path: Path,
    monkeypatch,
    provider: str,
) -> None:
    import espresso_proof.server as server_mod

    calls: list[dict] = []

    def fake_orchestrate_run(**kwargs):
        calls.append(kwargs)
        run_dir = tmp_path / "root" / "projects" / kwargs["project"] / "runs" / "run_001"
        return {"run_dir": run_dir, "state": "CLAIM_CLEAR"}

    monkeypatch.setattr(server_mod, "orchestrate_run", fake_orchestrate_run)

    wb = Workbench(tmp_path / "root", provider=provider)
    result = wb._do_create_run(CLAIM, project="ui_real")

    assert result["project"] == "ui_real"
    assert calls[0]["provider"] == provider
    assert calls[0]["front_half"] == provider


def test_create_run_rejects_empty(client) -> None:
    _, call, _ = client
    status, d = call("POST", "/api/runs", {"text": "   "})
    assert status == 400


# --------------------------------------------------------------------------- #
# agent actions: isolation + fail closed
# --------------------------------------------------------------------------- #


def _sha(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest() if p.exists() else "MISSING"


def test_run_examples_writes_only_examples(client) -> None:
    wb, call, _ = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    _mark_claim_clear(run_dir)
    ce0 = _sha(run_dir / "counterexamples.yaml")
    lit0 = _sha(run_dir / "literature_review.yaml")
    status, d = call("POST", f"/api/runs/{run_id}/agents/examples")
    assert status == 202
    job = _await_job(call, d["job_id"])
    assert job["state"] == "done"
    _, st = call("GET", f"/api/runs/{run_id}/status")
    agents = {a["agent"]: a["status"] for a in st["agents"]}
    assert agents["examples"] == "done"
    assert agents["counterexamples"] == "not_run"
    assert agents["literature"] == "not_run"
    assert _sha(run_dir / "counterexamples.yaml") == ce0
    assert _sha(run_dir / "literature_review.yaml") == lit0
    validate_run(run_dir)


def test_unclear_claim_server_agent_launch_is_blocked(client) -> None:
    wb, call, _ = client
    run_id = _create_text(call, UNCLEAR_CLAIM)
    run_dir = wb.resolve_run(run_id)
    before = _sha(run_dir / "examples.yaml")

    status, d = call("POST", f"/api/runs/{run_id}/agents/examples")
    assert status == 202
    job = _await_job(call, d["job_id"])
    assert job["state"] == "failed"
    assert "claim-clear gate" in job.get("error_detail", "")
    assert _sha(run_dir / "examples.yaml") == before
    _, st = call("GET", f"/api/runs/{run_id}/status")
    agents = {a["agent"]: a for a in st["agents"]}
    assert agents["examples"]["status"] == "skipped"
    board = read_yaml(run_dir / "agent_run_status.yaml")
    board_agents = {a["agent"]: a for a in board["agents"]}
    assert board_agents["examples"]["safe_to_use_as_evidence"] is False
    validate_run(run_dir)


def test_run_counterexamples_writes_only_counterexamples(client) -> None:
    wb, call, _ = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    _mark_claim_clear(run_dir)
    ex0 = _sha(run_dir / "examples.yaml")
    lit0 = _sha(run_dir / "literature_review.yaml")
    _, d = call("POST", f"/api/runs/{run_id}/agents/counterexamples")
    assert _await_job(call, d["job_id"])["state"] == "done"
    _, st = call("GET", f"/api/runs/{run_id}/status")
    agents = {a["agent"]: a["status"] for a in st["agents"]}
    assert agents["counterexamples"] == "done"
    assert _sha(run_dir / "examples.yaml") == ex0
    assert _sha(run_dir / "literature_review.yaml") == lit0


def test_run_literature_grants_no_reference_support(client) -> None:
    wb, call, _ = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    _mark_claim_clear(run_dir)
    _, d = call("POST", f"/api/runs/{run_id}/agents/literature")
    assert _await_job(call, d["job_id"])["state"] == "done"
    sg = read_yaml(run_dir / "source_grounding.yaml")
    assert "source_backed" not in {s.get("status") for s in sg.get("steps", [])}


def test_failed_agent_records_failed_and_preserves_run(client, monkeypatch) -> None:
    wb, call, _ = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    _mark_claim_clear(run_dir)
    # run examples once successfully
    _, d = call("POST", f"/api/runs/{run_id}/agents/examples")
    assert _await_job(call, d["job_id"])["state"] == "done"
    good = _sha(run_dir / "examples.yaml")
    # force the next examples run to crash mid-way
    import espresso_proof.orchestration.agent_runner as ar

    def boom(run_dir, agent, *, client):  # noqa: ANN001
        from espresso_proof.kernel.io import write_yaml

        write_yaml(Path(run_dir) / "examples.yaml", {"claim_id": "X", "examples": []})
        raise RuntimeError("boom")

    monkeypatch.setattr(ar, "_run_agent_body", boom)
    _, d = call("POST", f"/api/runs/{run_id}/agents/examples")
    job = _await_job(call, d["job_id"])
    assert job["state"] == "failed"
    # previous valid artifact preserved; run still valid
    assert _sha(run_dir / "examples.yaml") == good
    validate_run(run_dir)


def test_unknown_agent_rejected(client) -> None:
    _, call, _ = client
    run_id = _create(call)
    status, d = call("POST", f"/api/runs/{run_id}/agents/bogus")
    assert status == 400


def test_skip_agent_contributes_no_evidence(client) -> None:
    # Skipping is a CLI/orchestration capability (run-agent --skip); the old
    # server endpoint died with the legacy UI. The status view must still show
    # a skipped agent honestly.
    wb, call, _ = client
    run_id = _create(call)
    skip_agent(wb.resolve_run(run_id), "counterexamples")
    _, st = call("GET", f"/api/runs/{run_id}/status")
    agents = {a["agent"]: a["status"] for a in st["agents"]}
    assert agents["counterexamples"] == "skipped"


def test_continue_rejects_unknown_route(client) -> None:
    _, call, _ = client
    run_id = _create(call)
    status, d = call("POST", f"/api/runs/{run_id}/continue", {"strategy": "no_such_route", "effort": "quick_test"})
    assert status == 400
    assert "route" in d["error"].lower()


# --------------------------------------------------------------------------- #
# UI: native buttons, no command blocks on main path, static still works
# --------------------------------------------------------------------------- #


def test_legacy_run_page_is_gone(client) -> None:
    # The legacy interactive per-run page died with the legacy UI: /run/<id>
    # is an unknown path now (the React cockpit is the only interactive UI).
    _, call, _ = client
    run_id = _create(call)
    status, _ = call("GET", f"/run/{run_id}")
    assert status == 404


def test_static_dashboard_still_renders(tmp_path: Path) -> None:
    """Existing static dashboard generation must still work (server unavailable)."""
    from espresso_proof.orchestration.orchestrator import orchestrate_run

    claim = tmp_path / "c.md"
    claim.write_text(CLAIM, encoding="utf-8")
    rd = orchestrate_run(
        project="static", input_path=claim, output_root=tmp_path,
        provider="stub", front_half="stub", fast=True,
    )["run_dir"]
    html = render_run_dashboard(rd)  # non-interactive
    assert "Espresso Proof" in html
    assert "Choose Route" in html
    # static page keeps its copy-command affordance
    assert "Copy command" in html
    assert 'data-action="run-agent"' not in html


def test_claim_card_has_no_foreign_domain_hypotheses(tmp_path: Path) -> None:
    """A group-theory claim must not render algebraic-geometry hypothesis chips
    (smooth / projective / base field C) leaked from mock defaults."""
    from espresso_proof.orchestration.orchestrator import orchestrate_run

    claim = tmp_path / "c.md"
    claim.write_text(CLAIM, encoding="utf-8")
    rd = orchestrate_run(
        project="chips", input_path=claim, output_root=tmp_path,
        provider="stub", front_half="stub", fast=True,
    )["run_dir"]
    locked = read_yaml(rd / "locked_claim.yaml")
    names = " ".join(str(h.get("name", "")) for h in locked.get("hypotheses", [])).lower()
    assert "base field c" not in names
    assert "projective" not in names
    html = render_run_dashboard(rd)
    assert "base field C" not in html
    assert "Unclear conclusion" not in html or "concl" in html  # conclusion chip allowed, AG terms not


# --- Goal 65C-2: lost-job / stalled detection (mid-call process death) -------

def test_status_reports_stalled_after_mid_call_death(client) -> None:
    """A run with an agent call that started but never finished, and no live job
    progressing it, is reported as stalled (the order-3 dead-page failure) — a
    visible last-active-stage signal instead of a silent dead page."""
    from espresso_proof.kernel import observability as obs

    wb, call, _ = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    # healthy completed run: nothing dangling
    assert wb.status(run_id).get("stalled") is None
    # simulate the worker dying INSIDE an external call: a started agent_event
    # with no terminal record (exactly 65B's signal).
    obs.append_agent_event(
        run_dir, agent_name="llm_prover_reviewers", provider="external",
        adapter="external", stage="llm_prover_reviewers", agent_status="started",
    )
    st = wb.status(run_id)
    assert st["stalled"] is not None
    assert st["stalled"]["stage"] == "llm_prover_reviewers"
    assert "no proof" in st["stalled"]["message"].lower()
    # diagnostic only: no mathematical-status field appears on the stalled signal
    assert not any(
        k in st["stalled"] for k in ("proved", "checked", "source_backed", "refuted", "grade")
    )
    validate_run(run_dir)  # the run is still valid; stalled is display-only


def test_status_not_stalled_while_job_is_live(client) -> None:
    """A dangling started call with a LIVE in-memory job is in-flight, not lost."""
    from espresso_proof.kernel import observability as obs

    wb, call, _ = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    obs.append_agent_event(
        run_dir, agent_name="llm_prover_reviewers", provider="external",
        adapter="external", stage="llm_prover_reviewers", agent_status="started",
    )
    with wb._jobs_lock:
        wb._jobs["job-live"] = {"run_id": run_id, "state": "running"}
    assert wb.status(run_id).get("stalled") is None


def test_status_endpoint_exposes_stalled(client) -> None:
    """The HTTP /status endpoint carries the stalled signal for the UI."""
    from espresso_proof.kernel import observability as obs

    wb, call, _ = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    obs.append_agent_event(
        run_dir, agent_name="llm_prover_reviewers", provider="external",
        adapter="external", stage="llm_prover_reviewers", agent_status="started",
    )
    status, st = call("GET", f"/api/runs/{run_id}/status")
    assert status == 200
    assert st["stalled"] and st["stalled"]["stage"] == "llm_prover_reviewers"


# --- Goal 65F: browser/server troubleshooting gates --------------------------

def test_g65f_stalled_signal_exposes_failure_state_fields(client) -> None:
    """The UI must be able to show last active stage, elapsed time, and the last
    successful step (not just a dead 'Could not reach the server' page)."""
    from espresso_proof.kernel import observability as obs

    wb, call, _ = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    obs.append_agent_event(
        run_dir, agent_name="llm_prover_reviewers", provider="external",
        adapter="external", stage="llm_prover_reviewers", agent_status="started",
        started_at=obs.now_iso(),  # the real orchestrator start-event always sets this
    )
    _, st = call("GET", f"/api/runs/{run_id}/status")
    stalled = st["stalled"]
    assert stalled is not None
    assert stalled["last_active_stage"] == "llm_prover_reviewers"
    assert isinstance(stalled["elapsed_seconds"], int) and stalled["elapsed_seconds"] >= 0
    # the front half completed, so there is a last successful step to show
    assert stalled["last_successful_stage"]
    assert "events.jsonl" in stalled["diagnostics"]
    assert stalled["message"]


def test_g65f_troubleshooting_state_is_status_neutral(client) -> None:
    """No diagnostic/troubleshooting field may expose an earned math status for a
    stalled/failed run."""
    import json as _json

    from espresso_proof.kernel import observability as obs

    wb, call, _ = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    obs.append_agent_event(
        run_dir, agent_name="llm_prover_reviewers", provider="external",
        adapter="external", stage="llm_prover_reviewers", agent_status="started",
    )
    _, st = call("GET", f"/api/runs/{run_id}/status")
    blob = _json.dumps(st["stalled"]).lower()
    for forbidden in ("proved", "source_backed", "source-backed", '"checked"', "refuted"):
        assert forbidden not in blob


def test_g65f_failed_agent_keeps_server_alive(client) -> None:
    """A failed agent job must surface as a job failure with the server still
    answering (not a dead page)."""
    wb, call, _ = client
    run_id = _create_text(call, UNCLEAR_CLAIM)
    status, d = call("POST", f"/api/runs/{run_id}/agents/examples")
    assert status == 202
    job = _await_job(call, d["job_id"])
    assert job["state"] == "failed"  # the job failed, not the server
    # the server still answers run status afterwards (it stayed alive)
    code, st = call("GET", f"/api/runs/{run_id}/status")
    assert code == 200 and "agents" in st


# --- Goal 66: simple prime-order group claim is not blocked via the API ------

def test_g66_prime_order_group_claim_clears_via_api(client) -> None:
    from espresso_proof.claim.claim_clear import evaluate_claim_clear

    wb, call, _ = client
    run_id = _create_text(call, "prove that a group of order p is cyclic when p is a prime number")
    run_dir = wb.resolve_run(run_id)
    ledger = read_yaml(run_dir / "hypothesis_ledger.yaml")
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert ledger["claim_precision"] == "precise"
    assert evaluate_claim_clear(run_dir).clear is True
    assert manifest["current_state"] != "WAITING_CLAIM_CLARIFICATION"
    # claim precision only — no earned math status
    assert read_yaml(run_dir / "locked_claim.yaml")["human_confirmed"] is False
