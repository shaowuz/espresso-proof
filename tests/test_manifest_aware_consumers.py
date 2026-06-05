"""Manifest-Aware Bank Consumers V0.

Every user-facing/runtime consumer of bankable artifacts (examples,
counterexamples, strategy synthesis) reads through the manifest + bank-aware
loader, so a run converted by `materialize-banks` works exactly like a
single-YAML run: inspect, memo, memory, retrieval planning, feedback, dashboard.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.bank_dashboard import render_bank_dashboard
from espresso_proof.cli import app
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.literature_planner import _route_titles
from espresso_proof.llm_agents import load_agent_context
from espresso_proof.memory import write_run_memory
from espresso_proof.research_bank import (
    load_bankable_artifact,
    load_bankable_dict,
    materialize_run_banks,
)
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validators import validate_run

FIXTURES = Path("tests/fixtures")
_FORBIDDEN = ("proof-close", "proof complete", "we proved", "proved the claim")


def _single_run(tmp_path: Path, name: str = "single") -> Path:
    return run_mock_critique(
        "hodge_atoms", FIXTURES / "hodge_all_cubic_fourfolds" / "input.md", tmp_path / name
    )


def _bank_run(tmp_path: Path, name: str = "bank") -> Path:
    run_dir = _single_run(tmp_path, name)
    materialize_run_banks(run_dir)
    validate_run(run_dir)
    return run_dir


def _both(tmp_path: Path) -> list[Path]:
    return [_single_run(tmp_path), _bank_run(tmp_path)]


def _digest(run_dir: Path) -> dict[str, str]:
    return {
        str(p.relative_to(run_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(run_dir.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------------------
# 1-2) inspect on single-YAML and bank-directory runs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["single", "bank"])
def test_inspect_works_on_both_layouts(tmp_path: Path, layout: str) -> None:
    run_dir = _single_run(tmp_path) if layout == "single" else _bank_run(tmp_path)
    result = CliRunner().invoke(app, ["inspect", "--run", str(run_dir)])
    assert result.exit_code == 0, result.output
    assert "Run is valid" in result.output
    lower = result.output.lower()
    for phrase in _FORBIDDEN:
        assert phrase not in lower


# ---------------------------------------------------------------------------
# 3-4) memo on single-YAML and bank-directory runs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["single", "bank"])
def test_memo_works_on_both_layouts(tmp_path: Path, layout: str) -> None:
    run_dir = _single_run(tmp_path) if layout == "single" else _bank_run(tmp_path)
    result = CliRunner().invoke(app, ["memo", "--run", str(run_dir)])
    assert result.exit_code == 0, result.output
    assert "ESPRESSO MEMO" in result.output


def test_memo_identical_across_layouts(tmp_path: Path) -> None:
    single = CliRunner().invoke(app, ["memo", "--run", str(_single_run(tmp_path))]).output
    bank = CliRunner().invoke(app, ["memo", "--run", str(_bank_run(tmp_path))]).output
    # memo content is layout-independent (it reads no bankable artifact directly).
    assert single == bank


# ---------------------------------------------------------------------------
# 5) memory write path consumes counterexamples on both layouts.
# ---------------------------------------------------------------------------


def _inject_counterexample_pressure(run_dir: Path) -> None:
    ce = read_yaml(run_dir / "counterexamples.yaml")
    if ce["tests"]:
        ce["tests"][0]["refutes_step_only"] = True
        ce["tests"][0]["refutes_claim"] = False
    write_yaml(run_dir / "counterexamples.yaml", ce)


def test_memory_write_consumes_counterexamples_on_both_layouts(tmp_path: Path) -> None:
    def memory_types(run_dir: Path) -> set[str]:
        mem_dir = tmp_path / f"mem_{run_dir.parent.parent.name}_{run_dir.name}"
        written = write_run_memory(
            mem_dir, run_dir, project_id="hodge_atoms", run_id=run_dir.name
        )
        return {read_yaml(p)["type"] for p in written}

    single = _single_run(tmp_path, "mem_single")
    _inject_counterexample_pressure(single)
    validate_run(single)
    bank = _single_run(tmp_path, "mem_bank")
    _inject_counterexample_pressure(bank)
    materialize_run_banks(bank)
    validate_run(bank)

    types_single = memory_types(single)
    types_bank = memory_types(bank)
    # the bank run still produces the counterexample-pressure memory category.
    assert "counterexample" in types_single
    assert types_single == types_bank


# ---------------------------------------------------------------------------
# 6) retrieval planning + feedback consumers on both layouts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["single", "bank"])
def test_route_titles_consumes_strategies_on_both_layouts(tmp_path: Path, layout: str) -> None:
    run_dir = _single_run(tmp_path) if layout == "single" else _bank_run(tmp_path)
    titles = _route_titles(run_dir)
    assert titles  # strategy route titles are surfaced for the search plan


@pytest.mark.parametrize("layout", ["single", "bank"])
def test_agent_context_consumes_examples_and_counterexamples(tmp_path: Path, layout: str) -> None:
    run_dir = _single_run(tmp_path) if layout == "single" else _bank_run(tmp_path)
    ctx = load_agent_context(run_dir)
    assert "examples" in ctx["examples"]
    assert "tests" in ctx["counterexamples"]


@pytest.mark.parametrize("layout", ["single", "bank"])
def test_feedback_example_consumes_examples_on_both_layouts(tmp_path: Path, layout: str) -> None:
    run_dir = _single_run(tmp_path) if layout == "single" else _bank_run(tmp_path)
    target = load_bankable_dict(run_dir, "examples")["examples"][0]["id"]
    result = CliRunner().invoke(
        app,
        ["feedback-example", "--run", str(run_dir), "--target", target,
         "--label", "interesting", "--reason", "regression test"],
    )
    assert result.exit_code == 0, result.output
    assert (run_dir / "human_example_feedback.yaml").exists()


# ---------------------------------------------------------------------------
# 7) dashboard on both layouts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["single", "bank"])
def test_dashboard_works_on_both_layouts(tmp_path: Path, layout: str) -> None:
    run_dir = _single_run(tmp_path) if layout == "single" else _bank_run(tmp_path)
    html_text = render_bank_dashboard(run_dir)
    assert "Research Bank Dashboard" in html_text
    for phrase in _FORBIDDEN:
        assert phrase not in html_text.lower()


# ---------------------------------------------------------------------------
# 8) consumers do not mutate artifacts.
# ---------------------------------------------------------------------------


def test_read_consumers_do_not_mutate_artifacts(tmp_path: Path) -> None:
    run_dir = _bank_run(tmp_path)
    before = _digest(run_dir)
    CliRunner().invoke(app, ["inspect", "--run", str(run_dir)])
    CliRunner().invoke(app, ["memo", "--run", str(run_dir)])
    render_bank_dashboard(run_dir)
    load_agent_context(run_dir)
    _route_titles(run_dir)
    assert _digest(run_dir) == before


# ---------------------------------------------------------------------------
# 9) no status upgrades when switching layouts.
# ---------------------------------------------------------------------------


def test_no_status_change_between_layouts(tmp_path: Path) -> None:
    # Same run before vs after materialization (same run_id), so any difference
    # would be a real status/content change introduced by the layout switch.
    run_dir = _single_run(tmp_path)
    before = {k: load_bankable_dict(run_dir, k) for k in ("examples", "counterexamples", "strategy_synthesis")}
    grade_before = read_yaml(run_dir / "idea_grade.yaml")
    materialize_run_banks(run_dir)
    validate_run(run_dir)
    for key, value in before.items():
        assert load_bankable_dict(run_dir, key) == value
    assert read_yaml(run_dir / "idea_grade.yaml") == grade_before


# ---------------------------------------------------------------------------
# 10) missing required artifact still fails closed.
# ---------------------------------------------------------------------------


def test_missing_bankable_artifact_fails_closed(tmp_path: Path) -> None:
    run_dir = _single_run(tmp_path)
    (run_dir / "examples.yaml").unlink()  # manifest still points at it
    with pytest.raises((KernelValidationError, FileNotFoundError, OSError)):
        load_bankable_artifact(run_dir, "examples")
    # inspect (a consumer) also fails closed rather than fabricating an empty bank.
    result = CliRunner().invoke(app, ["inspect", "--run", str(run_dir)])
    assert result.exit_code != 0
