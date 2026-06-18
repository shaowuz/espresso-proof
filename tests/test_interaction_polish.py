"""Goal 29 Interaction Polish: state-preserving refresh, skeletons, button/job
states, calm errors. Display-only — no UI state may predict or upgrade evidence."""

from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.agent_run_status import set_agent_status
from espresso_proof.orchestration.agent_runner import run_single_agent
from espresso_proof.orchestration.orchestrator import orchestrate_run
from espresso_proof.orchestration.run_dashboard import render_run_dashboard
from espresso_proof.server import Workbench, _index_page
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


def test_agent_cards_carry_stable_identity_for_refresh(tmp_path: Path) -> None:
    """Every evidence card has a data-agent id so the refresh JS can restore
    which cards the reader had open (no lost place after a panel swap)."""
    run_dir = _run(tmp_path)
    html = render_run_dashboard(run_dir, interactive=True)
    for agent in ("claim_understanding", "literature", "examples", "counterexamples", "failure_patterns"):
        assert f'data-agent="{agent}"' in html
    # the refresh JS actually restores open state and scroll
    assert "openAgents" in html and "window.scrollTo" in html
    assert 'classList.add("refreshing")' in html


# --------------------------------------------------------------------------- #
# skeleton / busy states never predict evidence
# --------------------------------------------------------------------------- #


def test_skeleton_and_busy_styles_are_neutral(tmp_path: Path) -> None:
    """Skeleton shimmer + busy spinner exist and use neutral tones only — a
    loading state never renders the earned-green evidence tone."""
    run_dir = _run(tmp_path)
    html = render_run_dashboard(run_dir, interactive=True)
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
    html = render_run_dashboard(run_dir, interactive=True)
    # the running/failed cards carry running/failed dots, not done
    assert '<span class="adot running"></span>' in html
    assert '<span class="adot failed"></span>' in html
    # exactly one done badge (claim understanding) — not examples/literature
    assert html.count('aword done') == 1


# --------------------------------------------------------------------------- #
# calm errors / entry-page job row
# --------------------------------------------------------------------------- #


def test_entry_page_has_durable_job_row_with_retry(tmp_path: Path) -> None:
    wb = Workbench(tmp_path / "root", provider="stub")
    html = _index_page(wb)
    assert 'id="jobrow"' in html
    assert 'id="jr-retry"' in html and "Retry" in html
    assert "Check that Espresso is still running" in html
    # the long-wait copy exists for deep runs
    assert "Codex is still working" in html


def test_entry_page_main_path_is_clean(tmp_path: Path) -> None:
    """No raw terminal commands, YAML names, tracebacks, or provider enums on the
    entry page."""
    wb = Workbench(tmp_path / "root", provider="stub")
    html = _index_page(wb)
    for bad in ("espresso run-agent", "PYTHONPATH", ".yaml", "Traceback", ">stub<", ">external<", "provider="):
        assert bad not in html, bad


def test_run_page_main_path_hides_commands_and_enums(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    run_single_agent(run_dir, "examples")
    html = render_run_dashboard(run_dir, interactive=True)
    main_path = html.split('class="debug"')[0]
    assert "espresso run-agent --run" not in main_path
    assert "Traceback" not in html
    for enum in ("source_backed", "computation_checked", "human_needed", "idea_sketch", "likely_false"):
        # raw enums never appear outside the JSON island / debug drawer
        visible = main_path.split("<script>")[0]
        assert enum not in visible, enum


def test_interactive_buttons_acknowledge_via_busy_class(tmp_path: Path) -> None:
    """The click handlers add a .busy spinner class immediately (sub-200ms
    acknowledgement) and reset it on failure — no stale loading state."""
    run_dir = _run(tmp_path)
    html = render_run_dashboard(run_dir, interactive=True)
    assert 'classList.add("busy")' in html
    assert 'classList.remove("busy")' in html
    # elapsed-seconds feedback for long agent runs and the continue loop
    assert 'btn.textContent = "Running… " + secs + "s"' in html.replace("'", '"')
    assert "Still working" in html


def test_loading_copy_never_uses_earned_green_tone(tmp_path: Path) -> None:
    """In-progress flashes use the neutral 'info' tone — the earned-evidence
    green (.act-status.ok) is reserved for genuinely completed actions."""
    run_dir = _run(tmp_path)
    html = render_run_dashboard(run_dir, interactive=True)
    # the neutral tone exists and is muted, not green
    assert ".act-status.info{color:var(--muted)}" in html
    # every in-progress flash call uses "info", never true/"ok"
    assert '"Continuing…", "info"' in html
    assert 'flash(st, "Continuing…", true)' not in html
    assert '+ secs + "s", "info"' in html


def test_effort_pill_is_humanized(tmp_path: Path) -> None:
    """The selected-plan effort pill shows 'quick test', not the raw enum."""
    run_dir = _run(tmp_path)
    html = render_run_dashboard(run_dir, interactive=True)
    pill = html.split('id="selected-effort">')[1].split("<")[0]
    assert "_" not in pill
    # the JS keeps it humanized after route switches
    assert "humanEffort(route.effort)" in html