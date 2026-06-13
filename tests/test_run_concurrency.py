"""Goal 35B: cross-process run writer lock, events.jsonl timeline, and atomic
YAML writes. Concurrency must never duplicate prover rounds or leave partial
artifacts; the timeline is diagnostic only and never leaks the environment."""

from __future__ import annotations

import json
import multiprocessing
import threading
import time
from pathlib import Path

import pytest

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.orchestrator import orchestrate_run, resume_run
from espresso_proof.run_events import EVENT_KINDS, EVENTS_FILENAME, append_event, read_events
from espresso_proof.run_lock import RunLockedError, run_writer_lock
from espresso_proof.validators import validate_run

FIXTURES = Path("tests/fixtures")


# -- lock primitive -----------------------------------------------------------


def _hold_lock(run_dir: str, acquired, release) -> None:
    with run_writer_lock(Path(run_dir), action="test hold"):
        acquired.set()
        release.wait(timeout=30)


def test_lock_excludes_other_process(tmp_path: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    acquired = ctx.Event()
    release = ctx.Event()
    proc = ctx.Process(target=_hold_lock, args=(str(tmp_path), acquired, release))
    proc.start()
    try:
        assert acquired.wait(timeout=30), "holder process never acquired the lock"
        with pytest.raises(RunLockedError, match="wait a moment"):
            with run_writer_lock(tmp_path, action="second writer", timeout_seconds=0.2):
                pass
    finally:
        release.set()
        proc.join(timeout=30)
    # after the holder releases, the lock is acquirable again.
    with run_writer_lock(tmp_path, action="after release", timeout_seconds=5):
        pass


def test_lock_excludes_other_thread_and_waits(tmp_path: Path) -> None:
    acquired = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def holder() -> None:
        with run_writer_lock(tmp_path, action="holder"):
            acquired.set()
            release.wait(timeout=30)
            order.append("holder done")

    t = threading.Thread(target=holder)
    t.start()
    assert acquired.wait(timeout=30)
    # a short timeout fails closed while the lock is held...
    with pytest.raises(RunLockedError):
        with run_writer_lock(tmp_path, action="contender", timeout_seconds=0.2):
            pass
    # ...and a generous timeout serializes after release.
    release.set()
    with run_writer_lock(tmp_path, action="contender", timeout_seconds=10):
        order.append("contender ran")
    t.join(timeout=30)
    assert order == ["holder done", "contender ran"]


def test_lock_is_reentrant_in_same_thread(tmp_path: Path) -> None:
    with run_writer_lock(tmp_path, action="outer"):
        with run_writer_lock(tmp_path, action="inner", timeout_seconds=0.2):
            pass  # no deadlock, no RunLockedError


def test_lock_denied_is_recorded_on_timeline(tmp_path: Path) -> None:
    acquired = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with run_writer_lock(tmp_path, action="holder"):
            acquired.set()
            release.wait(timeout=30)

    t = threading.Thread(target=holder)
    t.start()
    assert acquired.wait(timeout=30)
    try:
        with pytest.raises(RunLockedError):
            with run_writer_lock(tmp_path, action="resume", timeout_seconds=0.2):
                pass
    finally:
        release.set()
        t.join(timeout=30)
    kinds = [e["kind"] for e in read_events(tmp_path)]
    assert "lock_denied" in kinds


# -- concurrent resume: no duplicate prover rounds ----------------------------


def _gate_run(tmp_path: Path) -> Path:
    result = orchestrate_run(
        project="conc",
        input_path=FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    return Path(result["run_dir"])


def test_concurrent_resume_cannot_duplicate_rounds(tmp_path: Path) -> None:
    run_dir = _gate_run(tmp_path)
    brief = read_yaml(
        run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["strategy_brief"]
    )
    selected = brief["top_strategies"][0]["id"]
    effort = brief["allowed_human_choices"][0]["effort_options"][0]

    results: list[str] = []
    errors: list[Exception] = []

    def do_resume() -> None:
        try:
            memo = resume_run(
                run_dir=run_dir,
                selected_strategy=selected,
                effort=effort,
                prover_provider="stub",
            )
            results.append(memo)
        except Exception as exc:  # noqa: BLE001 - collected for assertion
            errors.append(exc)

    threads = [threading.Thread(target=do_resume) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    # Both resumes complete: the second serializes behind the first and takes
    # the idempotent COMPLETED_MEMO path (or one fails closed with the friendly
    # lock error — never a corrupted run).
    assert len(results) + len(errors) == 2
    for exc in errors:
        assert isinstance(exc, RunLockedError)
    validate_run(run_dir)
    # No duplicate prover rounds on disk.
    index = read_yaml(run_dir / "prover_rounds" / "index.yaml")
    keys = [(r["round_index"], r["loop_attempt_id"]) for r in index["rounds"]]
    assert len(keys) == len(set(keys)), f"duplicate prover rounds: {keys}"
    loop = read_yaml(run_dir / "prover_loop.yaml")
    assert loop["budget"]["rounds_used"] <= loop["budget"]["max_rounds"]
    # the index matches the attempts actually recorded in the loop (no orphans).
    assert len(index["rounds"]) == len(loop["attempts"])


# -- events.jsonl timeline ----------------------------------------------------


def test_events_timeline_valid_jsonl_and_no_env_leak(tmp_path: Path, monkeypatch) -> None:
    sentinel = "sk-test-SECRET-SENTINEL-12345"
    monkeypatch.setenv("OPENAI_API_KEY", sentinel)
    run_dir = _gate_run(tmp_path)
    events_path = run_dir / EVENTS_FILENAME
    assert events_path.exists(), "orchestrate_run produced no events.jsonl"
    # Every line parses as a JSON object with a known kind and timestamp.
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert lines
    for line in lines:
        record = json.loads(line)
        assert isinstance(record, dict)
        # Reference EVENT_KINDS directly so the allowlist never drifts when new
        # additive kinds (Goal 54A: agent_call_*, server_job, replay, note) are added.
        assert record["kind"] in set(EVENT_KINDS)
        assert "ts" in record
        assert "monotonic_ms" in record  # Goal 54A: every event carries a monotonic stamp
    kinds = [json.loads(line)["kind"] for line in lines]
    assert "stage_started" in kinds and "stage_ok" in kinds
    # Redaction: no API key / environment dump in the timeline.
    assert sentinel not in events_path.read_text(encoding="utf-8")
    # Diagnostic only: the run validates with (and without) the timeline.
    validate_run(run_dir)


def test_failed_stage_records_rollback_event_and_survives_restore(tmp_path: Path, monkeypatch) -> None:
    from espresso_proof import source_grounding_stage
    from espresso_proof.stage_runner import StageError

    run_dir = _gate_run(tmp_path)
    src = tmp_path / "src.txt"
    src.write_text("Theorem 1.2. Exact statement.", encoding="utf-8")

    def boom(*, run_dir: Path, theorem_extraction_path: Path) -> None:
        raise RuntimeError("injected failure")

    monkeypatch.setattr(source_grounding_stage, "ground_run", boom)
    with pytest.raises(StageError):
        source_grounding_stage.prepare_source_grounding(
            run_dir=run_dir, ground_source=src, authors=["A. Author"]
        )
    kinds = [e["kind"] for e in read_events(run_dir)]
    # The rollback record itself survives the restore (append-only timeline).
    assert "validation_failed" in kinds
    assert "rollback" in kinds
    validate_run(run_dir)


def test_event_writer_is_fail_safe(tmp_path: Path) -> None:
    # Unknown kinds are dropped; a read-only directory cannot raise into the
    # runtime (append_event swallows the OSError).
    append_event(tmp_path, "not_a_real_kind", detail="x")
    assert read_events(tmp_path) == []
    missing = tmp_path / "does_not_exist"
    append_event(missing / "nested", "stage_ok", stage="x")  # parent missing: swallowed
    assert not (missing / "nested" / EVENTS_FILENAME).exists()


# -- atomic write_yaml --------------------------------------------------------


def test_write_yaml_failure_preserves_previous_bytes(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "artifact.yaml"
    write_yaml(target, {"ok": 1})
    original = target.read_bytes()

    import espresso_proof.io as io_mod

    def exploding_dump(data, handle, **kwargs):
        handle.write("partial: ")  # bytes hit the TEMP file, never the target
        raise RuntimeError("injected dump failure")

    monkeypatch.setattr(io_mod.yaml, "safe_dump", exploding_dump)
    with pytest.raises(RuntimeError, match="injected dump failure"):
        write_yaml(target, {"ok": 2})
    assert target.read_bytes() == original, "failed write must not touch the target"
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"temp files left behind: {leftovers}"


def test_write_yaml_round_trip_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "artifact.yaml"
    data = {"a": 1, "nested": {"b": [1, 2, 3]}, "s": "text"}
    write_yaml(target, data)
    assert read_yaml(target) == data
    write_yaml(target, {"replaced": True})
    assert read_yaml(target) == {"replaced": True}
