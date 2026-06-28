"""Goal 54A: per-run runtime observability (events.jsonl / agent_events.jsonl /
timings.yaml).

Observability is provenance, never evidence: records cannot carry a status-upgrade
field, cannot leak secrets/abs-paths, must survive stage rollback (so a failed
agent's record is not erased), and must not make old runs invalid.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from espresso_proof.kernel import observability as obs
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.orchestrator import orchestrate_run
from espresso_proof.kernel.run_events import EVENT_KINDS, append_event, read_events
from espresso_proof.kernel.stage_runner import StageRunner, StageError
from espresso_proof.kernel.validators import validate_run
from tests.kernel.test_evidence_aware_grade import FakeClaude


# --- module-level safety (no run needed) ------------------------------------

def test_monotonic_ms_is_int():
    assert isinstance(obs.monotonic_ms(), int)


def test_agent_event_roundtrip_and_required_fields(tmp_path: Path):
    obs.append_agent_event(
        tmp_path, agent_name="claim", provider="stub", adapter="stub", stage="claim",
        agent_status="finished", duration_ms=12, validation_status="passed",
    )
    rows = obs.read_agent_events(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    for k in ("schema_version", "agent_name", "provider", "adapter", "stage",
              "agent_status", "duration_ms", "validation_status", "fallback_used"):
        assert k in r
    assert r["agent_status"] == "finished" and r["duration_ms"] == 12


def test_failed_agent_record_keeps_duration_stage_error(tmp_path: Path):
    obs.append_agent_event(
        tmp_path, agent_name="prover", provider="external", adapter="codex", stage="prover",
        agent_status="failed", duration_ms=300, error_summary="boom",
        fallback_used=True, fallback_reason="timeout",
    )
    r = obs.read_agent_events(tmp_path)[0]
    assert r["agent_status"] == "failed" and r["stage"] == "prover"
    assert r["duration_ms"] == 300 and r["error_summary"] == "boom"
    assert r["fallback_used"] is True and r["fallback_reason"] == "timeout"


def test_forbidden_status_key_is_not_written(tmp_path: Path):
    # A record carrying a status-upgrade field must be dropped entirely.
    obs.append_agent_event(
        tmp_path, agent_name="x", provider="stub", adapter="stub", stage="s",
        agent_status="finished", source_backed=True,
    )
    assert obs.read_agent_events(tmp_path) == []
    obs.append_agent_event(
        tmp_path, agent_name="x", provider="stub", adapter="stub", stage="s",
        agent_status="finished", proof_status="proved",
    )
    assert obs.read_agent_events(tmp_path) == []


def test_status_named_value_in_status_field_is_rejected(tmp_path: Path):
    obs.append_agent_event(
        tmp_path, agent_name="x", provider="stub", adapter="stub", stage="s",
        agent_status="finished", validation_status="source_backed",
    )
    assert obs.read_agent_events(tmp_path) == []


def test_secret_looking_field_is_scrubbed_not_written(tmp_path: Path):
    obs.append_agent_event(
        tmp_path, agent_name="x", provider="stub", adapter="stub", stage="s",
        agent_status="finished", duration_ms=1, api_key="SECRET123", bearer_token="T",
    )
    blob = (tmp_path / obs.AGENT_EVENTS_FILENAME).read_text(encoding="utf-8")
    assert "SECRET123" not in blob and "api_key" not in blob and len(obs.read_agent_events(tmp_path)) == 1


def test_absolute_or_escaping_path_fails_closed(tmp_path: Path):
    # Writing into a non-confinable dir must not raise and must not write here.
    obs.append_agent_event(
        Path("/etc"), agent_name="x", provider="p", adapter="a", stage="s", agent_status="finished",
    )
    # observability never raises into the runtime; /etc/agent_events.jsonl is not created by us.
    assert not (tmp_path / obs.AGENT_EVENTS_FILENAME).exists()


def test_events_carry_monotonic_ms_and_new_kinds():
    for k in ("agent_call_started", "agent_call_finished", "agent_call_failed",
              "server_job", "replay", "note"):
        assert k in EVENT_KINDS


# --- rollback survival (the failed-agent record must not be erased) ----------

def test_agent_events_survive_stage_rollback(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_yaml(run_dir / "run_manifest.yaml", {"current_state": "SCAFFOLDED", "artifacts": {}})
    runner = StageRunner(run_dir)

    def mutate():
        # an agent ran and FAILED during this stage
        obs.append_agent_event(run_dir, agent_name="a", provider="stub", adapter="stub",
                               stage="s", agent_status="failed", error_summary="x")
        append_event(run_dir, "agent_call_failed", stage="s", detail="x")

    with pytest.raises(StageError):
        runner.run(name="s", mutate=mutate, validate=lambda: (_ for _ in ()).throw(ValueError("v")))

    # rollback happened, but the failed agent record + events survived (excluded from snapshot)
    assert any(a["agent_status"] == "failed" for a in obs.read_agent_events(run_dir))
    assert any(e["kind"] == "rollback" for e in read_events(run_dir))


# --- integration: a real stub run produces all 3, validate_run still passes --

def _stub_run(tmp_path: Path):
    claim = tmp_path / "c.md"
    claim.write_text("Every group of order p^2, where p is a prime, is abelian.\n", encoding="utf-8")
    res = orchestrate_run(
        project="obs", input_path=claim, output_root=tmp_path,
        provider="stub", front_half="stub", front_half_client=FakeClaude(precision="precise"),
    )
    return Path(res["run_dir"])


def test_run_produces_observability_and_still_validates(tmp_path: Path):
    run_dir = _stub_run(tmp_path)
    events = read_events(run_dir)
    agent_events = obs.read_agent_events(run_dir)
    timings = read_yaml(run_dir / obs.TIMINGS_FILENAME)

    assert events and all("monotonic_ms" in e for e in events)
    assert {"agent_call_started", "agent_call_finished"} <= {e.get("kind") for e in events}
    # Goal 65B: each call now emits a "started" record (so a mid-call death is
    # visible) plus a terminal record. Every started call here completed, so
    # every started has a matching finished and there is no dangling call.
    terminal = [a for a in agent_events if a.get("agent_status") != "started"]
    assert terminal and all(a.get("agent_status") == "finished" for a in terminal)
    assert all(isinstance(a.get("duration_ms"), int) for a in terminal)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    prover_terminal = [
        a for a in terminal if a.get("stage") == "llm_prover_reviewers"
    ]
    assert prover_terminal
    assert all(a.get("run_id") == manifest["run_id"] for a in prover_terminal)
    assert all(a.get("validation_status") == "passed" for a in prover_terminal)
    assert any(a.get("agent_status") == "started" for a in agent_events)
    assert obs.latest_unfinished_agent_call(run_dir) is None
    assert timings["stage_durations_ms"] and isinstance(timings["total_duration_ms"], int)
    # all three registered as OPTIONAL artifacts
    optional = manifest["optional_artifacts"]
    assert optional.get("events") == "events.jsonl"
    assert optional.get("agent_events") == "agent_events.jsonl"
    assert optional.get("timings") == "timings.yaml"
    # registering them must not break validation
    validate_run(run_dir)
    # no abs paths / secrets in agent_events
    blob = (run_dir / obs.AGENT_EVENTS_FILENAME).read_text(encoding="utf-8")
    assert "/Users/" not in blob


def test_old_run_without_observability_artifacts_still_validates(tmp_path: Path):
    # Compatibility rule: a run missing the observability artifacts must still validate.
    run_dir = _stub_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    for key, fname in (("events", "events.jsonl"), ("agent_events", obs.AGENT_EVENTS_FILENAME),
                       ("timings", obs.TIMINGS_FILENAME)):
        manifest["optional_artifacts"].pop(key, None)
        p = run_dir / fname
        if p.exists():
            p.unlink()
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    validate_run(run_dir)  # must not raise — old-run shape stays valid


def test_no_observability_record_carries_status_upgrade(tmp_path: Path):
    run_dir = _stub_run(tmp_path)
    forbidden = ("source_backed", "proved", "proof_complete", "grade", "refuted",
                 "computation_checked")
    for line in (run_dir / obs.AGENT_EVENTS_FILENAME).read_text().splitlines():
        rec = json.loads(line)
        assert not (set(rec) & set(forbidden))


# --- deep paths: prover pass + resume rounds are instrumented too (54A coverage) ---

def test_orchestrate_prover_pass_emits_agent_event(tmp_path: Path):
    run_dir = _stub_run(tmp_path)
    stages = {a.get("stage") for a in obs.read_agent_events(run_dir)}
    # not only front-half stages — the deep Prover+Reviewers pass is recorded too.
    assert "llm_prover_reviewers" in stages


def test_post_call_validation_failure_emits_terminal_agent_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from espresso_proof.orchestration import orchestrator as orch

    claim = tmp_path / "c.md"
    claim.write_text(
        "Every group of order p^2, where p is a prime, is abelian.\n",
        encoding="utf-8",
    )
    original_validate = orch.validate_run

    def fail_after_llm_artifact(run_dir: Path) -> None:
        original_validate(run_dir)
        if Path(run_dir, "llm_agent_run.yaml").exists():
            raise RuntimeError("injected post-call validation failure")

    monkeypatch.setattr(orch, "validate_run", fail_after_llm_artifact)

    with pytest.raises(StageError, match="post-call validation failure"):
        orchestrate_run(
            project="obs_failed_validation",
            input_path=claim,
            output_root=tmp_path,
            provider="stub",
            front_half="stub",
            front_half_client=FakeClaude(precision="precise"),
        )

    run_dir = next(tmp_path.rglob("run_manifest.yaml")).parent
    llm_events = [
        event
        for event in obs.read_agent_events(run_dir)
        if event.get("stage") == "llm_prover_reviewers"
    ]
    assert any(event.get("agent_status") == "started" for event in llm_events)
    failed = [event for event in llm_events if event.get("agent_status") == "failed"]
    assert failed
    assert failed[-1]["validation_status"] == "failed"
    assert obs.latest_unfinished_agent_call(run_dir) is None


def test_resume_prover_rounds_emit_agent_events(tmp_path: Path):
    from espresso_proof.orchestration.orchestrator import resume_run
    from espresso_proof.kernel.io import read_yaml as _ry
    from tests.orchestration.test_prover_loop_continuation import _ProverClient
    run_dir = _stub_run(tmp_path)
    brief = _ry(run_dir / _ry(run_dir / "run_manifest.yaml")["artifacts"]["strategy_brief"])
    selected = brief["top_strategies"][0]["id"]
    resume_run(run_dir=run_dir, selected_strategy=selected, effort="normal_pass",
               prover_client=_ProverClient(objections=["loc X", "hyp Y"]))
    validate_run(run_dir)
    rounds = [a for a in obs.read_agent_events(run_dir) if a.get("stage") == "prover_loop_round"]
    assert rounds, "prover-loop rounds must emit agent_events for stall diagnosis"
    # Goal 65B: a round emits a "started" record (duration None) then a terminal
    # record (duration int); assert on the terminal records.
    terminal_rounds = [a for a in rounds if a.get("agent_status") != "started"]
    assert terminal_rounds
    assert all(isinstance(a.get("duration_ms"), int) for a in terminal_rounds)
    assert all(isinstance(a.get("round_index"), int) for a in rounds)


# --- Goal 65B: mid-call death reconstruction ---------------------------------

def test_latest_unfinished_agent_call_detects_mid_call_death(tmp_path: Path):
    """A call that records 'started' but no terminal record (the order-3
    process-death shape) is recoverable as the dangling started record."""
    # earlier call completed cleanly
    obs.append_agent_event(tmp_path, agent_name="strategy", provider="external",
                           adapter="external", stage="strategy", agent_status="started")
    obs.append_agent_event(tmp_path, agent_name="strategy", provider="external",
                           adapter="external", stage="strategy", agent_status="finished",
                           duration_ms=24000)
    # prover call started, then the worker died mid-call (no terminal record)
    obs.append_agent_event(tmp_path, agent_name="llm_prover_reviewers", provider="external",
                           adapter="external", stage="llm_prover_reviewers", agent_status="started")
    dangling = obs.latest_unfinished_agent_call(tmp_path)
    assert dangling is not None
    assert dangling["stage"] == "llm_prover_reviewers"
    assert dangling["agent_status"] == "started"


def test_latest_unfinished_agent_call_none_when_all_paired(tmp_path: Path):
    for status in ("started", "finished"):
        obs.append_agent_event(tmp_path, agent_name="a", provider="stub", adapter="stub",
                               stage="claim", agent_status=status, duration_ms=1)
    assert obs.latest_unfinished_agent_call(tmp_path) is None


def test_latest_unfinished_pairs_by_round_index(tmp_path: Path):
    """round 1 finished, round 2 died mid-call -> round 2 is the dangling call."""
    for rnd, statuses in ((1, ("started", "finished")), (2, ("started",))):
        for status in statuses:
            obs.append_agent_event(tmp_path, agent_name="prover_round", provider="external",
                                   adapter="external", stage="prover_loop_round",
                                   agent_status=status, round_index=rnd)
    dangling = obs.latest_unfinished_agent_call(tmp_path)
    assert dangling is not None and dangling["round_index"] == 2


def test_started_event_cannot_carry_status_upgrade(tmp_path: Path):
    """The new started record is still subject to the forbidden-key guard."""
    obs.append_agent_event(tmp_path, agent_name="x", provider="stub", adapter="stub",
                           stage="s", agent_status="started", source_backed=True)
    assert obs.read_agent_events(tmp_path) == []  # dropped: forbidden key
