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

    client = ExternalCommandClient(model="x", timeout=1)
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
