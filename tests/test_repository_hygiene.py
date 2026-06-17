from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_codebase_spine_exists_with_core_sections() -> None:
    text = (REPO / "docs" / "engineering" / "CODEBASE_SPINE.md").read_text(
        encoding="utf-8"
    )
    for heading in (
        "## User Entry",
        "## Orchestration",
        "## Trust Boundary",
        "## Artifacts",
        "## UI",
        "## Testing Policy",
    ):
        assert heading in text
    for lane in ("quick", "focused", "full"):
        assert f"`{lane}`" in text


def test_root_has_single_current_state_entry() -> None:
    for stale in ("RUN_LOG.md", "RUN_STATUS.md", "NEXT_ACTIONS.md", "INBOX.md"):
        assert not (REPO / stale).exists(), f"stale root status file returned: {stale}"
    assert (REPO / "CURRENT_STATE_FOR_NEW_CONTEXT.md").is_file()


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
