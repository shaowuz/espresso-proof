"""Goal 08: agentic loop integration.

The single `orchestrate` command runs the bounded workflow (front-half -> prover
loop -> mathematical judgment -> failure-pattern audit -> memo) and can now also
run the literature scout and auto-ground a local corpus in the same command. Also
pins a regression: the `retrieve-and-ground` CLI command must resolve to the Goal
03 function (an import-name collision previously shadowed it).
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")
CLAIM = "If G / Z(G) is cyclic, then G is abelian."


def _fresh(runner: CliRunner, tmp_path: Path) -> Path:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(CLAIM, encoding="utf-8")
    res = runner.invoke(app, [
        "orchestrate", "--input", str(claim_path), "--project", "agent_runtime_demo",
        "--provider", "stub", "--front-half", "stub", "--output-root", str(tmp_path),
    ])
    assert res.exit_code == 0, res.output
    return Path(res.output.splitlines()[0].split("Wrote run artifacts to ")[-1])


def _first_resume_choice(run_dir: Path) -> tuple[str, str]:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(run_dir / manifest["artifacts"]["strategy_brief"])
    strategy_id = brief["top_strategies"][0]["id"]
    choices = {
        choice["strategy_id"]: choice.get("effort_options") or []
        for choice in brief.get("allowed_human_choices", [])
    }
    return strategy_id, (choices.get(strategy_id) or ["quick_test"])[0]


def _mark_claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)


def test_retrieve_and_ground_cli_command_resolves_goal03_function(tmp_path: Path) -> None:
    """Regression: the retrieve-and-ground command must call the Goal 03 function
    (run_dir/local_corpus kwargs), not the shadowing literature_retrieval one."""
    run_dir = run_mock_critique("ag", FIXTURES / "vague_claim_unclear" / "input.md", tmp_path)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "note.txt").write_text("Theorem 1.1. An exact statement.", encoding="utf-8")
    _mark_claim_clear(run_dir)
    res = CliRunner().invoke(app, [
        "retrieve-and-ground", "--run", str(run_dir), "--local-corpus", str(corpus),
        "--source-title", "Notes", "--source-author", "A. Author",
    ])
    assert res.exit_code == 0, res.output
    assert "Retrieval resolution:" in res.output
    validate_run(run_dir)


def test_orchestrate_one_command_loop_with_scout_and_auto_ground(tmp_path: Path) -> None:
    runner = CliRunner()
    run_dir = _fresh(runner, tmp_path)
    strategy_id, effort = _first_resume_choice(run_dir)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "src.txt").write_text(
        "Theorem 1.1. Let G be a finite group; then G has a composition series.",
        encoding="utf-8",
    )
    resumed = runner.invoke(app, [
        "orchestrate", "--resume", str(run_dir), "--provider", "stub",
        "--select-strategy", strategy_id, "--effort", effort,
        "--literature-scout", "--auto-ground", str(corpus),
    ])
    assert resumed.exit_code == 0, resumed.output
    assert "Literature scout:" in resumed.output
    assert "Auto-ground (local-first):" in resumed.output
    assert "Claim [locked]" in resumed.output
    # New artifacts produced by the single command, and the run stays valid.
    assert (run_dir / "literature_candidates.yaml").exists()
    assert (run_dir / "retrieval_resolution.yaml").exists()
    validate_run(run_dir)
    # No proof-complete language leaked into the memo.
    out = resumed.output.lower()
    for phrase in ("proof-close", "proof complete", "we proved", "this establishes the theorem"):
        assert phrase not in out
