"""Wire the evidence fan-out behind a default-on flag + observability,
and the full concurrency regression that proves every invariant holds.

Covers the RE-SCOPED subgoal-06 (the coordinator is exposed via an explicit CLI
`run-agents` batch command + a server batch endpoint, NOT auto-wired into
orchestrate, so the fast-core "defer evidence / stay PRELIMINARY" contract holds).
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.claim.claim_clear import ClaimClearBlockedError
from espresso_proof.cli import app
from espresso_proof.kernel import agent_run_status as ars
from espresso_proof.kernel.io import read_yaml
from espresso_proof.kernel.observability import read_agent_events
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.agent_runner import ON_DEMAND_AGENTS
from espresso_proof.orchestration import evidence_coordinator as ec
from espresso_proof.orchestration.evidence_coordinator import (
    evidence_parallel_enabled,
    run_evidence_batch_auto,
)
from espresso_proof.orchestration.orchestrator import orchestrate_run
from espresso_proof.server import Workbench, _make_handler

from tests.support_jobs import JOB_DEADLINE_SECONDS

CLAIM = "Every group of order p^2, where p is prime, is abelian."
UNCLEAR_CLAIM = "Prove the thing about the nice mirror object."

_EVIDENCE_FILES = [
    "examples.yaml",
    "computation_checks.yaml",
    "counterexamples.yaml",
    "tests.yaml",
    "literature_review.yaml",
]


def _fast_run(tmp_path: Path, claim: str = CLAIM, name: str = "p2") -> Path:
    claim_path = tmp_path / f"{name}.md"
    claim_path.write_text(claim, encoding="utf-8")
    result = orchestrate_run(
        project=name,
        input_path=claim_path,
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    return Path(result["run_dir"])


def _state(run_dir: Path) -> str:
    return read_yaml(run_dir / "run_manifest.yaml")["current_state"]


# --------------------------------------------------------------------------- #
# default-on flag + serial fallback
# --------------------------------------------------------------------------- #


def test_flag_default_on_and_respects_env(monkeypatch) -> None:
    monkeypatch.delenv(ec.PARALLEL_ENV, raising=False)
    assert evidence_parallel_enabled() is True  # default on
    for off in ("false", "0", "no", "off", "FALSE"):
        monkeypatch.setenv(ec.PARALLEL_ENV, off)
        assert evidence_parallel_enabled() is False
    for on in ("true", "1", "yes", ""):
        monkeypatch.setenv(ec.PARALLEL_ENV, on)
        assert evidence_parallel_enabled() is True


def test_auto_serial_and_parallel_paths_converge(tmp_path: Path, monkeypatch) -> None:
    base = _fast_run(tmp_path)
    proj = base.parents[1]
    shutil.copytree(proj, tmp_path / "ON" / proj.name)
    shutil.copytree(proj, tmp_path / "OFF" / proj.name)
    on_dir = tmp_path / "ON" / proj.name / "runs" / base.name
    off_dir = tmp_path / "OFF" / proj.name / "runs" / base.name

    monkeypatch.setenv(ec.PARALLEL_ENV, "true")
    run_evidence_batch_auto(on_dir)
    monkeypatch.setenv(ec.PARALLEL_ENV, "false")
    run_evidence_batch_auto(off_dir)

    for name in _EVIDENCE_FILES:
        assert (on_dir / name).read_bytes() == (off_dir / name).read_bytes(), name
    validate_run(on_dir)
    validate_run(off_dir)


# --------------------------------------------------------------------------- #
# re-scoped contract: fast core stays PRELIMINARY; batch is opt-in
# --------------------------------------------------------------------------- #


def test_fast_core_defers_until_batch(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    # fast core left every evidence lane not_run (PRELIMINARY)
    for agent in ON_DEMAND_AGENTS:
        assert ars.agent_status(run_dir, agent) == "not_run"
    # the opt-in batch enriches them
    run_evidence_batch_auto(run_dir)
    for agent in ON_DEMAND_AGENTS:
        assert ars.agent_status(run_dir, agent) == "done"


def test_batch_does_not_change_current_state(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    before = _state(run_dir)
    run_evidence_batch_auto(run_dir)
    assert _state(run_dir) == before  # lanes never touch the lifecycle scalar


# --------------------------------------------------------------------------- #
# trust boundary: claim-clear gate intact under the batch
# --------------------------------------------------------------------------- #


def test_claim_clear_gate_blocks_batch(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path, claim=UNCLEAR_CLAIM, name="vague")
    # the claim-clear-gated lanes must stay blocked when the claim is unclear
    with pytest.raises(ClaimClearBlockedError):
        run_evidence_batch_auto(run_dir, ["examples"])
    assert not ars.evidence_ready(run_dir, "examples")


# --------------------------------------------------------------------------- #
# observability: per-lane compute + commit timing (snapshot-excluded)
# --------------------------------------------------------------------------- #


def test_observability_records_per_lane_compute_and_commit(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    run_evidence_batch_auto(run_dir)

    events = read_agent_events(run_dir)
    commit_lanes = {
        e["agent_name"] for e in events if e.get("stage") == "evidence_commit"
    }
    compute_lanes = {
        e["agent_name"] for e in events if e.get("stage") == "evidence_compute"
    }
    # every lane is committed; the three precompute lanes also have a compute event
    assert set(ON_DEMAND_AGENTS) <= commit_lanes
    assert {"examples", "counterexamples", "literature"} <= compute_lanes
    # durations are recorded
    for e in events:
        if e.get("stage") in ("evidence_compute", "evidence_commit"):
            assert isinstance(e.get("duration_ms"), int)

    # timings.yaml aggregates per-lane durations and survives (snapshot-excluded)
    timings = read_yaml(run_dir / "timings.yaml")
    durations = timings.get("agent_call_durations_ms", {})
    for agent in ON_DEMAND_AGENTS:
        assert agent in durations
    validate_run(run_dir)


# --------------------------------------------------------------------------- #
# resume / idempotent re-entry
# --------------------------------------------------------------------------- #


def test_idempotent_reentry_converges(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    run_evidence_batch_auto(run_dir)
    snapshot = {n: (run_dir / n).read_bytes() for n in _EVIDENCE_FILES}
    # re-running the batch (e.g. after a crash/resume) converges to the same run
    run_evidence_batch_auto(run_dir)
    for name in _EVIDENCE_FILES:
        assert (run_dir / name).read_bytes() == snapshot[name], f"{name} drifted on re-entry"
    for agent in ON_DEMAND_AGENTS:
        assert ars.agent_status(run_dir, agent) == "done"
    validate_run(run_dir)


# --------------------------------------------------------------------------- #
# CLI entrypoint
# --------------------------------------------------------------------------- #


def test_cli_run_agents_enriches(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    result = CliRunner().invoke(
        app, ["run-agents", "--run", str(run_dir), "--provider", "stub"]
    )
    assert result.exit_code == 0, result.output
    assert "Batch" in result.output
    for agent in ON_DEMAND_AGENTS:
        assert ars.agent_status(run_dir, agent) == "done"
    validate_run(run_dir)


def test_cli_run_agents_passes_max_workers(tmp_path: Path, monkeypatch) -> None:
    # Guard against the --max-workers knob being silently ignored: the CLI must
    # forward it to the parallel coordinator.
    run_dir = _fast_run(tmp_path)
    seen: dict = {}

    def spy(run_dir_arg, agents_arg=None, **kwargs):
        seen["max_workers"] = kwargs.get("max_workers")
        return {"agents": {a: {"status": "done"} for a in ON_DEMAND_AGENTS}}

    monkeypatch.setattr(ec, "run_evidence_batch_auto", spy)
    result = CliRunner().invoke(
        app, ["run-agents", "--run", str(run_dir), "--max-workers", "7"]
    )
    assert result.exit_code == 0, result.output
    assert seen["max_workers"] == 7


def test_cli_run_agents_claim_clear_blocks(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path, claim=UNCLEAR_CLAIM, name="vague")
    # CLI pre-validates claim-clear before running the gated lanes
    result = CliRunner().invoke(
        app, ["run-agents", "--run", str(run_dir), "--agents", "examples"]
    )
    assert result.exit_code != 0
    assert not ars.evidence_ready(run_dir, "examples")


def test_cli_run_agents_subset_and_serial(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    result = CliRunner().invoke(
        app,
        ["run-agents", "--run", str(run_dir), "--agents", "literature,failure-patterns", "--serial"],
    )
    assert result.exit_code == 0, result.output
    assert ars.agent_status(run_dir, "literature") == "done"
    assert ars.agent_status(run_dir, "failure_patterns") == "done"
    # the unrequested lanes stay deferred
    assert ars.agent_status(run_dir, "examples") == "not_run"


# --------------------------------------------------------------------------- #
# server batch endpoint
# --------------------------------------------------------------------------- #


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

    yield wb, call
    httpd.shutdown()


def _await_job(call, job_id: str, timeout: float = JOB_DEADLINE_SECONDS) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, jd = call("GET", f"/api/jobs/{job_id}")
        if jd.get("state") in ("done", "failed"):
            return jd
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def _create_run(call) -> str:
    status, d = call("POST", "/api/runs", {"text": CLAIM})
    assert status == 202, d
    job = _await_job(call, d["job_id"])
    assert job["state"] == "done", job
    return job["result"]["run_id"]


def test_server_agents_batch_endpoint_enriches(client) -> None:
    wb, call = client
    run_id = _create_run(call)
    # precondition: evidence deferred
    _, st = call("GET", f"/api/runs/{run_id}/status")
    assert {a["agent"]: a["status"] for a in st["agents"]}["examples"] == "not_run"

    status, d = call("POST", f"/api/runs/{run_id}/agents-batch", {})
    assert status == 202, d
    assert set(d["agents"]) == set(ON_DEMAND_AGENTS)
    job = _await_job(call, d["job_id"])
    assert job["state"] == "done", job

    _, st2 = call("GET", f"/api/runs/{run_id}/status")
    by_agent = {a["agent"]: a["status"] for a in st2["agents"]}
    for agent in ON_DEMAND_AGENTS:
        assert by_agent[agent] == "done"


def test_server_agents_batch_rejects_unknown_agent(client) -> None:
    wb, call = client
    run_id = _create_run(call)
    status, d = call("POST", f"/api/runs/{run_id}/agents-batch", {"agents": ["not_a_lane"]})
    assert status == 400
    assert "error" in d


def test_server_agents_batch_claim_clear_blocks(client) -> None:
    # An unclear claim must not let the gated lanes enrich via the batch endpoint:
    # the claim-clear gate fires inside run_single_agent (fail-closed), so the JOB
    # fails (the server mirrors start_agent, which gates at commit time).
    wb, call = client
    status, d = call("POST", "/api/runs", {"text": UNCLEAR_CLAIM})
    assert status == 202, d
    create_job = _await_job(call, d["job_id"])
    run_id = create_job["result"]["run_id"]

    status, d = call("POST", f"/api/runs/{run_id}/agents-batch", {"agents": ["examples"]})
    assert status == 202, d
    job = _await_job(call, d["job_id"])
    assert job["state"] == "failed", job

    _, st = call("GET", f"/api/runs/{run_id}/status")
    by_agent = {a["agent"]: a["status"] for a in st["agents"]}
    assert by_agent["examples"] != "done"
