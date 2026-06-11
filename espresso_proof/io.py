from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


def read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_yaml(path: Path, data: Any) -> None:
    """Write YAML atomically: dump to a sibling temp file, then rename over the
    target. A crash mid-dump can no longer leave a truncated artifact behind —
    readers see either the old bytes or the complete new bytes, never a partial
    write (Goal 35B)."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=False)
        os.chmod(tmp_name, 0o644)  # mkstemp defaults to 0600; keep artifacts readable
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
