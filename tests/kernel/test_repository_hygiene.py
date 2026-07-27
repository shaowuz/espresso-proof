from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_runtime_dataflow_is_expressed_in_python_entrypoints() -> None:
    orchestrator = (
        REPO / "espresso_proof" / "orchestration" / "orchestrator.py"
    ).read_text(encoding="utf-8")
    agent_pass = (REPO / "espresso_proof" / "agents" / "agent_pass.py").read_text(
        encoding="utf-8"
    )
    prover_loop = (REPO / "espresso_proof" / "agents" / "prover_loop.py").read_text(
        encoding="utf-8"
    )
    for name in (
        "build_front_half_context_artifacts",
        "stop_if_claim_is_not_clear",
        "collect_candidate_literature",
        "execute_scoped_machine_checks",
        "run_initial_prover_reviewer_round_to_human_gate",
        "write_initial_prover_reviewer_artifacts",
    ):
        assert f"def {name}" in orchestrator
    for name in (
        "ask_prover_for_candidate_route",
        "ask_reviewers_to_attack_candidate_route",
        "assemble_llm_agent_run_report",
    ):
        assert f"def {name}" in agent_pass
    for name in ("build_next_prover_input_from_reviewer_feedback",):
        assert f"def {name}" in prover_loop


def test_removed_map_docs_do_not_replace_runtime_code_structure() -> None:
    for stale in (
        "architecture-spine.md",
        "agent-catalog.md",
        "agent-flow.md",
        "codebase-spine.md",
    ):
        assert not (REPO / "docs" / "engineering" / stale).exists()


def test_root_has_single_current_state_entry() -> None:
    for stale in (
        "RUN_LOG.md",
        "RUN_STATUS.md",
        "NEXT_ACTIONS.md",
        "INBOX.md",
        "CURRENT_STATE_FOR_NEW_CONTEXT.md",
        "AGENT_FLOW.md",
        "PRODUCT_PHILOSOPHY.md",
    ):
        assert not (REPO / stale).exists(), f"stale root status file returned: {stale}"
    assert (REPO / "docs" / "product-philosophy.md").is_file()


def test_local_artifact_outputs_are_ignored() -> None:
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        ".eval_runs/",
        "eval_runs/",
        ".espresso_agent_logs/",
        "projects/*/runs/",
        "runs/",
        "reports/",
        "tmp/",
        "espresso-proof-artifacts/",
    ):
        assert pattern in ignore


SKILL_CENTRIC_SOURCE_COMMIT = "32462abccc40d4aa0e78105cf2cd1f345c456c5e"


def test_claude_skill_comparison_is_inert_reference_material() -> None:
    readme = (comparison / "README.md").read_text(encoding="utf-8")
    assert SKILL_CENTRIC_SOURCE_COMMIT in readme
    for phrase in (
        "not the current product runtime",
        "not an installed Claude skill or Codex skill",
        "must not import",
        "Do not merge this directory wholesale",
    ):
        assert phrase in readme, f"comparison README lost its boundary claim: {phrase}"
    assert not (REPO / ".claude" / "skills" / "espresso-proof").exists()


def test_product_code_does_not_reference_the_comparison_directory() -> None:
    sources = list((REPO / "espresso_proof").rglob("*.py"))
    for suffix in ("*.ts", "*.tsx", "*.js", "*.jsx"):
        sources.extend((REPO / "web" / "src").rglob(suffix))
    assert sources, "no product sources found to scan"
    for source in sources:
        text = source.read_text(encoding="utf-8")
        assert COMPARISON_DIR not in text, (
            f"{source.relative_to(REPO)} references the inert comparison directory"
        )
