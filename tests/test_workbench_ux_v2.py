"""Goal 28 Workbench UX V2: product entry page, mode labels, trust colours,
research timeline. All display-only; no UI element upgrades kernel status."""

from __future__ import annotations

from inspect import signature
from pathlib import Path

import pytest

from espresso_proof import evidence_trust as et
from espresso_proof.agent_run_status import set_agent_status
from espresso_proof.agent_runner import run_single_agent, skip_agent
from espresso_proof.orchestrator import orchestrate_run
from espresso_proof.research_timeline import build_timeline
from espresso_proof.run_dashboard import render_run_dashboard
from espresso_proof.server import Workbench, WorkbenchError, _index_page, serve
from espresso_proof.validators import validate_run

CLAIM = "Every group of order p^2, where p is prime, is abelian."
_PROOF_WORDS = ("proof-close", "proof complete", "the theorem is proved", "we have proved")


def _run(tmp_path: Path, name: str = "ux") -> Path:
    claim = tmp_path / f"{name}.md"
    claim.write_text(CLAIM, encoding="utf-8")
    return orchestrate_run(
        project=name, input_path=claim, output_root=tmp_path,
        provider="stub", front_half="stub", fast=True,
    )["run_dir"]


# --------------------------------------------------------------------------- #
# entry page
# --------------------------------------------------------------------------- #


def test_entry_page_is_a_wide_product_page(tmp_path: Path) -> None:
    wb = Workbench(tmp_path / "root", provider="stub")
    html = _index_page(wb)
    # a real product landing, not a tiny 760px debug form
    assert "grid-template-columns:minmax(620px,1.15fr) minmax(500px,.85fr)" in html
    assert "@media (max-width:1224px)" in html
    assert "max-width:760px" not in html
    # Product UI exposes the real-agent path only; deterministic smoke/stub
    # remains backend-only for tests/demos, not a user mode.
    assert "Real agent run" in html
    assert "Fast preview" not in html
    assert 'value="fast"' not in html
    # Goal 32: demos are NOT a run mode; they live in a separate "Open demo"
    # section.
    assert "Demo mode" not in html
    assert "Open demo" in html
    assert ">stub<" not in html and ">external<" not in html
    # workflow guidance + demos + recent runs
    assert "How it works" in html
    assert "Recent runs" in html
    assert "p² group theorem" in html


def test_entry_page_has_no_proof_complete_language(tmp_path: Path) -> None:
    wb = Workbench(tmp_path / "root", provider="stub")
    html = _index_page(wb).lower()
    for w in _PROOF_WORDS:
        assert w not in html


# --------------------------------------------------------------------------- #
# mode -> provider mapping
# --------------------------------------------------------------------------- #


def test_mode_maps_to_provider(tmp_path: Path) -> None:
    wb = Workbench(tmp_path / "root", provider="stub")
    # Backend mapping remains for smoke tests and explicit API callers.
    assert wb._provider_for_mode("fast") == "stub"
    assert wb._provider_for_mode("codex") == "external"
    # Goal 32: "demo" is not a mode; unknown modes fall back to the server default
    assert wb._provider_for_mode("demo") == "stub"  # fallback, not a mode mapping
    assert wb._provider_for_mode(None) == "stub"  # falls back to server default


def test_product_server_defaults_to_external_provider(tmp_path: Path) -> None:
    """The canonical product server must not silently default to template runs."""

    assert Workbench(tmp_path / "root")._provider_for_mode(None) == "external"
    assert signature(serve).parameters["provider"].default == "external"


def test_invalid_mode_rejected(tmp_path: Path) -> None:
    wb = Workbench(tmp_path / "root", provider="stub")
    with pytest.raises(WorkbenchError):
        wb.start_create_run(CLAIM, mode="bogus")


# --------------------------------------------------------------------------- #
# trust-label colour discipline
# --------------------------------------------------------------------------- #


def test_green_reserved_for_earned_evidence_only() -> None:
    for level in et.TRUST_LABELS:
        if level in ("reference_backed", "scope_checked"):
            assert et.is_green(level), level
            assert et.TONE_CLASS[et.trust_tone(level)] == "trust-green"
        else:
            assert not et.is_green(level), level
            assert et.TONE_CLASS[et.trust_tone(level)] != "trust-green"
    # candidate / running / skipped specifically are never green
    for level in ("candidate", "smoke_checked", "running", "skipped", "needs_check", "failed"):
        assert et.TONE_CLASS[et.trust_tone(level)] != "trust-green"


def test_trust_labels_are_human_readable() -> None:
    for label in et.TRUST_LABELS.values():
        assert label == label.strip() and label[0].isupper()
        assert "_" not in label  # never a raw enum token


