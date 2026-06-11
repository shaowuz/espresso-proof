"""Espresso Product Workbench — a local, read-mostly interactive server.

This is a small standard-library HTTP server (no web framework, no new stack)
that turns the static dashboard into a clickable local product: type a problem,
get a quick core run, click Run Examples / Counterexamples / Literature, Continue
a route, Skip an agent — with live progress — without typing terminal commands.

Every browser action goes through the EXISTING kernel path:
``orchestrate_run`` (fast core) / ``run_single_agent`` / ``skip_agent`` /
``resume_run`` — each of which calls ``validate_run`` and fails closed. The
server never sets mathematical status, never runs an arbitrary shell command
from browser input, and confines every run_id to the project-runs root (no
``/`` / ``..`` / symlink escape).

Long actions (agent runs, route continuation) run on a background thread and
return a ``job_id`` immediately; the UI polls ``GET /api/jobs/{job_id}``.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .agent_runner import ON_DEMAND_AGENTS, run_single_agent, skip_agent
from .orchestrator import orchestrate_run, resume_run
from .run_dashboard import (
    render_run_dashboard,
    render_evidence_panel_html,
    render_topbar_status_html,
    status_summary,
)
from .validators import validate_run

# A run_id may contain only these characters — no path separators or dots-dots.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PROJECT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# CLI ``--agent`` spelling -> internal agent key.
_AGENT_ALIASES = {"failure-patterns": "failure_patterns"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


class WorkbenchError(Exception):
    """A request-level error carrying an HTTP status code (fails closed)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Workbench:
    """Holds the runs root, the per-run locks, and the in-memory job table.

    The job table records ONLY process state (queued/running/done/failed +
    a human-readable error). It is never persisted as proof/evidence status.
    """

    def __init__(self, runs_root: Path, *, provider: str = "stub") -> None:
        self.root = Path(runs_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.provider = provider
        self._jobs: dict[str, dict[str, Any]] = {}
        self._jobs_lock = threading.Lock()
        self._run_locks: dict[str, threading.Lock] = {}
        self._run_locks_guard = threading.Lock()

    # -- path safety ---------------------------------------------------------
    def resolve_run(self, run_id: str) -> Path:
        """Resolve a browser-supplied run_id to a confined run directory.

        Rejects anything that is not a bare, safe id and anything that resolves
        outside ``<root>/projects/*/runs/``. Returns the existing run dir.
        """

        if not run_id or not _RUN_ID_RE.match(run_id):
            raise WorkbenchError(400, "invalid run id")
        matches = sorted(self.root.glob(f"projects/*/runs/{run_id}"))
        for candidate in matches:
            resolved = candidate.resolve()
            # confine: must be inside root AND match the runs layout exactly.
            if self.root in resolved.parents and resolved.name == run_id and resolved.is_dir():
                if resolved.parent.name == "runs":
                    return resolved
        raise WorkbenchError(404, "run not found")

    def _run_lock(self, run_id: str) -> threading.Lock:
        with self._run_locks_guard:
            return self._run_locks.setdefault(run_id, threading.Lock())

    # -- jobs ----------------------------------------------------------------
    def _new_job(self, *, run_id: str | None, action: str, agent: str | None) -> str:
        job_id = uuid.uuid4().hex
        with self._jobs_lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "run_id": run_id,
                "action": action,
                "agent": agent,
                "state": "queued",
                "error": None,
                "started_at": _now(),
                "finished_at": None,
            }
        return job_id

    def _set_job(self, job_id: str, **fields: Any) -> None:
        with self._jobs_lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(fields)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise WorkbenchError(404, "job not found")
            return dict(job)

    def _run_job(self, job_id: str, work, *, lock: threading.Lock | None = None) -> None:
        """Execute one action on a background thread, recording job state.

        Holds ``lock`` (a per-run lock) while working when given; create-run has
        no run yet, so it runs without one. Fails closed: any exception (incl. a
        kernel validation error or model latency failure) is captured as a
        human-readable job error; the run itself is preserved by the underlying
        transactional action.
        """

        self._set_job(job_id, state="running")
        try:
            if lock is not None:
                with lock:
                    result = work()
            else:
                result = work()
            self._set_job(job_id, state="done", finished_at=_now(), result=result)
        except Exception as exc:  # noqa: BLE001 - surface a friendly error
            self._set_job(
                job_id,
                state="failed",
                finished_at=_now(),
                error=str(exc)[:400] or "action failed",
            )

    # -- actions -------------------------------------------------------------
    def start_create_run(self, text: str, *, project: str | None = None) -> dict[str, Any]:
        """Validate input synchronously, then create the run on a BACKGROUND job.

        The HTTP request returns immediately with a ``job_id`` — it never waits on
        model latency (critical for ``--provider external``, where the front-half
        + prover agents call a real model). The UI polls ``GET /api/jobs/{id}``
        and redirects to the run when the job result carries a ``run_id``.
        """

        text = (text or "").strip()
        if not text:
            raise WorkbenchError(400, "empty problem text")
        if len(text) > 4000:
            raise WorkbenchError(400, "problem text too long")
        project = (project or "workbench").strip() or "workbench"
        if not _PROJECT_RE.match(project):
            raise WorkbenchError(400, "invalid project name")
        job_id = self._new_job(run_id=None, action="create", agent=None)

        def work() -> dict[str, Any]:
            return self._do_create_run(text, project)

        threading.Thread(target=self._run_job, args=(job_id, work), daemon=True).start()
        return {"job_id": job_id}

    def _do_create_run(self, text: str, project: str) -> dict[str, Any]:
        """The actual fast-core run (runs inside the background job)."""

        inbox = self.root / "_inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        input_path = inbox / f"{uuid.uuid4().hex}.md"
        input_path.write_text(text + "\n", encoding="utf-8")
        try:
            result = orchestrate_run(
                project=project,
                input_path=input_path,
                output_root=self.root,
                provider=self.provider,
                front_half=self.provider if self.provider != "stub" else "stub",
                fast=True,
            )
        finally:
            input_path.unlink(missing_ok=True)
        run_dir = result["run_dir"]
        return {
            "run_id": run_dir.name,
            "project": run_dir.parents[1].name,
            "state": result["state"],
        }

    def start_agent(self, run_id: str, agent: str) -> dict[str, Any]:
        run_dir = self.resolve_run(run_id)
        agent = _AGENT_ALIASES.get(agent, agent)
        if agent not in ON_DEMAND_AGENTS:
            raise WorkbenchError(400, f"unknown agent: {agent}")
        job_id = self._new_job(run_id=run_id, action="agent", agent=agent)

        def work() -> dict[str, Any]:
            # run_single_agent fails CLOSED: on error it rolls the run back to its
            # previous valid state and returns status="failed" (it does not raise).
            # Surface that as a failed JOB so the UI shows a friendly error, while
            # the previous valid artifact stays intact.
            result = run_single_agent(run_dir, agent, provider=self.provider)
            if result.get("status") == "failed":
                raise WorkbenchError(500, result.get("error") or f"{agent} agent failed")
            return result

        threading.Thread(
            target=self._run_job, args=(job_id, work),
            kwargs={"lock": self._run_lock(run_id)}, daemon=True,
        ).start()
        return {"job_id": job_id, "agent": agent}

    def skip(self, run_id: str, agent: str, *, reason: str | None = None) -> dict[str, Any]:
        run_dir = self.resolve_run(run_id)
        agent = _AGENT_ALIASES.get(agent, agent)
        if agent not in ON_DEMAND_AGENTS:
            raise WorkbenchError(400, f"unknown agent: {agent}")
        with self._run_lock(run_id):
            skip_agent(run_dir, agent, reason=reason)
        return {"agent": agent, "status": "skipped", "summary": self.status(run_id)}

    def continue_route(self, run_id: str, strategy: str, effort: str | None) -> dict[str, Any]:
        run_dir = self.resolve_run(run_id)
        if not strategy or not isinstance(strategy, str):
            raise WorkbenchError(400, "missing route id")
        # Validate the route id against the run's actual brief (reject unknowns).
        brief = self._strategy_ids(run_dir)
        if strategy != "none" and strategy not in brief:
            raise WorkbenchError(400, f"unknown route id: {strategy}")
        if effort is not None and effort not in {"quick_test", "normal_pass", "deep_exploration"}:
            raise WorkbenchError(400, "invalid effort")
        job_id = self._new_job(run_id=run_id, action="continue", agent=None)

        def work() -> dict[str, Any]:
            resume_run(
                run_dir=run_dir,
                selected_strategy=strategy,
                effort=effort,
                prover_provider=self.provider,
            )
            return {"continued": strategy}

        threading.Thread(
            target=self._run_job, args=(job_id, work),
            kwargs={"lock": self._run_lock(run_id)}, daemon=True,
        ).start()
        return {"job_id": job_id, "strategy": strategy}

    def _strategy_ids(self, run_dir: Path) -> set[str]:
        from .io import read_yaml

        manifest = read_yaml(run_dir / "run_manifest.yaml")
        brief_rel = manifest["artifacts"].get("strategy_brief")
        if not brief_rel or not (run_dir / brief_rel).exists():
            return set()
        brief = read_yaml(run_dir / brief_rel)
        return {str(r.get("id")) for r in (brief.get("top_strategies") or []) if r.get("id")}

    # -- read views ----------------------------------------------------------
    def status(self, run_id: str) -> dict[str, Any]:
        run_dir = self.resolve_run(run_id)
        validate_run(run_dir)
        summary = status_summary(run_dir)
        summary["run_id"] = run_id
        summary["evidence_html"] = render_evidence_panel_html(run_dir)
        summary["topbar_html"] = render_topbar_status_html(run_dir)
        return summary

    def dashboard_html(self, run_id: str) -> str:
        run_dir = self.resolve_run(run_id)
        validate_run(run_dir)
        return render_run_dashboard(run_dir, interactive=True)

    def list_runs(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for manifest_path in sorted(self.root.glob("projects/*/runs/*/run_manifest.yaml")):
            run_dir = manifest_path.parent
            try:
                from .io import read_yaml

                manifest = read_yaml(manifest_path)
                claim_rel = manifest["artifacts"].get("locked_claim")
                claim = read_yaml(run_dir / claim_rel) if claim_rel else {}
                runs.append(
                    {
                        "run_id": run_dir.name,
                        "project": run_dir.parents[1].name,
                        "state": manifest.get("current_state", ""),
                        "statement": (claim.get("normalized_statement") or claim.get("statement") or "")[:140],
                    }
                )
            except Exception:  # noqa: BLE001 - a broken run never breaks the list
                continue
        return runs[-50:]


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #


def _make_handler(workbench: Workbench):
    class Handler(BaseHTTPRequestHandler):
        server_version = "EspressoWorkbench/1"

        # Quieten the default per-request stderr logging (and never log bodies).
        def log_message(self, *args: Any) -> None:  # noqa: D401
            return

        # -- helpers ---------------------------------------------------------
        def _send_json(self, obj: Any, status: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str, status: int = 200) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            if length > 1_000_000:
                raise WorkbenchError(413, "request too large")
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                raise WorkbenchError(400, "invalid JSON body") from exc
            if not isinstance(data, dict):
                raise WorkbenchError(400, "expected a JSON object")
            return data

        def _parts(self) -> list[str]:
            path = self.path.split("?", 1)[0]
            return [p for p in path.split("/") if p]

        # -- routing ---------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            try:
                parts = self._parts()
                if not parts:
                    self._send_html(_index_page(workbench))
                    return
                if parts == ["api", "health"]:
                    self._send_json({"ok": True})
                    return
                if len(parts) == 2 and parts[0] == "run":
                    self._send_html(workbench.dashboard_html(parts[1]))
                    return
                if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                    self._send_json(workbench.get_job(parts[2]))
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "status":
                    self._send_json(workbench.status(parts[2]))
                    return
                if parts == ["api", "runs"]:
                    self._send_json({"runs": workbench.list_runs()})
                    return
                raise WorkbenchError(404, "not found")
            except WorkbenchError as exc:
                self._send_json({"error": exc.message}, status=exc.status)
            except Exception as exc:  # noqa: BLE001 - never leak a stack to the browser
                self._send_json({"error": "internal error"}, status=500)

        def do_POST(self) -> None:  # noqa: N802
            try:
                parts = self._parts()
                body = self._read_json()
                if parts == ["api", "runs"]:
                    # Returns a job_id immediately; the run happens on a background
                    # thread so the request never waits on model latency.
                    self._send_json(
                        workbench.start_create_run(body.get("text", ""), project=body.get("project")),
                        status=202,
                    )
                    return
                if len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3] == "agents":
                    self._send_json(workbench.start_agent(parts[2], parts[4]), status=202)
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "continue":
                    self._send_json(
                        workbench.continue_route(parts[2], body.get("strategy", ""), body.get("effort")),
                        status=202,
                    )
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "skip-agent":
                    self._send_json(
                        workbench.skip(parts[2], body.get("agent", ""), reason=body.get("reason"))
                    )
                    return
                raise WorkbenchError(404, "not found")
            except WorkbenchError as exc:
                self._send_json({"error": exc.message}, status=exc.status)
            except Exception:  # noqa: BLE001
                self._send_json({"error": "internal error"}, status=500)

    return Handler


