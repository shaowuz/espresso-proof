from __future__ import annotations

from pathlib import Path

import pytest
import typer

from espresso_proof import cli, cli_shared


def test_windows_run_path_guard_fails_before_deep_run_creation(monkeypatch) -> None:
    output_root = Path("C:/very/long") / ("nested" * 30)
    monkeypatch.setattr(cli_shared.os, "name", "nt", raising=False)

    with pytest.raises(typer.BadParameter, match="MAX_PATH"):
        cli._guard_windows_run_path_length(
            output_root,
            "project_with_a_long_name",
        )


def test_windows_run_path_guard_allows_short_paths(monkeypatch) -> None:
    output_root = Path("C:/tmp/runs")
    monkeypatch.setattr(cli_shared.os, "name", "nt", raising=False)

    cli._guard_windows_run_path_length(output_root, "p")
