from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.io import read_yaml
from espresso_proof.orchestrator import orchestrate_run, resume_run
from espresso_proof.run_dashboard import render_run_dashboard, write_run_dashboard
from espresso_proof.validators import validate_run


def _claim(tmp_path: Path) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(
        "Let p be a prime. Every group G of order p^2 is abelian.\n",
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
    brief = read_yaml(run_dir / "strategy_brief.yaml")
    selected = brief["top_strategies"][0]["id"]

    resume_run(run_dir=run_dir, selected_strategy=selected, effort="quick_test")
    validate_run(run_dir)
    html = (run_dir / "research_dashboard.html").read_text(encoding="utf-8")

    assert "Reviewed" in html
    assert "Needs repair" in html
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
    from espresso_proof.io import write_yaml

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
    from espresso_proof.io import write_yaml

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
    html = render_run_dashboard(run_dir)
    assert "admissible checked counterexample" not in html
    assert "hypothesis match unclear" in html
    # the route-card pressure line must not claim a refutation either
    assert "A checked counterexample refutes the claim" not in html