_STATE_WORDS = {
    "SCAFFOLDED": "Starting",
    "FRONT_HALF_DONE": "Initial read",
    "LITERATURE_DONE": "Initial read",
    "WAITING_LLM_AGENT_HUMAN_GATE": "Waiting at your decision",
    "COMPLETED_MEMO": "Memo ready",
}


def _index_page(workbench: Workbench) -> str:
    import html as _html

    rows = "".join(
        f'<li><a href="/run/{_html.escape(r["run_id"])}">{_html.escape(r["statement"] or r["run_id"])}</a>'
        f'<span class="st">{_html.escape(_STATE_WORDS.get(r["state"], r["state"].replace("_", " ").title()))}</span></li>'
        for r in reversed(workbench.list_runs())
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Espresso Proof — Workbench</title>
<style>
body{{margin:0;background:#faf7f1;color:#211d16;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.wrap{{max-width:760px;margin:0 auto;padding:60px 24px}}
h1{{font-family:Georgia,serif;font-weight:500;font-size:34px;margin:0 0 6px}}
.bean{{display:inline-block;width:14px;height:14px;border-radius:50% 50% 50% 50%/60% 60% 40% 40%;background:radial-gradient(circle at 35% 30%,#9a6f4b,#5a3d24);vertical-align:-1px;margin-right:8px}}
p.sub{{color:#7a7367;margin:0 0 26px}}
textarea{{width:100%;min-height:96px;padding:14px 16px;border:1px solid #d9c9b8;border-radius:12px;font:inherit;background:#fffdf9;resize:vertical}}
.row{{display:flex;gap:12px;align-items:center;margin-top:14px}}
button{{appearance:none;border:0;background:#6b4a2f;color:#fff;font:inherit;font-weight:650;padding:11px 20px;border-radius:10px;cursor:pointer}}
button:hover{{background:#553a25}}button:disabled{{opacity:.6;cursor:progress}}
.err{{color:#9c3f2d;font-size:14px}}
.runs{{margin-top:40px}}.runs h2{{font-size:12px;text-transform:uppercase;letter-spacing:.1em;color:#7a7367}}
ul{{list-style:none;padding:0;margin:0}}
.runs li{{display:flex;justify-content:space-between;gap:12px;padding:11px 14px;border:1px solid #e6dfd2;border-radius:10px;background:#fffdf9;margin-bottom:8px}}
.runs a{{color:#211d16;text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.runs a:hover{{color:#6b4a2f}}
.st{{color:#7a7367;font-size:13px;white-space:nowrap}}
</style></head><body><div class="wrap">
<h1><span class="bean"></span>Espresso Proof</h1>
<p class="sub">Type a mathematical claim. You get a quick initial read and proof routes; then run evidence agents and continue a route — all in the browser.</p>
<textarea id="text" placeholder="e.g. Every group of order p^2, where p is prime, is abelian."></textarea>
<div class="row"><button id="go" type="button">Run Espresso</button><span class="err" id="err"></span></div>
<div class="runs"><h2>Recent runs</h2><ul>{rows or '<li class="st">No runs yet.</li>'}</ul></div>
</div><script>
const go=document.getElementById("go"),err=document.getElementById("err");
go.addEventListener("click",async()=>{{
  const text=document.getElementById("text").value.trim();
  if(!text){{err.textContent="Enter a claim first.";return;}}
  go.disabled=true;go.textContent="Starting run…";err.textContent="";
  try{{
    // Returns a job_id immediately; the run happens in the background so we never
    // block on model latency. Poll the job, then open the run when it is ready.
    const r=await fetch("/api/runs",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{text}})}});
    const d=await r.json();
    if(!r.ok)throw new Error(d.error||"failed");
    const jobId=d.job_id;
    for(let i=0;i<900;i++){{
      await new Promise(res=>setTimeout(res,800));
      let j;
      try{{j=await (await fetch("/api/jobs/"+encodeURIComponent(jobId))).json();}}catch{{continue;}}
      if(j.state==="done"&&j.result&&j.result.run_id){{location.href="/run/"+encodeURIComponent(j.result.run_id);return;}}
      if(j.state==="failed")throw new Error(j.error||"run failed");
    }}
    throw new Error("timed out starting the run");
  }}catch(e){{err.textContent=e.message;go.disabled=false;go.textContent="Run Espresso";}}
}});
</script></body></html>"""


def serve(runs_root: Path, *, host: str = "127.0.0.1", port: int = 8765, provider: str = "stub") -> None:
    """Start the workbench server (blocking). Binds to localhost by default."""

    workbench = Workbench(runs_root, provider=provider)
    httpd = ThreadingHTTPServer((host, port), _make_handler(workbench))
    url = f"http://{host}:{port}/"
    print(f"Espresso Workbench on {url}  (runs root: {workbench.root})")
    print("Open it in a browser, type a claim, and click. Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        httpd.server_close()


__all__ = ["Workbench", "WorkbenchError", "serve"]
