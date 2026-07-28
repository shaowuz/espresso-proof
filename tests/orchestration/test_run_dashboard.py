from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.kernel.agent_run_status import set_agent_status
from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.cli import app, serve_command
from espresso_proof.cli_dev import bank_dashboard_command, run_dashboard_command
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.orchestrator import orchestrate_run, resume_run
from espresso_proof.orchestration.run_dashboard import render_run_dashboard, write_run_dashboard
from espresso_proof.kernel.validators import validate_run


def _claim(tmp_path: Path) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(
        "Every group of order p^2, where p is prime, is abelian.\n",
        encoding="utf-8",
    )
    return path


def _fresh_run(tmp_path: Path) -> Path:
    result = orchestrate_run(
        project="dashboard_demo",
        input_path=_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
    )
    return result["run_dir"]


def _mark_claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest["current_state"] = "WAITING_LLM_AGENT_HUMAN_GATE"
    write_yaml(run_dir / "run_manifest.yaml", manifest)


def test_orchestrate_writes_minimal_interactive_dashboard_at_gate(tmp_path: Path) -> None:
    run_dir = _fresh_run(tmp_path)
    validate_run(run_dir)

    dashboard = run_dir / "research_dashboard.html"
    assert dashboard.exists()
    html = dashboard.read_text(encoding="utf-8")

    assert "Espresso Proof" in html
    assert "Choose Route" in html
    assert "Selected Plan" in html
    assert "Copy command" in html
    assert "aria-pressed=\"true\"" in html
    assert "--select-strategy" in html
    assert "Waiting" in html
    assert "Repair needed" not in html
    assert "source_backed" not in html


def test_resume_refreshes_dashboard_with_repair_state(tmp_path: Path) -> None:
    run_dir = _fresh_run(tmp_path)
    _mark_claim_clear(run_dir)
    brief = read_yaml(run_dir / "strategy_brief.yaml")
    selected = brief["top_strategies"][0]["id"]
    choices = {
        choice["strategy_id"]: choice.get("effort_options") or []
        for choice in brief.get("allowed_human_choices", [])
    }
    effort = (choices.get(selected) or ["quick_test"])[0]

    resume_run(run_dir=run_dir, selected_strategy=selected, effort=effort)
    validate_run(run_dir)
    html = (run_dir / "research_dashboard.html").read_text(encoding="utf-8")

    # This is a STUB run, so the dashboard must NOT present it as a real
    # reviewed prover loop — it shows the honest template-fallback label instead.
    assert "Reviewed" not in html
    assert "Needs repair" not in html
    assert "no real prover ran" in html
    assert "Review Result" in html
    assert "Not a refutation" in html
    assert "--literature-scout" in html
    assert "No proof status was upgraded" in html


def test_run_dashboard_cli_writes_default_and_custom_output(tmp_path: Path) -> None:
    run_dir = _fresh_run(tmp_path)
    default = run_dir / "research_dashboard.html"
    default.unlink()
    runner = CliRunner()

    result = runner.invoke(app, ["run-dashboard", "--run", str(run_dir)])
    assert result.exit_code == 0, result.output
    assert default.exists()
    assert "Wrote run dashboard" in result.output

    custom = tmp_path / "custom_dashboard.html"
    result = runner.invoke(app, ["run-dashboard", "--run", str(run_dir), "--out", str(custom)])
    assert result.exit_code == 0, result.output
    assert custom.exists()
    assert "Every group" in custom.read_text(encoding="utf-8")


def test_legacy_dashboard_cli_docstrings_mark_dev_only() -> None:
    assert "legacy/dev-only static dashboard" in (run_dashboard_command.__doc__ or "")
    assert "dev-only static HTML dashboard" in (bank_dashboard_command.__doc__ or "")
    assert "canonical product entry" in (serve_command.__doc__ or "").lower()
    assert "external Codex-agent" in (serve_command.__doc__ or "")


def test_run_dashboard_is_read_only_for_yaml_artifacts(tmp_path: Path) -> None:
    run_dir = _fresh_run(tmp_path)
    before = {
        path.relative_to(run_dir): path.read_bytes()
        for path in run_dir.rglob("*.yaml")
    }

    render_run_dashboard(run_dir)
    write_run_dashboard(run_dir)

    after = {
        path.relative_to(run_dir): path.read_bytes()
        for path in run_dir.rglob("*.yaml")
    }
    assert after == before


# --- review-required regression tests (UI rewrite) --------------------------


