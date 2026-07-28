from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


def _tmp_prefix(path: Path) -> str:
    """Keep atomic-write temp names short enough for deep Windows run paths."""

    return f".{path.stem[:24]}."


def _os_path(path: Path | str) -> str:
    """Return an OS path string that tolerates deep run directories on Windows."""

    raw = os.fspath(path)
    if os.name != "nt":
        return raw
    absolute = os.path.abspath(raw)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute.lstrip("\\")
    return "\\\\?\\" + absolute


def read_yaml(path: Path) -> Any:
    with open(_os_path(path), "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_yaml(path: Path, data: Any) -> None:
    """Write YAML atomically: dump to a sibling temp file, then rename over the
    target. A process crash mid-dump can no longer leave a truncated artifact —
    Readers see either the old bytes or the complete new bytes.

    Scope note: atomic against process crash and concurrent readers, not
    against power loss (no fsync of the file or directory). After power loss a
    stale or missing target is possible; ``validate_run`` then fails closed."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=_tmp_prefix(path), suffix=".tmp", dir=_os_path(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=False)
        os.chmod(tmp_name, 0o644)  # mkstemp defaults to 0600; keep artifacts readable
        os.replace(tmp_name, _os_path(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_text(path: Path) -> str:
    with open(_os_path(path), "r", encoding="utf-8") as handle:
        return handle.read()


def read_bytes(path: Path | str) -> bytes:
    with open(_os_path(path), "rb") as handle:
        return handle.read()


def path_exists(path: Path | str) -> bool:
    return os.path.exists(_os_path(path))


def path_is_file(path: Path | str) -> bool:
    return os.path.isfile(_os_path(path))


def list_files_with_suffix(directory: Path | str, suffix: str) -> list[Path]:
    directory = Path(directory)
    if not path_exists(directory):
        return []
    names = sorted(os.listdir(_os_path(directory)))
    return [
        directory / name
        for name in names
        if name.endswith(suffix) and path_is_file(directory / name)
    ]


def unlink_path(path: Path | str) -> None:
    os.unlink(_os_path(path))


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 text atomically with the same sibling-temp rename pattern as YAML."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=_tmp_prefix(path), suffix=".tmp", dir=_os_path(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, _os_path(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_bytes(path: Path | str, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=_tmp_prefix(path), suffix=".tmp", dir=_os_path(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, _os_path(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
