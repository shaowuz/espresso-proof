"""Goal 30 Information Architecture: the Now strip (trust-grouped evidence
summary + derived next action) and first-screen comprehension. Display-only —
nothing here may overclaim, hide missing evidence, or change kernel status."""

from __future__ import annotations

from pathlib import Path

from espresso_proof.agent_run_status import set_agent_status
from espresso_proof.agent_runner import run_single_agent, skip_agent
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.orchestrator import orchestrate_run, resume_run
from espresso_proof.run_dashboard import render_run_dashboard
from espresso_proof.validators import validate_run

CLAIM = "Every group of order p^2, where p is prime, is abelian."


def _run(tmp_path: Path, name: str = "ia") -> Path:
    claim = tmp_path / f"{name}.md"
    claim.write_text(CLAIM, encoding="utf-8")
    return orchestrate_run(
        project=name, input_path=claim, output_root=tmp_path,
        provider="stub", front_half="stub", fast=True,
    )["run_dir"]


def _now_section(html: str) -> str:
    return html.split('class="card now"')[1].split("</section>")[0]


# --------------------------------------------------------------------------- #
# trust-grouped evidence summary
# --------------------------------------------------------------------------- #


def test_now_strip_shows_trust_grouped_counts(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    html = render_run_dashboard(run_dir, interactive=True)
    now = _now_section(html)
    assert "checked in finite/computational scope" in now
    assert "reference-supported" in now
    assert "candidate source(s)" in now
    assert "evidence agent(s) not finished" in now


def test_zero_earned_evidence_is_never_green(tmp_path: Path) -> None:
    """A fresh run has no earned evidence: the checked/reference chips must be
    grey (count 0), never the earned-green tone (candidate never green)."""
    run_dir = _run(tmp_path)
    now = _now_section(render_run_dashboard(run_dir, interactive=True))
    assert "now-green" not in now
    # candidates, if any, are amber or grey — never green
    assert 'now-chip now-grey' in now


def test_candidate_sources_do_not_appear_as_checked(tmp_path: Path) -> None:
    """Candidate literature must raise the candidate count, not the checked or
    reference-supported counts (candidate is amber, not green)."""
    run_dir = _run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    lit_path = run_dir / manifest["artifacts"]["literature_review"]
    lit = read_yaml(lit_path)
    lit["candidate_references"] = [{
        "id": "lit_x", "title": "Some candidate paper", "authors": ["A. Author"],
        "locator": {"kind": "arxiv_id", "value": "2101.00001"},
        "relevance": "core", "status": "candidate",
        "why_relevant": "Possibly relevant.", "found_by": "literature_review",
    }]
    write_yaml(lit_path, lit)
    set_agent_status(run_dir, "literature", "done")
    validate_run(run_dir)
    now = _now_section(render_run_dashboard(run_dir, interactive=True))
    assert '<b>1</b> candidate source(s)' in now
    assert '<b>0</b> checked' in now
    assert '<b>0</b> reference-supported' in now
    # the candidate chip is amber; nothing is green
    assert "now-green" not in now


def test_missing_evidence_stays_visible_after_selecting_route(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    skip_agent(run_dir, "literature", reason="defer")
    brief = read_yaml(run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["strategy_brief"])
    sid = (brief.get("top_strategies") or [{}])[0].get("id") or "none"
    resume_run(run_dir=run_dir, selected_strategy=sid, effort="quick_test")
    validate_run(run_dir)
    now = _now_section(render_run_dashboard(run_dir, interactive=True))
    # skipped/not-run agents still counted as unfinished — selecting a route
    # never hides missing evidence
    assert "evidence agent(s) not finished" in now
    assert '<b>0</b> evidence agent(s) not finished' not in now


# --------------------------------------------------------------------------- #
# derived next action (honest, advisory)
# --------------------------------------------------------------------------- #


def test_next_action_suggests_running_missing_agents(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    now = _now_section(render_run_dashboard(run_dir, interactive=True))
    assert "Suggested next step" in now
    assert "Run the evidence agents" in now
    assert "Counterexamples" in now or "Examples" in now or "Literature" in now


def test_running_agent_never_yields_are_done(tmp_path: Path) -> None:
    """Mathematician-workflow blocker repro: a running counterexample search must
    yield a 'wait' suggestion — never 'the evidence agents are done', and never
    a suggestion to continue the route while the search is in flight."""
    run_dir = _run(tmp_path)
    set_agent_status(run_dir, "counterexamples", "running", increment_attempt=True)
    validate_run(run_dir)
    now = _now_section(render_run_dashboard(run_dir, interactive=True))
    assert "are done" not in now
    assert "Continue the selected route" not in now
    assert "still running" in now
    assert "Counterexamples" in now


def test_skipped_agents_without_selection_never_say_continue(tmp_path: Path) -> None:
    """Skipped-flow blocker repro: with every optional agent skipped/done and NO
    human route selection, the suggestion must be to choose a route — never
    'Continue the selected route'."""
    run_dir = _run(tmp_path)
    run_single_agent(run_dir, "examples")
    for agent in ("literature", "counterexamples", "failure_patterns"):
        skip_agent(run_dir, agent, reason="defer")
    validate_run(run_dir)
    now = _now_section(render_run_dashboard(run_dir, interactive=True))
    assert "Continue the selected route" not in now
    assert "Choose a route" in now
    # skips acknowledged, not silently treated as done
    assert "skipped" in now


def test_suggestion_never_contradicts_unfinished_chip(tmp_path: Path) -> None:
    """Whenever the strip counts unfinished agents > 0, the suggestion must not
    say the agents are done (consistency between chip and action)."""
    run_dir = _run(tmp_path)
    set_agent_status(run_dir, "literature", "queued")
    validate_run(run_dir)
    now = _now_section(render_run_dashboard(run_dir, interactive=True))
    assert "evidence agent(s) not finished" in now
    assert "<b>0</b> evidence agent(s) not finished" not in now
    assert "are done" not in now


def test_next_action_for_admissible_refutation_is_read_counterexample(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    ce_path = run_dir / "counterexamples.yaml"
    ce = read_yaml(ce_path)
    ce["tests"] = [{
        "id": "cx", "operator_id": "op", "example_id": "ex",
        "satisfies_all_claim_hypotheses": True, "target": "theorem",
        "result": "failed", "refutes_claim": True, "refutes_step_only": False,
        "exposes_missing_hypothesis": False,
        "explanation": "An admissible checked counterexample.",
    }]
    write_yaml(ce_path, ce)
    set_agent_status(run_dir, "counterexamples", "done")
    validate_run(run_dir)
    now = _now_section(render_run_dashboard(run_dir, interactive=True))
    assert "Read the counterexample" in now
    assert "Continue the selected route" not in now


def test_route_only_attack_does_not_trigger_refutation_action(tmp_path: Path) -> None:
    """A proof-step-only attack must not produce the refutation next-action."""
    run_dir = _run(tmp_path)
    ce_path = run_dir / "counterexamples.yaml"
    ce = read_yaml(ce_path)
    ce["tests"] = [{
        "id": "cx2", "operator_id": "op", "example_id": "ex",
        "satisfies_all_claim_hypotheses": True, "target": "proof_step",
        "result": "failed", "refutes_claim": False, "refutes_step_only": True,
        "exposes_missing_hypothesis": False,
        "explanation": "Attacks one proof step only.",
    }]
    write_yaml(ce_path, ce)
    validate_run(run_dir)
    now = _now_section(render_run_dashboard(run_dir, interactive=True))
    assert "Read the counterexample" not in now


# --------------------------------------------------------------------------- #
# first-screen acceptance + consistency
# --------------------------------------------------------------------------- #


def test_first_screen_acceptance_elements_present(tmp_path: Path) -> None:
    """Claim, state, route, agent status, next action all render; no command
    block outside the debug drawer (spec first-screen checklist)."""
    run_dir = _run(tmp_path)
    html = render_run_dashboard(run_dir, interactive=True)
    assert 'class="claim"' in html or "Claim &middot;" in html
    assert "Waiting" in html  # state chip
    assert 'aria-pressed="true"' in html  # a route is highlighted
    assert "Evidence Agents" in html
    assert "Suggested next step" in html
    main_path = html.split('class="debug"')[0]
    assert "espresso run-agent --run" not in main_path


def test_selected_route_and_route_list_agree(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    html = render_run_dashboard(run_dir, interactive=True)
    pressed_block = html.split('aria-pressed="true"')[1]
    pressed_title = pressed_block.split("<b>")[1].split("</b>")[0]
    plan_title = html.split('id="selected-title">')[1].split("<")[0]
    assert pressed_title == plan_title


def test_single_canonical_evidence_summary(tmp_path: Path) -> None:
    """The duplicated 'Run evidence so far' plan line is gone — the Now strip is
    the one canonical evidence summary (no contradictory counts)."""
    run_dir = _run(tmp_path)
    run_single_agent(run_dir, "examples")
    html = render_run_dashboard(run_dir, interactive=True)
    assert "Run evidence so far" not in html
    assert html.count("checked in finite/computational scope") == 1