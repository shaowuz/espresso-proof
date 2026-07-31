"""Performance Perception: display-only fragment cache with honest
invalidation, timing display, and the confined technical-log endpoint. Nothing
here may bypass validation or make stale/missing evidence look checked."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import espresso_proof.server as server_mod
from espresso_proof.orchestration.agent_runner import run_single_agent
from espresso_proof.server import Workbench, WorkbenchError

from tests.support_jobs import wait_for_job

CLAIM = "Every group of order p^2, where p is prime, is abelian."


def _wb_with_run(tmp_path: Path) -> tuple[Workbench, str]:
    wb = Workbench(tmp_path / "root", provider="stub")
    out = wb.start_create_run(CLAIM)
    job = wait_for_job(lambda: wb.get_job(out["job_id"]))
    return wb, job["result"]["run_id"]


# --------------------------------------------------------------------------- #
# display cache: hit, invalidate on change, fail closed on invalid run
# --------------------------------------------------------------------------- #


def test_status_cache_hits_and_invalidates_on_artifact_change(tmp_path: Path, monkeypatch) -> None:
    wb, run_id = _wb_with_run(tmp_path)
    calls = {"n": 0}
    real = server_mod.status_summary

    def counting(run_dir):  # noqa: ANN001
        calls["n"] += 1
        return real(run_dir)

    monkeypatch.setattr(server_mod, "status_summary", counting)
    first = wb.status(run_id)
    assert calls["n"] == 1
    second = wb.status(run_id)  # unchanged artifacts -> cache hit, no re-render
    assert calls["n"] == 1
    assert second == first
    # mutate an artifact through the kernel path -> digest changes -> re-render
    run_dir = wb.resolve_run(run_id)
    run_single_agent(run_dir, "examples")
    third = wb.status(run_id)
    assert calls["n"] == 2
    agents = {a["agent"]: a["status"] for a in third["agents"]}
    assert agents["examples"] == "done"


def test_stale_cache_never_served_after_artifact_removal(tmp_path: Path) -> None:
    """Removing an artifact invalidates the cached fragment: the run stops
    validating and status() raises — stale 'checked' HTML is never replayed."""
    wb, run_id = _wb_with_run(tmp_path)
    wb.status(run_id)  # populate the cache
    run_dir = wb.resolve_run(run_id)
    (run_dir / "examples.yaml").unlink()
    with pytest.raises(WorkbenchError) as exc:
        wb.status(run_id)
    assert "no longer passes" in exc.value.message  # honest copy, not a stack
    # and the cache entry is gone (no replay even if asked again)
    with pytest.raises(WorkbenchError):
        wb.status(run_id)


def test_cache_invalidates_on_non_yaml_artifact(tmp_path: Path) -> None:
    """Kernel-review blocker pin: validate_run also checks final_report.md
    (existence + proof language) — a non-YAML artifact must invalidate the
    cache exactly like a YAML one. Stale 'checked' HTML is never replayed."""
    wb, run_id = _wb_with_run(tmp_path)
    wb.status(run_id)  # populate cache
    run_dir = wb.resolve_run(run_id)
    report = run_dir / "final_report.md"
    # (a) language tamper -> digest changes -> revalidation fails -> 409, no stale serve
    original = report.read_text()
    report.write_text(original + "\nWe proved the theorem. Proof complete.\n")
    with pytest.raises(WorkbenchError):
        wb.status(run_id)
    # (b) restore -> fresh validated render again
    report.write_text(original)
    assert wb.status(run_id)["run_id"] == run_id
    # (c) removal -> 409 again, never the cached fragment
    report.unlink()
    with pytest.raises(WorkbenchError):
        wb.status(run_id)


def test_cache_is_displayonly_deep_copy(tmp_path: Path) -> None:
    """Mutating a returned summary cannot poison the cache (JSON round-trip)."""
    wb, run_id = _wb_with_run(tmp_path)
    first = wb.status(run_id)
    first["state"] = "POISONED"
    first["agents"][0]["status_word"] = "Checked"
    second = wb.status(run_id)
    assert second["state"] != "POISONED"
    assert second["agents"][0]["status_word"] != "Checked"


# --------------------------------------------------------------------------- #
# timings: jobs + agents (display only)
# --------------------------------------------------------------------------- #


def test_jobs_record_duration(tmp_path: Path) -> None:
    wb, run_id = _wb_with_run(tmp_path)
    jobs = [j for j in wb._jobs.values() if j["state"] == "done"]
    assert jobs and all(isinstance(j.get("duration_seconds"), float) for j in jobs)


def test_status_exposes_agent_last_updated(tmp_path: Path) -> None:
    wb, run_id = _wb_with_run(tmp_path)
    run_dir = wb.resolve_run(run_id)
    run_single_agent(run_dir, "examples")
    summary = wb.status(run_id)
    ex = next(a for a in summary["agents"] if a["agent"] == "examples")
    assert ex["last_updated"]
    assert isinstance(ex.get("duration_seconds"), (int, float))


def test_agent_card_shows_updated_time_not_proof_progress(tmp_path: Path) -> None:
    from espresso_proof.orchestration.run_dashboard import render_run_dashboard

    wb, run_id = _wb_with_run(tmp_path)
    run_dir = wb.resolve_run(run_id)
    run_single_agent(run_dir, "examples")
    html = render_run_dashboard(run_dir)
    assert 'class="aupdated"' in html and "updated " in html
    # a timing never implies proof progress
    lowered = html.lower()
    for phrase in ("proof progress", "almost proved", "closer to a proof"):
        assert phrase not in lowered


# --------------------------------------------------------------------------- #
# reload during a running job: board state survives a fresh render
# --------------------------------------------------------------------------- #


def test_reload_during_running_job_shows_running(tmp_path: Path) -> None:
    from espresso_proof.kernel.agent_run_status import set_agent_status
    from espresso_proof.orchestration.run_dashboard import render_run_dashboard
    from espresso_proof.kernel.validators import validate_run

    wb, run_id = _wb_with_run(tmp_path)
    run_dir = wb.resolve_run(run_id)
    set_agent_status(run_dir, "literature", "running", increment_attempt=True)
    validate_run(run_dir)
    # a fresh page render (= reload) shows the persisted running state honestly
    html = render_run_dashboard(run_dir)
    assert '<span class="adot running"></span>' in html
    assert "In progress; not yet evidence." in html