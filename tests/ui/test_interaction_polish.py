"""Goal 29 Interaction Polish: state-preserving refresh, skeletons, button/job
states, calm errors. Display-only — no UI state may predict or upgrade evidence."""

from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.agent_run_status import set_agent_status
from espresso_proof.orchestration.agent_runner import run_single_agent
from espresso_proof.orchestration.orchestrator import orchestrate_run
from espresso_proof.orchestration.run_dashboard import render_run_dashboard
from espresso_proof.server import Workbench
from espresso_proof.kernel.validators import validate_run

CLAIM = "Every group of order p^2, where p is prime, is abelian."


def _run(tmp_path: Path) -> Path:
    claim = tmp_path / "c.md"
    claim.write_text(CLAIM, encoding="utf-8")
    return orchestrate_run(
        project="polish", input_path=claim, output_root=tmp_path,
        provider="stub", front_half="stub", fast=True,
    )["run_dir"]


# --------------------------------------------------------------------------- #
# state-preserving refresh
# --------------------------------------------------------------------------- #


def test_agent_cards_carry_stable_identity(tmp_path: Path) -> None:
    """Every evidence card has a data-agent id (stable identity for tooling;
    the legacy refresh JS died with the legacy UI)."""
    run_dir = _run(tmp_path)
    html = render_run_dashboard(run_dir)
    for agent in ("claim_understanding", "literature", "examples", "counterexamples", "failure_patterns"):
        assert f'data-agent="{agent}"' in html


# --------------------------------------------------------------------------- #
# skeleton / busy states never predict evidence
# --------------------------------------------------------------------------- #


def test_skeleton_and_busy_styles_are_neutral(tmp_path: Path) -> None:
    """Skeleton shimmer + busy spinner exist and use neutral tones only — a
    loading state never renders the earned-green evidence tone."""
    run_dir = _run(tmp_path)
    html = render_run_dashboard(run_dir)
    skel_css = html.split(".skel{", 1)[1].split("@keyframes shimmer", 1)[0]
    assert "--green" not in skel_css and "#2e7d54" not in skel_css
    busy_css = html.split(".act.busy{", 1)[1].split("@keyframes spin", 1)[0]
    assert "--green" not in busy_css and "#2e7d54" not in busy_css
    assert "#evidence-panel.refreshing" in html


def test_running_and_failed_agents_show_no_done_styling(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    set_agent_status(run_dir, "examples", "running", increment_attempt=True)
    set_agent_status(run_dir, "literature", "failed", error_summary="x")
    validate_run(run_dir)
    html = render_run_dashboard(run_dir)
    # the running/failed cards carry running/failed dots, not done
    assert '<span class="adot running"></span>' in html
    assert '<span class="adot failed"></span>' in html
    # exactly one done badge (claim understanding) — not examples/literature
    assert html.count('aword done') == 1


# --------------------------------------------------------------------------- #
# calm errors / entry-page job row
# --------------------------------------------------------------------------- #


def test_run_page_hides_tracebacks_and_raw_enums(tmp_path: Path) -> None:
    # The static page shows copy commands BY DESIGN (it degrades without a
    # server); what must never appear is implementation leakage.
    run_dir = _run(tmp_path)
    run_single_agent(run_dir, "examples")
    html = render_run_dashboard(run_dir)
    assert "Traceback" not in html
    visible = html.split("<script>")[0]
    for enum in ("source_backed", "computation_checked", "human_needed", "idea_sketch", "likely_false"):
        # raw enums never appear outside the JSON island
        assert enum not in visible, enum


def test_effort_pill_is_humanized(tmp_path: Path) -> None:
    """The selected-plan effort pill shows 'quick test', not the raw enum."""
    run_dir = _run(tmp_path)
    html = render_run_dashboard(run_dir)
    pill = html.split('id="selected-effort">')[1].split("<")[0]
    assert "_" not in pill
    # the JS keeps it humanized after route switches
    assert "humanEffort(route.effort)" in html