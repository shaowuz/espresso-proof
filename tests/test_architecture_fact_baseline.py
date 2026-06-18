"""Phase 00 fact-baseline guard.

A deliberately small, low-churn check that keeps the architecture fact baseline
(docs/status/ESPRESSO_ARCHITECTURE_FACT_BASELINE.md) honest as the cleanup
proceeds. It only makes POSITIVE, stable assertions (a required doc exists and
states the source-of-truth boundary; foundational root modules and the canonical
runtime entrypoint exist). It intentionally does NOT assert the absence of
planned modules, so later phases that add them do not have to fight this test.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = REPO_ROOT / "docs" / "status" / "ESPRESSO_ARCHITECTURE_FACT_BASELINE.md"


def test_fact_baseline_exists_and_states_source_of_truth() -> None:
    assert BASELINE.is_file(), "Phase 00 fact baseline is missing"
    text = BASELINE.read_text(encoding="utf-8").lower()
    # The baseline must pin root as the active implementation source of truth...
    assert "source of truth" in text
    assert "espresso_proof/" in text and "tests/" in text
    # ...and explicitly call the run/report trees reference-only.
    assert "reference material only" in text
    assert "espresso-proof-run/" in text


def test_foundational_root_modules_exist() -> None:
    # These are the load-bearing runtime/safety modules the cleanup builds on;
    # they must remain present in the root package.
    for module in (
        "cli.py",
        "orchestration/orchestrator.py",
        "agents/front_half_agents.py",
        "orchestration/prover_loop_driver.py",
        "agents/llm_agents.py",
        "kernel/validators.py",
        "kernel/models.py",
        "memory/memory.py",
    ):
        assert (REPO_ROOT / "espresso_proof" / module).is_file(), f"missing root module {module}"


def test_canonical_orchestrate_entrypoint_registered() -> None:
    # The canonical runtime entrypoint must stay registered in the CLI (checked by
    # source text so this does not depend on importing the package).
    cli_text = (REPO_ROOT / "espresso_proof" / "cli.py").read_text(encoding="utf-8")
    assert '@app.command("orchestrate")' in cli_text
