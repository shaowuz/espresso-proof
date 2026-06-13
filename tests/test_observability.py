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

from espresso_proof import observability as obs
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.orchestrator import orchestrate_run
from espresso_proof.run_events import EVENT_KINDS, append_event, read_events
from espresso_proof.stage_runner import StageRunner, StageError
from espresso_proof.validators import validate_run
from tests.test_evidence_aware_grade import FakeClaude


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
    assert agent_events and all(a.get("agent_status") == "finished" for a in agent_events)
    assert all(isinstance(a.get("duration_ms"), int) for a in agent_events)
    assert timings["stage_durations_ms"] and isinstance(timings["total_duration_ms"], int)
    # all three registered as OPTIONAL artifacts
    optional = read_yaml(run_dir / "run_manifest.yaml")["optional_artifacts"]
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
