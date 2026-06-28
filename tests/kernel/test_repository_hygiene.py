from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def test_codebase_spine_exists_with_core_sections() -> None:
    text = (REPO / "docs" / "engineering" / "codebase-spine.md").read_text(
        encoding="utf-8"
    )
    for heading in (
        "## entrypoints",
        "## package layout",
        "## trust boundary",
        "## local checks",
    ):
        assert heading in text
    for command in ("uv run espresso export-schemas", "uv run python -m pytest -q"):
        assert command in text


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
    assert (REPO / "docs" / "engineering" / "agent-flow.md").is_file()


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
