"""Pivot PR2: same-process auto-continue past the prover human gate.

``orchestrate_run(auto_continue=True)`` may continue past a PENDING prover
human gate by selecting the top-scored strategy — but ONLY with honest
provenance (AUTO_CHOICE_CONTINUE_SELECTED, selected_by/resolved_by='auto',
human_confirmed=false) and ONLY from that gate: any earlier human question
(claim clarification, ask_human) still stops the run. The picking rule never
widens what a human could have chosen.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.kernel.io import read_yaml
from espresso_proof.kernel.validators import validate_run
from espresso_proof.orchestration.orchestrator import (
    _auto_continue_from_gate,
    _pick_auto_choice,
    auto_select_strategy,
    orchestrate_run,
    write_dashboard_and_return_run_summary,
)

CLEAR_CLAIM = "Every group of order p^2, where p is prime, is abelian."
UNCLEAR_CLAIM = "The Mathieu group M23 is realizable as a Galois group over Q."


def _orchestrate(tmp_path: Path, claim_text: str, name: str, **kwargs) -> dict:
    claim_path = tmp_path / f"{name}.md"
    claim_path.write_text(claim_text, encoding="utf-8")
    return orchestrate_run(
        project="agent_runtime_demo",
        input_path=claim_path,
        output_root=tmp_path,
        provider="stub",
        front_half=kwargs.pop("front_half", "mock"),
        **kwargs,
    )


def _brief(**overrides) -> dict:
    brief = {
        "top_strategies": [
            {"id": "strategy_a", "score": 40, "estimated_effort": "quick_test"},
            {"id": "strategy_b", "score": 70, "estimated_effort": "normal_pass"},
            {"id": "strategy_c", "score": 70, "estimated_effort": "deep_dive"},
        ],
        "allowed_human_choices": [
            {"strategy_id": "strategy_a", "effort_options": ["quick_test"]},
            {"strategy_id": "strategy_b", "effort_options": ["quick_test", "normal_pass"]},
            {"strategy_id": "strategy_c", "effort_options": ["quick_test"]},
            {"strategy_id": "none", "effort_options": ["quick_test"]},
        ],
    }
    brief.update(overrides)
    return brief


# ---------------------------------------------------------------- picking rule


def test_pick_auto_choice_takes_top_score_with_offered_effort() -> None:
    choice = _pick_auto_choice(_brief())
    # strategy_b and strategy_c tie at 70; brief order breaks the tie.
    assert choice["selected_strategy"] == "strategy_b"
    assert choice["effort"] == "normal_pass"  # estimated_effort, offered
    assert choice["score"] == 70


def test_pick_auto_choice_falls_back_to_first_offered_effort() -> None:
    # strategy_c wins alone; its estimated_effort (deep_dive) is NOT offered,
    # so the pick falls back to the first offered option instead of inventing.
    brief = _brief()
    brief["top_strategies"] = [
        {"id": "strategy_c", "score": 90, "estimated_effort": "deep_dive"}
    ]
    choice = _pick_auto_choice(brief)
    assert choice["selected_strategy"] == "strategy_c"
    assert choice["effort"] == "quick_test"


def test_pick_auto_choice_never_widens_the_human_menu() -> None:
    # A top strategy missing from allowed_human_choices is NOT eligible, even
    # with the best score — auto can never choose what a human could not.
    brief = _brief()
    brief["top_strategies"].append({"id": "strategy_hidden", "score": 99})
    choice = _pick_auto_choice(brief)
    assert choice["selected_strategy"] == "strategy_b"


def test_pick_auto_choice_empty_brief_stops_honestly() -> None:
    choice = _pick_auto_choice(_brief(top_strategies=[]))
    assert choice["selected_strategy"] == "none"
    assert choice["effort"] == "quick_test"
    assert choice["score"] is None


# ------------------------------------------------------- same-process continue


def test_auto_continue_reaches_memo_with_honest_provenance(tmp_path: Path) -> None:
    result = _orchestrate(tmp_path, CLEAR_CLAIM, "clear", auto_continue=True)
    run_dir = Path(result["run_dir"])
    assert result["auto_continue"]["attempted"] is True
    assert result["auto_continue"]["selected_by"] == "auto"
    assert result["auto_continue"]["decision"] == "continue"
    assert "memo" in result
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["current_state"] == "COMPLETED_MEMO"
    validate_run(run_dir)

    # the persisted artifacts record machinery, never a human:
    human_choice = read_yaml(run_dir / manifest["artifacts"]["human_choice"])
    assert human_choice["selected_by"] == "auto"
    assert human_choice["human_confirmed"] is False
    agent_run = read_yaml(run_dir / "llm_agent_run.yaml")
    assert agent_run["human_gate"]["resolved_by"] == "auto"
    assert agent_run["human_gate"]["human_confirmed"] is False
    # the auto pick matches what auto_select_strategy reports for this brief
    assert human_choice["selected_strategy"] == result["auto_continue"]["selected_strategy"]
    # the locked claim's own confirmation is untouched (gate != claim confirmation)
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    assert claim["human_confirmed"] is False

    # the honest state was written (and logged) on the way to COMPLETED_MEMO
    events = (run_dir / "events.jsonl").read_text(encoding="utf-8") if (
        run_dir / "events.jsonl"
    ).exists() else ""
    assert "auto_choice_selected" in events


def test_auto_continue_never_answers_an_earlier_human_question(tmp_path: Path) -> None:
    # An unclear claim stops at claim clarification BEFORE the prover gate;
    # auto_continue must not answer that human question.
    result = _orchestrate(
        tmp_path, UNCLEAR_CLAIM, "unclear", front_half="stub", auto_continue=True
    )
    run_dir = Path(result["run_dir"])
    assert result["state"] == "WAITING_CLAIM_CLARIFICATION"
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["current_state"] == "WAITING_CLAIM_CLARIFICATION"
    # no auto selection was recorded
    auto_report = result.get("auto_continue")
    assert auto_report is None or auto_report["attempted"] is False
    validate_run(run_dir)


def test_auto_continue_fails_closed_when_gate_already_resolved(tmp_path: Path) -> None:
    first = _orchestrate(tmp_path, CLEAR_CLAIM, "clear", auto_continue=True)
    run_dir = Path(first["run_dir"])
    summary = write_dashboard_and_return_run_summary(run_dir=run_dir)
    again = _auto_continue_from_gate(
        run_dir=run_dir,
        summary=summary,
        provider="stub",
        model="gpt-4.1",
        api_key_env="OPENAI_API_KEY",
        external_agent_command_env="ESPRESSO_EXTERNAL_AGENT_COMMAND",
        reviewer_set="espresso",
        rounds=None,
    )
    # the gate is already resolved: nothing to auto-continue, run untouched.
    assert again["auto_continue"]["attempted"] is False
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["current_state"] == "COMPLETED_MEMO"
    validate_run(run_dir)


def test_auto_select_strategy_reads_the_run_brief(tmp_path: Path) -> None:
    result = _orchestrate(tmp_path, CLEAR_CLAIM, "clear")
    run_dir = Path(result["run_dir"])
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(run_dir / manifest["artifacts"]["strategy_brief"])
    choice = auto_select_strategy(run_dir=run_dir)
    assert choice == _pick_auto_choice(brief)


# --------------------------------------------------------------------- CLI


def test_cli_resume_auto_continue(tmp_path: Path) -> None:
    result = _orchestrate(tmp_path, CLEAR_CLAIM, "clear")
    run_dir = Path(result["run_dir"])
    runner = CliRunner()
    outcome = runner.invoke(
        app, ["orchestrate", "--resume", str(run_dir), "--auto-continue"]
    )
    assert outcome.exit_code == 0, outcome.output
    assert "selected_by=auto" in outcome.output
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["current_state"] == "COMPLETED_MEMO"
    human_choice = read_yaml(run_dir / manifest["artifacts"]["human_choice"])
    assert human_choice["selected_by"] == "auto"
    assert human_choice["human_confirmed"] is False


def test_cli_rejects_select_strategy_with_auto_continue(tmp_path: Path) -> None:
    result = _orchestrate(tmp_path, CLEAR_CLAIM, "clear")
    runner = CliRunner()
    outcome = runner.invoke(
        app,
        [
            "orchestrate",
            "--resume",
            str(result["run_dir"]),
            "--auto-continue",
            "--select-strategy",
            "strategy_a",
        ],
    )
    assert outcome.exit_code != 0


def test_cli_resume_still_requires_a_choice(tmp_path: Path) -> None:
    result = _orchestrate(tmp_path, CLEAR_CLAIM, "clear")
    runner = CliRunner()
    outcome = runner.invoke(app, ["orchestrate", "--resume", str(result["run_dir"])])
    assert outcome.exit_code != 0
