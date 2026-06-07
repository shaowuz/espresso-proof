"""Local logging helpers for external Espresso agent adapters."""

from __future__ import annotations

import json
import logging
import os
import shlex
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_LOG_DIR = ".espresso_agent_logs"
EVENTS_LOG = "codex_agent_events.jsonl"
PROMPTS_LOG = "codex_agent_prompts.log"


def resolve_agent_log_dir(*, cwd: Path | None = None) -> Path:
    raw = os.environ.get("ESPRESSO_AGENT_LOG_DIR", DEFAULT_LOG_DIR)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (cwd or Path.cwd()) / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def shell_preview(argv: list[str]) -> str:
    return shlex.join(argv)


def log_event(log_dir: Path, event: dict[str, Any]) -> None:
    payload = dict(event)
    payload.setdefault("timestamp", utc_timestamp())
    _write_line(
        path=log_dir / EVENTS_LOG,
        logger_name="espresso.agent.events",
        line=json.dumps(payload, sort_keys=True, ensure_ascii=False),
    )


def log_prompt_record(log_dir: Path, record: dict[str, Any]) -> None:
    timestamp = record.get("timestamp") or utc_timestamp()
    lines = [
        "=" * 80,
        f"timestamp: {timestamp}",
        f"event: {record.get('event', '')}",
        f"request_id: {record.get('request_id', '')}",
        f"stage: {record.get('stage', '')}",
        f"skills: {', '.join(record.get('skills', []))}",
        f"goal_file: {record.get('goal_file') or 'none'}",
        f"working_directory: {record.get('working_directory', '')}",
        f"model: {record.get('model', '')}",
        f"effort: {record.get('effort', '')}",
        "command_argv:",
        json.dumps(record.get("command_argv", []), indent=2, ensure_ascii=False),
        "command_preview:",
        str(record.get("command_preview", "")),
        "system_prompt:",
        str(record.get("system_prompt", "")),
        "user_prompt:",
        str(record.get("user_prompt", "")),
        "injected_goal:",
        str(record.get("injected_goal", "")),
        "injected_skill_text:",
        str(record.get("injected_skill_text", "")),
        "result:",
        json.dumps(record.get("result", {}), indent=2, ensure_ascii=False),
        "",
    ]
    _write_line(
        path=log_dir / PROMPTS_LOG,
        logger_name="espresso.agent.prompts",
        line="\n".join(lines),
    )


def _write_line(*, path: Path, logger_name: str, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    try:
        logger.info(line)
    finally:
        logger.removeHandler(handler)
        handler.close()