def test_dashboard_escapes_script_breakout_in_route_text(tmp_path: Path) -> None:
    """Stored-XSS guard: artifact free text containing </script> must not be able
    to terminate the inline script block or inject live HTML (reviewer BLOCKER).
    The embedded JSON escapes <, >, & to \\uXXXX."""
    run_dir = _fresh_run(tmp_path)
    brief_path = run_dir / "strategy_brief.yaml"
    brief = read_yaml(brief_path)
    payload = "</script><img src=x onerror=alert(1)>"
    for route in brief.get("top_strategies") or []:
        route["why_this_route"] = f"{payload} plausible idea"
    write_yaml(brief_path, brief)
    html = render_run_dashboard(run_dir)
    assert "</script><img" not in html
    assert "onerror=alert(1)>" not in html
    # the payload survives only in escaped form
    assert "\\u003c/script\\u003e" in html


def test_dashboard_unclear_counterexample_never_admissible(tmp_path: Path) -> None:
    """Display can never be looser than the kernel: a counterexample with
    satisfies_all_claim_hypotheses == 'unclear' must NOT render as an admissible
    refutation, even if it carries refutes_claim (reviewer BLOCKER)."""
    from espresso_proof.kernel.io import write_yaml

    run_dir = _fresh_run(tmp_path)
    ce_path = run_dir / "counterexamples.yaml"
    ce = read_yaml(ce_path)
    claim_id = ce.get("claim_id")
    ce["tests"] = [
        {
            "id": "cx_unclear",
            "operator_id": "op_manual",
            "example_id": "ex_unclear",
            "satisfies_all_claim_hypotheses": "unclear",
            "target": "theorem",
            "result": "failed",
            "refutes_claim": True,
            "refutes_step_only": False,
            "exposes_missing_hypothesis": False,
            "explanation": "A candidate counterexample with unclear hypothesis match.",
        }
    ]
    ce["claim_id"] = claim_id
    write_yaml(ce_path, ce)
    set_agent_status(run_dir, "counterexamples", "done")
    html = render_run_dashboard(run_dir)
    assert "admissible checked counterexample" not in html
    assert "hypothesis match unclear" in html
    # the route-card pressure line must not claim a refutation either
    assert "A checked counterexample refutes the claim" not in html


# ---------------------------------------------------------------------------
# PR2: headline priority + route-table provenance + advisory attempt section
# ---------------------------------------------------------------------------

from espresso_proof.orchestration.run_dashboard import _headline_blocker


def test_main_blocker_headline_never_context_note() -> None:
    p3 = {"text": "Global evidence note (non-blocking context)", "severity": "P3"}
    p1 = {"text": "S6 induction gap must be closed", "severity": "P1"}
    # [hard-negative] a P3 context note can never headline
    latest = {"blocker_details": [p3, p1], "blockers": [p3["text"], p1["text"]]}
    assert "S6 induction gap" in _headline_blocker(latest, latest["blockers"])
    # all-P3: honest 'no blocking repair' wording, never a P3 text
    all_p3 = {"blocker_details": [p3], "blockers": [p3["text"]]}
    headline = _headline_blocker(all_p3, all_p3["blockers"])
    assert "No blocking reviewer repair recorded" in headline
    assert "non-blocking context" not in headline
    # legacy attempt without details falls back to blockers[0]
    legacy = {"blockers": ["legacy first blocker"]}
    assert "legacy first blocker" in _headline_blocker(legacy, legacy["blockers"])
    assert _headline_blocker({}, []) == "No blocking reviewer repair recorded."


def test_route_table_provenance_and_advisory_attempt(tmp_path: Path) -> None:
    claim = tmp_path / "claim.md"
    claim.write_text("Every group of order p^2, where p is prime, is abelian.", encoding="utf-8")
    result = orchestrate_run(
        project="dashboard_advisory",
        input_path=claim,
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
        auto_continue=True,
    )
    run_dir = Path(result["run_dir"])
    html = render_run_dashboard(run_dir)
    assert "source-grounding plan" in html
    if (run_dir / "advisory_proof_sketch.yaml").exists():
        assert "Latest prover attempt (advisory, unvalidated)" in html
        assert "may deviate from the" in html
    # [hard-negative] removing the sidecar removes the advisory section
    sketch_path = run_dir / "advisory_proof_sketch.yaml"
    if sketch_path.exists():
        sketch_path.unlink()
    html_without = render_run_dashboard(run_dir)
    assert "Latest prover attempt (advisory, unvalidated)" not in html_without