# --------------------------------------------------------------------------- #
# research timeline
# --------------------------------------------------------------------------- #


def test_timeline_shows_claim_route_and_evidence_events(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    run_single_agent(run_dir, "examples")
    events = build_timeline(run_dir)
    texts = " | ".join(e["text"] for e in events)
    assert "Claim locked" in texts
    assert "proof route" in texts
    assert "Examples recorded" in texts


def test_timeline_skipped_and_failed_are_not_done(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    skip_agent(run_dir, "counterexamples", reason="defer")
    set_agent_status(run_dir, "literature", "failed", error_summary="provider timeout")
    validate_run(run_dir)
    events = build_timeline(run_dir)
    by_text = {e["text"]: e["tone"] for e in events}
    skip_ev = [t for t in by_text if "Counterexamples skipped" in t]
    fail_ev = [t for t in by_text if "Literature Review failed" in t]
    assert skip_ev and by_text[skip_ev[0]] == "skip"
    assert fail_ev and by_text[fail_ev[0]] == "fail"
    # no event claims those agents are done/checked
    assert not any("Counterexamples checked" in t or "Counterexample search:" in t for t in by_text)


def test_timeline_does_not_upgrade_status(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    grade0 = (run_dir / "idea_grade.yaml").read_bytes()
    build_timeline(run_dir)  # pure read
    assert (run_dir / "idea_grade.yaml").read_bytes() == grade0
    validate_run(run_dir)


def test_dashboard_renders_timeline_without_green_on_skip_or_fail(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    skip_agent(run_dir, "counterexamples", reason="defer")
    set_agent_status(run_dir, "literature", "failed", error_summary="x")
    validate_run(run_dir)
    html = render_run_dashboard(run_dir, interactive=True)
    assert "Research Timeline" in html
    # the skip/fail timeline rows use skip/fail tone classes, never the green tone
    assert 'class="tl skip"' in html
    assert 'class="tl fail"' in html
    assert 'class="tl earned"' not in html  # nothing earned green in this run


def test_run_page_has_no_proof_complete_language(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    run_single_agent(run_dir, "examples")
    html = render_run_dashboard(run_dir, interactive=True).lower()
    for w in _PROOF_WORDS:
        assert w not in html


def test_timeline_dropped_hypothesis_is_not_admissible_refutation(tmp_path: Path) -> None:
    """A dropped-hypothesis counterexample must read as 'none refute', never as an
    admissible refutation — the timeline mirrors the kernel admissibility gate."""
    from espresso_proof.io import read_yaml, write_yaml

    run_dir = _run(tmp_path)
    ce_path = run_dir / "counterexamples.yaml"
    ce = read_yaml(ce_path)
    ce["tests"] = [
        {
            "id": "cx_drop", "operator_id": "op", "example_id": "ex",
            "satisfies_all_claim_hypotheses": False,  # dropped hypothesis
            "target": "theorem", "result": "failed",
            "refutes_claim": False, "refutes_step_only": False,
            "exposes_missing_hypothesis": True, "missing_hypothesis": "order is p^2",
            "explanation": "A larger group fails, but it drops the order hypothesis.",
        }
    ]
    write_yaml(ce_path, ce)
    set_agent_status(run_dir, "counterexamples", "done")
    validate_run(run_dir)
    texts = " | ".join(e["text"] for e in build_timeline(run_dir))
    assert "admissible checked counterexample refutes" not in texts
    assert "none refute the claim" in texts


def test_codex_mode_fails_closed_when_external_unconfigured(tmp_path: Path, monkeypatch) -> None:
    """'Codex deep run' maps to the real external provider. If it is not
    configured the BACKGROUND job fails closed with a friendly error, no run is
    created, and nothing is upgraded — the browser is never left hanging."""
    import time

    monkeypatch.delenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", raising=False)
    wb = Workbench(tmp_path / "root", provider="stub")
    out = wb.start_create_run(CLAIM, mode="codex")
    job_id = out["job_id"]
    deadline = time.time() + 15
    while time.time() < deadline:
        job = wb.get_job(job_id)
        if job["state"] in ("done", "failed"):
            break
        time.sleep(0.2)
    assert job["state"] == "failed"
    assert job.get("error")  # a human-readable error, not a hang
    assert wb.list_runs() == []  # no run dir created, nothing upgraded


def test_product_create_run_defaults_to_codex_mode() -> None:
    """The built cockpit should not silently create template/stub runs."""
    api = Path("web/src/lib/api.ts").read_text(encoding="utf-8")
    assert 'mode = "codex"' in api
