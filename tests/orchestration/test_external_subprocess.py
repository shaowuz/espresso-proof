"""Goal 65C-1: external agent subprocess process-group cleanup + typed timeout.

The order-3 postmortem's prime suspect for the unbounded hang was that
``subprocess.run(timeout=)`` kills only the direct child — a forking/hung
external CLI survives as an orphan and blocks the worker thread. The client now
runs the agent in its own process group (``start_new_session=True``) and kills
the WHOLE group with ``os.killpg`` on timeout, raising a typed
``ExternalAgentTimeoutError`` carrying the timeout so callers record metadata.
"""

from __future__ import annotations

import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from espresso_proof.agents.llm_agents import ExternalAgentTimeoutError, ExternalCommandClient


def _set_cmd(monkeypatch, script: Path) -> None:
    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_COMMAND", f"{sys.executable} {script}")


def test_external_timeout_kills_whole_process_group(tmp_path: Path, monkeypatch):
    """A forking child that ignores SIGTERM is still reclaimed at timeout because
    the whole process group is SIGKILLed."""
    pidfile = tmp_path / "child.pid"
    script = tmp_path / "agent.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, sys, time, signal
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            pid = os.fork()
            if pid == 0:
                # forked child: ignore SIGTERM, record pid, then sleep long
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                open({str(pidfile)!r}, "w").write(str(os.getpid()))
                time.sleep(120)
                sys.exit(0)
            time.sleep(120)
            """
        ),
        encoding="utf-8",
    )
    _set_cmd(monkeypatch, script)

    # max_retries=0 pins the ORIGINAL single-attempt process-group semantics
    # (the bounded-retry layer is tested separately below).
    client = ExternalCommandClient(model="x", timeout=1, max_retries=0)
    with pytest.raises(ExternalAgentTimeoutError) as ei:
        client.complete_json(system="s", prompt="p")
    assert ei.value.timeout_seconds == 1

    # the forked child recorded its pid; after killpg it must be gone (SIGKILL to
    # the group cannot be ignored), proving the whole group was reclaimed.
    deadline = time.time() + 5
    while not pidfile.exists() and time.time() < deadline:
        time.sleep(0.05)
    child_pid = int(pidfile.read_text())
    while time.time() < deadline:
        try:
            os.kill(child_pid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            break
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)  # child is dead -> group cleanup worked


def test_external_happy_path_still_returns_json(tmp_path: Path, monkeypatch):
    """The Popen rewrite preserves the normal request/response contract."""
    script = tmp_path / "agent.py"
    script.write_text(
        textwrap.dedent(
            """
            import sys, json
            json.load(sys.stdin)  # consume request
            print(json.dumps({"output": {"ok": True, "note": "hi"}}))
            """
        ),
        encoding="utf-8",
    )
    _set_cmd(monkeypatch, script)
    client = ExternalCommandClient(model="x", timeout=30)
    out = client.complete_json(system="s", prompt="p")
    assert out == {"ok": True, "note": "hi"}


def test_external_nonzero_exit_raises_runtimeerror(tmp_path: Path, monkeypatch):
    script = tmp_path / "agent.py"
    script.write_text("import sys; sys.stderr.write('boom'); sys.exit(3)\n", encoding="utf-8")
    _set_cmd(monkeypatch, script)
    client = ExternalCommandClient(model="x", timeout=30)
    with pytest.raises(RuntimeError) as ei:
        client.complete_json(system="s", prompt="p")
    assert "boom" in str(ei.value)
    assert not isinstance(ei.value, ExternalAgentTimeoutError)


# ---------------------------------------------------------------------------
# PR1b: bounded retry + timeout_seconds request envelope
# ---------------------------------------------------------------------------

import json
import textwrap as _tw


def test_external_retry_absorbs_one_transient_failure(tmp_path: Path, monkeypatch):
    counter = tmp_path / "attempts.txt"
    script = tmp_path / "agent.py"
    script.write_text(
        _tw.dedent(
            f"""
            import json, os, sys
            path = {str(counter)!r}
            n = int(open(path).read()) if os.path.exists(path) else 0
            open(path, "w").write(str(n + 1))
            if n == 0:
                sys.exit(1)  # first attempt: transient death
            json.load(sys.stdin)
            print(json.dumps({{"ok": True}}))
            """
        ),
        encoding="utf-8",
    )
    _set_cmd(monkeypatch, script)
    client = ExternalCommandClient(model="x", timeout=30, max_retries=1)
    out = client.complete_json(system="s", prompt="p")
    assert out == {"ok": True}
    assert client.last_attempts == 2
    assert counter.read_text() == "2"


def test_external_retry_is_bounded_and_fails_closed(tmp_path: Path, monkeypatch):
    # [hard-negative] a permanently dying agent is retried EXACTLY max_retries
    # times, then the last error propagates — no infinite retry.
    counter = tmp_path / "attempts.txt"
    script = tmp_path / "agent.py"
    script.write_text(
        _tw.dedent(
            f"""
            import os, sys
            path = {str(counter)!r}
            n = int(open(path).read()) if os.path.exists(path) else 0
            open(path, "w").write(str(n + 1))
            sys.stderr.write("permadeath")
            sys.exit(1)
            """
        ),
        encoding="utf-8",
    )
    _set_cmd(monkeypatch, script)
    client = ExternalCommandClient(model="x", timeout=30, max_retries=1)
    with pytest.raises(RuntimeError, match="permadeath"):
        client.complete_json(system="s", prompt="p")
    assert counter.read_text() == "2"
    assert client.last_attempts == 2


def test_external_retry_env_and_validation(tmp_path: Path, monkeypatch):
    script = tmp_path / "agent.py"
    script.write_text("import sys; sys.exit(1)\n", encoding="utf-8")
    _set_cmd(monkeypatch, script)
    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_RETRIES", "0")
    client = ExternalCommandClient(model="x", timeout=30)
    with pytest.raises(RuntimeError):
        client.complete_json(system="s", prompt="p")
    assert client.last_attempts == 1
    # clamp to the ceiling
    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_RETRIES", "99")
    assert ExternalCommandClient(model="x", timeout=30).max_retries == 3
    # [hard-negative] bad configuration fails at construction, not mid-run
    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_RETRIES", "soon")
    with pytest.raises(RuntimeError, match="must be an integer"):
        ExternalCommandClient(model="x", timeout=30)
    monkeypatch.setenv("ESPRESSO_EXTERNAL_AGENT_RETRIES", "-1")
    with pytest.raises(RuntimeError, match=">= 0"):
        ExternalCommandClient(model="x", timeout=30)


def test_external_timeout_type_survives_retries(tmp_path: Path, monkeypatch):
    script = tmp_path / "agent.py"
    script.write_text("import time; time.sleep(120)\n", encoding="utf-8")
    _set_cmd(monkeypatch, script)
    client = ExternalCommandClient(model="x", timeout=1, max_retries=1)
    with pytest.raises(ExternalAgentTimeoutError) as ei:
        client.complete_json(system="s", prompt="p")
    assert ei.value.timeout_seconds == 1
    assert client.last_attempts == 2


def test_request_envelope_carries_timeout_seconds(tmp_path: Path, monkeypatch):
    seen = tmp_path / "request.json"
    script = tmp_path / "agent.py"
    script.write_text(
        _tw.dedent(
            f"""
            import json, sys
            request = json.load(sys.stdin)
            open({str(seen)!r}, "w").write(json.dumps(request))
            print(json.dumps({{"ok": True}}))
            """
        ),
        encoding="utf-8",
    )
    _set_cmd(monkeypatch, script)
    client = ExternalCommandClient(model="x", timeout=42)
    client.complete_json(system="s", prompt="p")
    request = json.loads(seen.read_text())
    assert request["timeout_seconds"] == 42
