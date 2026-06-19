"""Espresso Product Workbench — a local, read-mostly interactive server.

This is a small standard-library HTTP server (no web framework, no new stack)
that serves the React cockpit when ``web/dist`` exists, with a legacy built-in
HTML fallback for CI/dev. Type a problem, get a quick core run, click Run
Examples / Counterexamples / Literature, Continue a route, Skip an agent — with
live progress — without typing terminal commands.

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
import os
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from espresso_proof.orchestration.agent_runner import ON_DEMAND_AGENTS, run_single_agent, skip_agent
from espresso_proof.orchestration.evidence_coordinator import run_evidence_batch_auto
from espresso_proof.ui.cockpit_view import PANELS, build_panel, build_snapshot
from espresso_proof.orchestration.orchestrator import orchestrate_run, resume_run
from espresso_proof.kernel.run_events import append_event
from espresso_proof.orchestration.run_dashboard import (
    render_run_dashboard,
    render_evidence_panel_html,
    render_topbar_status_html,
    status_summary,
)
from espresso_proof.kernel.validators import validate_run

# Goal 43A: the built React cockpit (web/dist). When the build exists the
# server serves it at "/" (the legacy index stays at /legacy and remains the
# automatic fallback when no build is present, e.g. in CI). Static assets are
# confined to this directory and to a fixed content-type allowlist.
_WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

# A run_id may contain only these characters — no path separators or dots-dots.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PROJECT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# CLI ``--agent`` spelling -> internal agent key.
_AGENT_ALIASES = {"failure-patterns": "failure_patterns"}

# Human-facing run modes -> internal provider. Users never see provider words.
# The product UI now starts real-agent runs only; the deterministic fast/stub
# path remains for tests, smoke loops, demos, and explicit API callers.
# Demos are a separate "Open demo" path (Goal 32) — never a mode or provider.
_MODE_PROVIDER = {"fast": "stub", "codex": "external"}



def _now() -> str:
    return datetime.now(UTC).isoformat()


def _friendly_error(raw: str) -> str:
    """Translate a kernel/runtime error into product copy (Goal 31).

    Every message answers: what happened, is the math state still safe, and
    what to do next. The raw text is preserved separately as ``error_detail``
    for the advanced/debug path — never shown as the headline.
    """

    lowered = (raw or "").strip().lower()
    if "espresso_external_agent_command" in lowered or "openai_api_key" in lowered:
        return (
            "The Codex deep run is not set up on this machine. Your math state is "
            "unchanged — no mathematical results were written. Set up the Codex "
            "agent and retry."
        )
    if "rolled back" in lowered:
        # ONLY the StageRunner's own message guarantees an actual rollback —
        # never claim one from a bare validation failure.
        return (
            "The result did not pass Espresso's safety validation, so it was rolled "
            "back. Your previous valid run is preserved. Retry, or skip this agent."
        )
    if "validation" in lowered:
        return (
            "This action did not pass Espresso's safety validation, so it stopped "
            "without going further. Existing results are untouched. Retry after "
            "checking the run diagnostics."
        )
    if "timed out" in lowered or "timeout" in lowered:
        return (
            "The step took too long and stopped safely. Existing results are "
            "untouched. Retry after checking the run diagnostics."
        )
    return (
        "Something went wrong, so this step stopped safely. Existing results are "
        "untouched. Retry after checking the run diagnostics."
    )


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

    def __init__(self, runs_root: Path, *, provider: str = "external") -> None:
        self.root = Path(runs_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.provider = provider
        self._jobs: dict[str, dict[str, Any]] = {}
        self._jobs_lock = threading.Lock()
        self._run_locks: dict[str, threading.Lock] = {}
        self._run_locks_guard = threading.Lock()
        # Display-only fragment cache: run_id -> (artifact-mtime digest, JSON).
        # Never a truth source; see Workbench.status for the invalidation rules.
        self._status_cache: dict[str, tuple[str, str]] = {}
        self._status_cache_lock = threading.Lock()

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
                "cancelled": False,
                "started_at": _now(),
                "finished_at": None,
            }
        return job_id

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        """Best-effort stop. A job still QUEUED is cancelled before it runs; a
        job already RUNNING cannot be force-killed (the agent work is not
        cooperatively interruptible), so we only mark the intent — the kernel
        still validates whatever the in-flight agent finishes, and nothing is
        ever left in an unsafe state."""

        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise WorkbenchError(404, "job not found")
            if job["state"] in ("done", "failed", "cancelled"):
                return {"ok": True, "state": job["state"]}
            job["cancelled"] = True
            if job["state"] == "queued":
                job["state"] = "cancelled"
                job["finished_at"] = _now()
            return {"ok": True, "state": job["state"]}

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

        import time as _time

        # a job cancelled while still queued never starts its work.
        with self._jobs_lock:
            if self._jobs.get(job_id, {}).get("cancelled"):
                self._jobs[job_id].update(state="cancelled", finished_at=_now())
                return
        self._set_job(job_id, state="running")
        t0 = _time.monotonic()
        try:
            if lock is not None:
                with lock:
                    result = work()
            else:
                result = work()
            self._set_job(
                job_id, state="done", finished_at=_now(), result=result,
                duration_seconds=round(_time.monotonic() - t0, 2),
            )
            self._append_job_event(job_id, "job_done")
        except Exception as exc:  # noqa: BLE001 - surface a friendly error
            raw = str(exc)[:400] or "action failed"
            self._set_job(
                job_id,
                state="failed",
                finished_at=_now(),
                duration_seconds=round(_time.monotonic() - t0, 2),
                # headline = product copy (what happened / math state safe / next
                # step); the raw cause stays available as error_detail only.
                error=_friendly_error(raw),
                error_detail=raw,
            )
            self._append_job_event(job_id, "job_failed", detail=raw)

    def _append_job_event(self, job_id: str, kind: str, *, detail: str = "") -> None:
        """Record job completion on the run's diagnostic events.jsonl timeline
        (Goal 35B). Best-effort: a job without a resolvable run, or any timeline
        write problem, never affects the job result."""

        try:
            job = self.get_job(job_id)
            run_id = job.get("run_id")
            if not run_id:
                return
            run_dir = self.resolve_run(run_id)
        except Exception:  # noqa: BLE001 - diagnostics only
            return
        stage = job.get("action") or ""
        if job.get("agent"):
            stage = f"{stage}:{job['agent']}"
        append_event(run_dir, kind, stage=stage, detail=detail)

    # -- actions -------------------------------------------------------------
    def _provider_for_mode(self, mode: str | None) -> str:
        """Map a human-facing UI mode to an internal provider.

        Users pick a mode, never a provider word. An unknown mode falls back to
        the server's configured provider. A "Codex deep run" requests the real
        external path; if it is not configured, the orchestrate preflight fails
        closed and the background job records a friendly error.
        """

        if not mode:
            return self.provider
        return _MODE_PROVIDER.get(str(mode), self.provider)

    def start_create_run(
        self, text: str, *, project: str | None = None, mode: str | None = None
    ) -> dict[str, Any]:
        """Validate input synchronously, then create the run on a BACKGROUND job.

        The HTTP request returns immediately with a ``job_id`` — it never waits on
        model latency (critical for the Codex deep run, where the front-half +
        prover agents call a real model). The UI polls ``GET /api/jobs/{id}`` and
        redirects to the run when the job result carries a ``run_id``.
        """

        text = (text or "").strip()
        if not text:
            raise WorkbenchError(400, "empty problem text")
        if len(text) > 4000:
            raise WorkbenchError(400, "problem text too long")
        project = (project or "workbench").strip() or "workbench"
        if not _PROJECT_RE.match(project):
            raise WorkbenchError(400, "invalid project name")
        if mode is not None and str(mode) not in _MODE_PROVIDER:
            raise WorkbenchError(400, "invalid mode")
        provider = self._provider_for_mode(mode)
        job_id = self._new_job(run_id=None, action="create", agent=None)

        def work() -> dict[str, Any]:
            return self._do_create_run(text, project, provider)

        threading.Thread(target=self._run_job, args=(job_id, work), daemon=True).start()
        return {"job_id": job_id}

    def _do_create_run(self, text: str, project: str, provider: str | None = None) -> dict[str, Any]:
        """The actual fast-core run (runs inside the background job)."""

        provider = provider or self.provider
        # Test-only hook: deliberately slow run creation to prove the instant lane
        # is decoupled (the transient draft must still appear fast). Never set in
        # production; bounded so a typo cannot hang the server.
        delay_ms = os.environ.get("ESPRESSO_TEST_CREATE_DELAY_MS")
        if delay_ms:
            try:
                time.sleep(min(20.0, max(0.0, float(delay_ms) / 1000.0)))
            except ValueError:
                pass
        inbox = self.root / "_inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        input_path = inbox / f"{uuid.uuid4().hex}.md"
        input_path.write_text(text + "\n", encoding="utf-8")
        try:
            result = orchestrate_run(
                project=project,
                input_path=input_path,
                output_root=self.root,
                provider=provider,
                front_half=provider if provider != "stub" else "stub",
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

    def start_demo(self, demo_id: str) -> dict[str, Any]:
        """Build a pre-validated showcase run on a background job (Goal 32).

        Demos are NOT a run mode: each is a deterministic, model-free run built
        through the existing kernel paths (and ``create_demo_run`` ends with
        ``validate_run``, fail closed). Returns a ``job_id`` immediately.
        """

        from espresso_proof.orchestration.demos import DEMOS, create_demo_run

        if demo_id not in DEMOS:
            raise WorkbenchError(404, "unknown demo")
        job_id = self._new_job(run_id=None, action="demo", agent=None)

        def work() -> dict[str, Any]:
            run_dir = create_demo_run(self.root, demo_id)
            return {
                "run_id": run_dir.name,
                "project": run_dir.parents[1].name,
                "demo": demo_id,
            }

        threading.Thread(target=self._run_job, args=(job_id, work), daemon=True).start()
        return {"job_id": job_id, "demo": demo_id}

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

    def start_agents_batch(self, run_id: str, agents: list[str] | None = None) -> dict[str, Any]:
        """Run a BATCH of on-demand evidence agents (parallel compute, serial commit).

        Same fail-closed / re-validate-after-every-commit discipline as
        ``start_agent``; converges to the same valid run as running them one by one.
        Opt-in -- the fast-core pass still defers evidence until a batch is invoked.
        """
        run_dir = self.resolve_run(run_id)
        requested: list[str] | None = None
        if agents is not None:
            requested = []
            for raw in agents:
                a = _AGENT_ALIASES.get(raw, raw)
                if a not in ON_DEMAND_AGENTS:
                    raise WorkbenchError(400, f"unknown agent: {raw}")
                requested.append(a)
            if not requested:
                raise WorkbenchError(400, "no agents requested")
        resolved = requested if requested is not None else list(ON_DEMAND_AGENTS)
        job_id = self._new_job(run_id=run_id, action="agents-batch", agent=",".join(resolved))

        def work() -> dict[str, Any]:
            result = run_evidence_batch_auto(run_dir, requested, provider=self.provider)
            failed = [a for a, r in result["agents"].items() if r.get("status") == "failed"]
            if failed:
                raise WorkbenchError(500, f"evidence agents failed: {', '.join(failed)}")
            return result

        threading.Thread(
            target=self._run_job, args=(job_id, work),
            kwargs={"lock": self._run_lock(run_id)}, daemon=True,
        ).start()
        return {"job_id": job_id, "agents": resolved}

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
        # A continue must never write a human_choice the kernel rejects: each
        # strategy allows only a subset of efforts (e.g. the lock-claim route is
        # quick-only). Coerce a missing/disallowed effort to an allowed one so a
        # one-click continue can never corrupt the run.
        allowed = self._allowed_efforts(run_dir).get(strategy, set())
        if allowed and (effort is None or effort not in allowed):
            for pref in ("normal_pass", "quick_test", "deep_exploration"):
                if pref in allowed:
                    effort = pref
                    break
            else:
                effort = next(iter(sorted(allowed)))
        job_id = self._new_job(run_id=run_id, action="continue", agent=None)

        def work() -> dict[str, Any]:
            from espresso_proof.strategy.strategy_feedback import feedback_summary
            from espresso_proof.ui.latest_round import graph_stamp, write_latest_round_ledger

            # advisory human ranking/time-allocation context for THIS round
            # (the existing taste_judgment channel; never evidence).
            taste = feedback_summary(run_dir, selected_strategy=strategy)
            prev_stamp = graph_stamp(run_dir)
            resume_run(
                run_dir=run_dir,
                selected_strategy=strategy,
                effort=effort,
                taste_judgment=taste,
                prover_provider=self.provider,
            )
            # advisory explanatory ledger of what the round did (display only).
            write_latest_round_ledger(
                run_dir, selected_strategy=strategy, prev_graph_stamp=prev_stamp
            )
            return {"continued": strategy}

        threading.Thread(
            target=self._run_job, args=(job_id, work),
            kwargs={"lock": self._run_lock(run_id)}, daemon=True,
        ).start()
        return {"job_id": job_id, "strategy": strategy}

    # Goal 43 polish: which panel exports which validated artifact file.
    _DOWNLOADABLE = {
        "examples": "examples",
        "counterexamples": "counterexamples",
        "computations": "computation_checks",
        "literature": "literature_review",
        "strategies": "strategy_brief",
    }

    def artifact_download(self, run_id: str, panel: str) -> tuple[str, bytes, str]:
        """Return (artifact_name, bytes, filename) for a downloadable panel.

        Read-only export of a VALIDATED artifact (the run is validated first, so
        a download never serves a run that fails safety). Path-confined via
        ``resolve_run`` and an allowlist of panel -> manifest artifact; a
        missing artifact (panel not run) is an honest 404."""

        from espresso_proof.kernel.io import read_yaml

        name = self._DOWNLOADABLE.get(panel)
        if name is None:
            raise WorkbenchError(404, "no such artifact")
        run_dir = self.resolve_run(run_id)
        try:
            validate_run(run_dir)
        except Exception as exc:
            raise WorkbenchError(
                409,
                "This run no longer passes Espresso's safety validation, so its "
                "artifacts cannot be exported.",
            ) from exc
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        rel = manifest.get("artifacts", {}).get(name) or manifest.get(
            "optional_artifacts", {}
        ).get(name)
        if not rel:
            raise WorkbenchError(404, "this artifact has not been produced yet")
        path = (run_dir / rel).resolve()
        if run_dir.resolve() not in path.parents or not path.is_file():
            raise WorkbenchError(404, "this artifact has not been produced yet")
        return name, path.read_bytes(), f"{run_id}-{name}.yaml"

    def record_research_note(self, run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Goal 43F: bind one advisory research/chat note to a run object.

        Path-confined, bounded body, strict-model-validated (status/evidence
        fields rejected), scrubbed and written to a sidecar the kernel never
        reads, and the core run re-validated afterward — a note changes no
        proof/source/evidence/grade/claim status."""

        from datetime import UTC, datetime
        from uuid import uuid4

        from pydantic import ValidationError

        from espresso_proof.claim.research_notes import ResearchNote, append_note

        allowed = {"target_kind", "target_id", "text"}
        if set(body) - allowed:
            raise WorkbenchError(400, "invalid note")
        run_dir = self.resolve_run(run_id)
        try:
            note = ResearchNote.model_validate(
                {
                    "note_id": uuid4().hex,
                    "run_id": run_id,
                    "target_kind": body.get("target_kind"),
                    "target_id": str(body.get("target_id") or ""),
                    "text": str(body.get("text") or "")[:500],
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
        except ValidationError as exc:
            raise WorkbenchError(400, "invalid note") from exc
        if note.target_id.strip() == "":
            raise WorkbenchError(400, "invalid note")
        with self._run_lock(run_id):
            append_note(run_dir, note)
            try:
                validate_run(run_dir)
            except Exception as exc:
                raise WorkbenchError(
                    409,
                    "This run no longer passes Espresso's safety validation; the "
                    "note is advisory and changed no mathematical status.",
                ) from exc
        return {"ok": True, "target_kind": note.target_kind, "target_id": note.target_id}

    def record_strategy_feedback(self, run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Goal 43C: record one advisory strategy-feedback item.

        Path-confined, bounded body (the handler already caps it), the strategy
        id must exist in the run's brief, the item is strict-model-validated
        (status/evidence fields rejected), written atomically to the sidecar,
        and the CORE run is re-validated afterward (the sidecar is never part
        of the kernel manifest, so a high score upgrades nothing)."""

        from datetime import UTC, datetime
        from uuid import uuid4

        from pydantic import ValidationError

        from espresso_proof.kernel.io import read_yaml
        from espresso_proof.strategy.strategy_feedback import StrategyFeedback, append_feedback, latest_by_strategy

        # reject any unexpected key up front so a client cannot even ATTEMPT to
        # smuggle a status/evidence field through this advisory endpoint.
        allowed = {"strategy_id", "score", "priority", "note", "intended_use"}
        if set(body) - allowed:
            raise WorkbenchError(400, "invalid feedback")
        run_dir = self.resolve_run(run_id)
        strategy_id = str(body.get("strategy_id") or "")
        brief = self._strategy_ids(run_dir)
        if strategy_id not in brief:
            raise WorkbenchError(400, f"unknown route id: {strategy_id}")
        try:
            claim = read_yaml(
                run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["locked_claim"]
            )
        except Exception as exc:  # noqa: BLE001 — corrupt/missing manifest fails closed
            raise WorkbenchError(404, "run is not in a feedback-ready state") from exc
        try:
            item = StrategyFeedback.model_validate(
                {
                    "feedback_id": uuid4().hex,
                    "run_id": run_id,
                    "claim_id": claim.get("id", ""),
                    "strategy_id": strategy_id,
                    "score": body.get("score", 50),
                    "priority": body.get("priority", "normal"),
                    "note": str(body.get("note") or "")[:500],
                    "intended_use": body.get("intended_use", "ranking"),
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
        except ValidationError as exc:
            # never write on a bad payload; the prior run is untouched.
            raise WorkbenchError(400, "invalid feedback") from exc
        # serialize the read-modify-write append under the per-run lock so two
        # concurrent POSTs cannot lose an item (the sidecar append is a
        # load->append->write cycle, not a single atomic op).
        with self._run_lock(run_id):
            append_feedback(run_dir, item)
            # confirm the kernel run still validates after the sidecar write.
            try:
                validate_run(run_dir)
            except Exception as exc:
                raise WorkbenchError(
                    409,
                    "This run no longer passes Espresso's safety validation; the "
                    "feedback is advisory and changed no mathematical status.",
                ) from exc
            latest = latest_by_strategy(run_dir).get(strategy_id)
        return {
            "ok": True,
            "strategy_id": strategy_id,
            "feedback": latest.model_dump(mode="json") if latest else None,
        }

    def _strategy_ids(self, run_dir: Path) -> set[str]:
        from espresso_proof.kernel.io import read_yaml

        manifest = read_yaml(run_dir / "run_manifest.yaml")
        brief_rel = manifest["artifacts"].get("strategy_brief")
        if not brief_rel or not (run_dir / brief_rel).exists():
            return set()
        brief = read_yaml(run_dir / brief_rel)
        return {str(r.get("id")) for r in (brief.get("top_strategies") or []) if r.get("id")}

    def _allowed_efforts(self, run_dir: Path) -> dict[str, set[str]]:
        """Effort options the kernel allows per strategy, from the brief's
        allowed_human_choices (the same source the validator checks)."""

        from espresso_proof.kernel.io import read_yaml

        try:
            manifest = read_yaml(run_dir / "run_manifest.yaml")
            brief = read_yaml(run_dir / manifest["artifacts"]["strategy_brief"])
        except Exception:  # noqa: BLE001
            return {}
        out: dict[str, set[str]] = {}
        for choice in brief.get("allowed_human_choices") or []:
            sid = choice.get("strategy_id")
            if sid:
                out[str(sid)] = {str(e) for e in (choice.get("effort_options") or [])}
        return out

    # -- read views ----------------------------------------------------------
    def _run_digest(self, run_dir: Path) -> str:
        """A display-cache key: mtime+size of EVERY file in the run directory.

        Any artifact write, removal, or addition changes the digest, so a cached
        fragment can never outlive the validated state it was rendered from. The
        digest is NEVER a truth source — it only decides whether to re-render.
        """

        # Every FILE in the run dir (not just *.yaml): validate_run also checks
        # non-YAML manifest artifacts such as final_report.md (existence +
        # proof-language), so they must invalidate the cache too. Size is
        # included alongside mtime_ns to close cheap mtime-restore tampering
        # and coarse-mtime filesystems.
        parts: list[str] = []
        for path in sorted(run_dir.rglob("*")):
            if not path.is_file():
                continue
            try:
                st = path.stat()
                parts.append(f"{path.relative_to(run_dir)}:{st.st_mtime_ns}:{st.st_size}")
            except OSError:
                parts.append(f"{path.name}:gone")
        import hashlib

        return hashlib.sha1("|".join(parts).encode()).hexdigest()

    def status(self, run_id: str) -> dict[str, Any]:
        """Validated status + rendered fragments, with a display-only cache.

        Cache rules (Goal 33): keyed by the artifact-mtime digest; a hit is
        served only when the digest is unchanged since the last VALIDATED
        render; any validation failure drops the entry and raises (fail closed
        — stale HTML is never served after the run stops validating).
        """

        run_dir = self.resolve_run(run_id)
        digest = self._run_digest(run_dir)
        with self._status_cache_lock:
            cached = self._status_cache.get(run_id)
            if cached is not None and cached[0] == digest:
                summary = json.loads(cached[1])  # deep copy via JSON round-trip
                # Goal 65C: stalled/lost is recomputed live (depends on in-memory
                # job state), never cached, so a process death surfaces immediately.
                summary["stalled"] = self._run_stalled(run_id, run_dir)
                return summary
        try:
            validate_run(run_dir)
        except Exception as exc:
            # the run no longer validates: never serve a stale fragment for it,
            # and surface honest product copy (G31) instead of a generic error.
            with self._status_cache_lock:
                self._status_cache.pop(run_id, None)
            raise WorkbenchError(
                409,
                "This run no longer passes Espresso's safety validation, so its "
                "status cannot be shown. Existing results on disk are untouched.",
            ) from exc
        summary = status_summary(run_dir)
        summary["run_id"] = run_id
        summary["evidence_html"] = render_evidence_panel_html(run_dir)
        summary["topbar_html"] = render_topbar_status_html(run_dir)
        with self._status_cache_lock:
            self._status_cache[run_id] = (digest, json.dumps(summary, ensure_ascii=False))
            # bound the cache (display-only; eviction is always safe)
            while len(self._status_cache) > 32:
                self._status_cache.pop(next(iter(self._status_cache)))
        # Goal 65C: attach the live stalled/lost signal AFTER caching, so it is
        # recomputed on every poll and never frozen into the cache.
        summary["stalled"] = self._run_stalled(run_id, run_dir)
        return summary

    def _run_stalled(self, run_id: str, run_dir: Path) -> dict[str, Any] | None:
        """Goal 65C: report when a run has an agent call that recorded ``started``
        but no terminal record (the 65B dangling-start signal) AND no in-memory
        job is progressing it — i.e. the worker/process died mid-call (the order-3
        failure: the server vanished and the in-memory job table with it). This is
        the diagnostic trail that turns a silent dead page into a visible
        last-active-stage + recovery prompt. Display only: it carries NO
        mathematical status and never upgrades proof/source/evidence/grade.
        Returns None for a healthy or genuinely in-flight run; fails closed to
        None on any error."""
        try:
            from datetime import UTC, datetime

            from espresso_proof.kernel.observability import latest_unfinished_agent_call, read_agent_events

            dangling = latest_unfinished_agent_call(run_dir)
            if dangling is None:
                return None
            with self._jobs_lock:
                live = any(
                    job.get("run_id") == run_id
                    and job.get("state") in {"queued", "running"}
                    for job in self._jobs.values()
                )
            if live:
                return None  # a real in-flight call, not a death
            stage = dangling.get("stage") or "an agent step"
            since = dangling.get("started_at")
            # Goal 65F: give the UI the data to render a real failure state
            # (last active stage, elapsed, last completed step) instead of only a
            # dead "Could not reach the server" page. Display only — no math status.
            elapsed_seconds = None
            try:
                if since:
                    elapsed_seconds = max(
                        0, int((datetime.now(UTC) - datetime.fromisoformat(since)).total_seconds())
                    )
            except Exception:  # noqa: BLE001
                elapsed_seconds = None
            last_successful_stage = None
            for rec in read_agent_events(run_dir):
                if rec.get("agent_status") == "finished" and rec.get("stage"):
                    last_successful_stage = rec.get("stage")  # keep the latest
            return {
                "stage": stage,
                "last_active_stage": stage,
                "since": since,
                "elapsed_seconds": elapsed_seconds,
                "last_successful_stage": last_successful_stage,
                # where to look — the per-run diagnostic trail (Goal 54A/65B)
                "diagnostics": ["events.jsonl", "agent_events.jsonl"],
                "message": (
                    f"This run stopped responding during '{stage}'. No proof, "
                    "source, or evidence status was earned. You can retry that step "
                    "or choose another route."
                ),
            }
        except Exception:  # noqa: BLE001 - diagnostics must never break status
            return None

    def snapshot(self, run_id: str) -> dict[str, Any]:
        """Goal 43A: the typed cockpit view model (display-only, fail closed).

        Same validation + digest-cache contract as ``status``: a snapshot is
        served only for a run that currently passes safety validation, and a
        stale entry is never served after the artifacts change."""

        run_dir = self.resolve_run(run_id)
        digest = self._run_digest(run_dir)
        key = f"{run_id}::snapshot"
        with self._status_cache_lock:
            cached = self._status_cache.get(key)
            if cached is not None and cached[0] == digest:
                return json.loads(cached[1])
        try:
            validate_run(run_dir)
        except Exception as exc:
            with self._status_cache_lock:
                self._status_cache.pop(key, None)
            raise WorkbenchError(
                409,
                "This run no longer passes Espresso's safety validation, so its "
                "status cannot be shown. Existing results on disk are untouched.",
            ) from exc
        summary = status_summary(run_dir)
        summary["run_id"] = run_id
        snap = build_snapshot(run_dir, run_id, summary)
        with self._status_cache_lock:
            self._status_cache[key] = (digest, json.dumps(snap, ensure_ascii=False))
            while len(self._status_cache) > 32:
                self._status_cache.pop(next(iter(self._status_cache)))
        return snap

    def panel(self, run_id: str, panel: str) -> dict[str, Any]:
        """Goal 43B: one validated cockpit panel slice (display-only, fail
        closed). Each panel reads its own artifact so panels refresh
        independently; the same validate-then-digest-cache contract applies, so
        a cached slice is never served after the run stops validating, and a
        missing artifact is an honest ``missing``/``not_run`` placeholder —
        never fabricated evidence."""

        if panel not in PANELS:
            raise WorkbenchError(404, "no such panel")
        run_dir = self.resolve_run(run_id)
        digest = self._run_digest(run_dir)
        key = f"{run_id}::panel::{panel}"
        with self._status_cache_lock:
            cached = self._status_cache.get(key)
            if cached is not None and cached[0] == digest:
                return json.loads(cached[1])
        try:
            validate_run(run_dir)
        except Exception as exc:
            with self._status_cache_lock:
                self._status_cache.pop(key, None)
            raise WorkbenchError(
                409,
                "This run no longer passes Espresso's safety validation, so its "
                "status cannot be shown. Existing results on disk are untouched.",
            ) from exc
        summary = status_summary(run_dir)
        summary["run_id"] = run_id
        slice_ = build_panel(run_dir, run_id, summary, panel)
        with self._status_cache_lock:
            self._status_cache[key] = (digest, json.dumps(slice_, ensure_ascii=False))
            # 6 panels per run share the status cache; size for a handful of
            # concurrently-open runs (display-only; eviction is always safe).
            while len(self._status_cache) > 96:
                self._status_cache.pop(next(iter(self._status_cache)))
        return slice_

    def debug_info(self, run_id: str) -> dict[str, Any]:
        """Confined technical log for the advanced/debug path (Goal 33 §7).

        Tail-limited and redacted: the last jobs for this run (raw causes
        truncated), the agent process board, and the front-half stage audit.
        Path access goes through ``resolve_run`` only; home-directory prefixes
        are redacted from every string. Display-only — never a truth source.
        """

        run_dir = self.resolve_run(run_id)
        home = str(Path.home())

        def _redact(value: Any) -> Any:
            if isinstance(value, str):
                return value.replace(home, "~")[:300]
            if isinstance(value, list):
                return [_redact(v) for v in value[:20]]
            if isinstance(value, dict):
                return {k: _redact(v) for k, v in value.items()}
            return value

        with self._jobs_lock:
            jobs = [
                dict(j) for j in self._jobs.values()
                if j.get("run_id") == run_id
                or (j.get("result") or {}).get("run_id") == run_id
            ]
        jobs = jobs[-20:]  # tail-limited
        from espresso_proof.kernel.agent_run_status import load_agent_run_status
        from espresso_proof.kernel.io import read_yaml

        board = load_agent_run_status(run_dir) or {}
        front_half: dict[str, Any] = {}
        fh_path = run_dir / "front_half_run.yaml"
        if fh_path.exists():
            report = read_yaml(fh_path)
            front_half = {
                "provider": report.get("provider"),
                "stages": [
                    {"stage": s.get("stage"), "status": s.get("status"), "errors": s.get("errors", [])}
                    for s in report.get("stages", [])[:20]
                ],
            }
        return _redact({
            "run_id": run_id,
            "jobs": jobs,
            "agent_board": board.get("agents", []),
            "front_half": front_half,
            "note": "Technical log (advanced). Process records only — never evidence or proof status.",
        })

    def dashboard_html(self, run_id: str) -> str:
        run_dir = self.resolve_run(run_id)
        validate_run(run_dir)
        return render_run_dashboard(run_dir, interactive=True)

    def list_runs(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for manifest_path in sorted(self.root.glob("projects/*/runs/*/run_manifest.yaml")):
            run_dir = manifest_path.parent
            try:
                from espresso_proof.kernel.io import read_yaml

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

        def _stream_chat(
            self,
            run_id: str,
            message: str,
            active_object: dict[str, Any] | None = None,
        ) -> None:
            """Goal 43F: stream the conversational preview as Server-Sent Events.

            Read-only: it resolves the run (path-confined) and streams already-
            scrubbed lines built from the validated artifacts. It writes nothing
            and never upgrades status. A mid-stream error ends the stream with a
            safe ``done`` event so the client falls back to polling."""

            from espresso_proof.ui.chat_stream import stream_chat

            try:
                run_dir = workbench.resolve_run(run_id)
            except WorkbenchError as exc:
                self._send_json({"error": exc.message}, status=exc.status)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()

            def emit(event: str, data: dict) -> None:
                chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()

            try:
                for line in stream_chat(run_dir, message, active_object):
                    emit("message", {"text": line})
                emit("done", {"ok": True})
            except Exception:  # noqa: BLE001 — a broken pipe / read error ends safely
                try:
                    emit("done", {"ok": False})
                except Exception:  # noqa: BLE001
                    pass

        def _emit_instant_draft(self, message: str, run_dir: Path | None) -> None:
            """Shared SSE core for the instant lane (Goal 55): ack immediately,
            then stream an already-scrubbed labeled DRAFT built within a small
            latency budget. It never blocks on a slow model and upgrades no
            status. When ``run_dir`` is given the draft is persisted as an advisory
            SIDECAR the kernel never reads; the transient (pre-run) path persists
            nothing. The caller has already sent the SSE response headers."""

            from espresso_proof.ui.instant_lane import (
                build_instant_draft,
                instant_lane_metrics,
                stream_instant_draft,
                write_instant_conversation,
            )
            from espresso_proof.kernel.observability import monotonic_ms, now_iso

            received = now_iso()
            t0 = monotonic_ms()

            def emit(event: str, data: dict) -> None:
                chunk = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()

            try:
                emit("ack", {"label": "Draft only — not checked", "working": True})
                ack_ms = monotonic_ms() - t0
                # client=None → friendly deterministic fallback draft: instant and
                # safe, and never the deep product adapter. A bounded fast-model
                # profile can be injected here later without changing the contract.
                draft = build_instant_draft(
                    message, run_id=run_dir.name if run_dir else "", created_at=now_iso()
                )
                draft_ms = monotonic_ms() - t0
                for line in stream_instant_draft(draft):
                    emit("draft", {"text": line})
                if run_dir is not None:
                    try:
                        write_instant_conversation(run_dir, draft)
                    except Exception:  # noqa: BLE001 — persistence is advisory; never break the stream
                        pass
                metrics = instant_lane_metrics(
                    request_received_at=received,
                    first_visible_ack_ms=ack_ms,
                    first_draft_visible_ms=draft_ms,
                    status=draft.status,
                    background_run_started=run_dir is not None,
                )
                emit("done", {"ok": True, **metrics, "advisory": True})
            except Exception:  # noqa: BLE001 — a broken pipe / read error ends safely
                try:
                    emit("done", {"ok": False, "advisory": True})
                except Exception:  # noqa: BLE001
                    pass

        def _begin_sse(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()

        def _stream_instant_draft(self, run_id: str, message: str) -> None:
            """Run-scoped instant draft: persists the advisory sidecar."""

            try:
                run_dir = workbench.resolve_run(run_id)
            except WorkbenchError as exc:
                self._send_json({"error": exc.message}, status=exc.status)
                return
            self._begin_sse()
            self._emit_instant_draft(message, run_dir)

        def _stream_instant_draft_transient(self, message: str) -> None:
            """Pre-run instant draft (Goal 55): answers the claim text the instant
            it is submitted — decoupled from run creation, so a slow workflow can
            never delay or blank the draft. Persists nothing (no run id yet)."""

            self._begin_sse()
            self._emit_instant_draft(message, None)

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

        def _require_loopback_origin(self) -> None:
            """CSRF-style guard for new mutating endpoints (Goal 43C).

            The workbench binds to loopback only, so a legitimate browser POST
            carries a loopback Host and (for fetch) a loopback Origin. Reject a
            cross-site Origin and a non-loopback Host so a page on another
            origin cannot drive a state change. Loopback covers any port (tests
            and real servers use varied ports)."""

            def _is_loopback_host(value: str) -> bool:
                hostname = value.split("@")[-1].split(":")[0].strip().lower()
                return hostname in {"127.0.0.1", "localhost", "::1", "[::1]"}

            origin = (self.headers.get("Origin") or "").strip().lower()
            if origin:
                # A present Origin must be a loopback http(s) origin. An opaque
                # "null" origin (sandboxed iframe, data: URL) has no "//" and is
                # rejected here — a mutating call must not come from one. A
                # legitimate non-browser client (curl/CLI) sends no Origin and
                # is covered by the loopback Host check below.
                if "//" not in origin:
                    raise WorkbenchError(403, "cross-site request blocked")
                scheme, _, rest = origin.partition("//")
                if scheme not in ("http:", "https:") or not _is_loopback_host(rest):
                    raise WorkbenchError(403, "cross-site request blocked")
            host = (self.headers.get("Host") or "").strip().lower()
            # HTTP/1.1 always sends Host; a non-loopback Host means the request
            # was not addressed to this local server.
            if not host or not _is_loopback_host(host):
                raise WorkbenchError(403, "non-loopback host blocked")

        def _parts(self) -> list[str]:
            path = self.path.split("?", 1)[0]
            return [p for p in path.split("/") if p]

        # -- routing ---------------------------------------------------------
        def _serve_web_asset(self, parts: list[str]) -> bool:
            """Serve a built cockpit asset from web/dist, confined to that
            directory and to the static content-type allowlist. Returns False
            (caller 404s) for anything else — fail closed."""

            if not _WEB_DIST.is_dir():
                return False
            try:
                candidate = _WEB_DIST.joinpath(*parts).resolve()
            except Exception:  # noqa: BLE001
                return False
            if _WEB_DIST.resolve() not in candidate.parents:
                return False
            ctype = _STATIC_TYPES.get(candidate.suffix.lower())
            if ctype is None or not candidate.is_file():
                return False
            body = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True

        def do_GET(self) -> None:  # noqa: N802
            try:
                parts = self._parts()
                if not parts:
                    index = _WEB_DIST / "index.html"
                    if index.is_file():
                        self._send_html(index.read_text(encoding="utf-8"))
                    else:
                        self._send_html(_index_page(workbench))
                    return
                if parts == ["legacy"]:
                    self._send_html(_index_page(workbench))
                    return
                if parts == ["api", "health"]:
                    self._send_json({"ok": True})
                    return
                if parts == ["api", "info"]:
                    # display-only server info for the settings panel — no paths.
                    self._send_json(
                        {"provider": workbench.provider, "runs": len(workbench.list_runs())}
                    )
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "snapshot":
                    self._send_json(workbench.snapshot(parts[2]))
                    return
                if len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3] == "panel":
                    self._send_json(workbench.panel(parts[2], parts[4]))
                    return
                if len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3] == "artifact":
                    name, body, fname = workbench.artifact_download(parts[2], parts[4])
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-yaml; charset=utf-8")
                    self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
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
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "debug":
                    self._send_json(workbench.debug_info(parts[2]))
                    return
                if parts == ["api", "runs"]:
                    self._send_json({"runs": workbench.list_runs()})
                    return
                if parts[0] != "api" and self._serve_web_asset(parts):
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
                        workbench.start_create_run(
                            body.get("text", ""),
                            project=body.get("project"),
                            mode=body.get("mode"),
                        ),
                        status=202,
                    )
                    return
                if len(parts) == 3 and parts[:2] == ["api", "demos"]:
                    self._send_json(workbench.start_demo(parts[2]), status=202)
                    return
                if len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3] == "agents":
                    self._send_json(workbench.start_agent(parts[2], parts[4]), status=202)
                    return

                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "agents-batch":
                    self._send_json(
                        workbench.start_agents_batch(parts[2], body.get("agents")), status=202
                    )
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
                if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
                    # mutating POST: enforce the loopback CSRF guard so another
                    # origin cannot cancel a local run's jobs.
                    self._require_loopback_origin()
                    self._send_json(workbench.cancel_job(parts[2]))
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "strategy-feedback":
                    # new mutating endpoint: enforce the loopback CSRF guard.
                    self._require_loopback_origin()
                    self._send_json(workbench.record_strategy_feedback(parts[2], body))
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "research-note":
                    # Goal 43F: bind an advisory note to a run object (CSRF-guarded).
                    self._require_loopback_origin()
                    self._send_json(workbench.record_research_note(parts[2], body))
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "chat":
                    # Goal 43F: a conversational PREVIEW stream (SSE). It reads
                    # the run's validated state and writes nothing; the loopback
                    # guard still applies. Every line is proof-language-scrubbed
                    # server-side before it leaves the process.
                    self._require_loopback_origin()
                    active_object = body.get("active_object")
                    self._stream_chat(
                        parts[2],
                        str(body.get("message", "")),
                        active_object if isinstance(active_object, dict) else None,
                    )
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "instant-draft":
                    # Goal 55: the instant conversation lane (SSE). Acks fast, then
                    # streams an already-scrubbed labeled DRAFT; loopback-guarded;
                    # persists an advisory sidecar only and upgrades no status.
                    self._require_loopback_origin()
                    self._stream_instant_draft(parts[2], str(body.get("message", "")))
                    return
                if len(parts) == 2 and parts == ["api", "instant-draft"]:
                    # Goal 55: the PRE-RUN instant lane (SSE). Answers the submitted
                    # claim immediately, decoupled from run creation, so a slow
                    # workflow never blanks the page. Loopback-guarded; persists
                    # nothing and upgrades no status.
                    self._require_loopback_origin()
                    self._stream_instant_draft_transient(str(body.get("message", "")))
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

    runs = list(reversed(workbench.list_runs()))
    rows = "".join(
        f'<li><a href="/run/{_html.escape(r["run_id"])}">{_html.escape(r["statement"] or r["run_id"])}</a>'
        f'<span class="st">{_html.escape(_STATE_WORDS.get(r["state"], r["state"].replace("_", " ").title()))}</span></li>'
        for r in runs
    )
    from espresso_proof.orchestration.demos import DEMOS

    demo_cards = "".join(
        f'<div class="demo-card">'
        f'<b>{_html.escape(d["title"])}</b>'
        f'<span class="demo-what">{_html.escape(d["demonstrates"])}</span>'
        f'<span class="demo-lesson">{_html.escape(d["lesson"])}</span>'
        f'<button type="button" class="demo-open" data-demo="{_html.escape(demo_id)}">Open demo</button>'
        f'</div>'
        for demo_id, d in DEMOS.items()
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Espresso Proof — Workbench</title>
<style>{_INDEX_STYLE}</style></head><body>
<header class="top"><div class="brand"><span class="bean"></span>Espresso Proof</div>
<div class="tagline">Local mathematical research workbench</div></header>
<main class="grid">
  <section class="enter card">
    <h1>Study a claim</h1>
    <p class="lead">Type a mathematical claim. You get a quick initial read and 2-3 proof routes, then run evidence agents and continue a route — all in the browser, no terminal.</p>
    <textarea id="text" placeholder="e.g. Every group of order p^2, where p is prime, is abelian."></textarea>
    <input type="hidden" name="mode" value="codex">
    <p class="mode-note"><b>Real agent run.</b> Uses the configured real agent; slower than a template smoke run, but meaningful.</p>
    <div class="go-row"><button id="go" type="button" class="go">Run Espresso</button></div>
    <div class="jobrow" id="jobrow" hidden>
      <div class="jr-head"><span class="jr-spin"></span><b id="jr-stage">Starting run…</b><span class="jr-elapsed" id="jr-elapsed"></span></div>
      <div class="jr-bar"><i></i></div>
      <p class="jr-note" id="jr-note">Espresso is creating the run. This page stays usable while it works.</p>
      <div class="jr-fail" id="jr-fail" hidden>
        <p class="err" id="jr-err"></p>
        <div class="jr-actions">
          <button type="button" class="go small" id="jr-retry">Retry</button>
        </div>
      </div>
    </div>
    <p class="err" id="err"></p>
  </section>
  <aside class="side">
    <div class="card how">
      <h2>How it works</h2>
      <ol>
        <li><b>Quick read</b> — claim understanding + preliminary routes.</li>
        <li><b>Choose a route</b> — your call steers ranking and time, never evidence.</li>
        <li><b>Run evidence</b> — examples, counterexamples, literature, on demand.</li>
        <li><b>Continue the proof search</b> — read what is checked, cited, or still missing.</li>
      </ol>
    </div>
    <div class="card demos"><h2>Open demo</h2>
      <p class="demo-note">Pre-validated showcase runs — instant, model-free, honest about every trust boundary.</p>
      <div class="demo-grid">{demo_cards}</div></div>
    <div class="card runs"><h2>Recent runs</h2><ul>{rows or '<li class="st">No runs yet.</li>'}</ul></div>
  </aside>
</main>
<script>
const go=document.getElementById("go"),err=document.getElementById("err");
const textEl=document.getElementById("text");
function selectedMode(){{return "codex";}}
// Open demo: one click creates a pre-validated showcase run (model-free,
// deterministic) via its own endpoint — demos are NOT a run mode.
document.querySelectorAll(".demo-open").forEach(b=>b.addEventListener("click",()=>{{
  openDemo(b.dataset.demo,b);
}}));
async function openDemo(demoId,btn){{
  btn.disabled=true;btn.textContent="Opening…";err.textContent="";
  jobrow.hidden=false;jobrow.classList.remove("failed");jrFail.hidden=true;
  jrStage.textContent="Preparing the demo…";
  jrNote.textContent="Espresso is building a pre-validated showcase run. This is model-free and takes a few seconds.";
  jrElapsed.textContent="";
  const t0=Date.now();
  try{{
    const r=await fetch("/api/demos/"+encodeURIComponent(demoId),{{method:"POST"}});
    const d=await r.json();
    if(!r.ok)throw new Error(d.error||"failed");
    for(let i=0;i<300;i++){{
      await new Promise(res=>setTimeout(res,600));
      let j;
      try{{j=await (await fetch("/api/jobs/"+encodeURIComponent(d.job_id))).json();}}
      catch{{jrNote.textContent="The local server did not answer. Check that Espresso is still running, then retry.";continue;}}
      jrElapsed.textContent=Math.round((Date.now()-t0)/1000)+"s";
      if(j.state==="done"&&j.result&&j.result.run_id){{jrStage.textContent="Demo ready — opening…";location.href="/run/"+encodeURIComponent(j.result.run_id);return;}}
      if(j.state==="failed"){{jobFailed(j.error||"The demo could not be opened.",false);btn.disabled=false;btn.textContent="Open demo";return;}}
    }}
    jobFailed("Timed out preparing the demo.",false);btn.disabled=false;btn.textContent="Open demo";
  }}catch(e){{jobFailed(e.message,false);btn.disabled=false;btn.textContent="Open demo";}}
}}
// Durable job row: the click is acknowledged immediately, the row stays visible
// while the background job works (stage + elapsed + alive bar), and a failure
// offers Retry — never a frozen page or raw fetch error.
const jobrow=document.getElementById("jobrow"),jrStage=document.getElementById("jr-stage"),
  jrElapsed=document.getElementById("jr-elapsed"),jrNote=document.getElementById("jr-note"),
  jrFail=document.getElementById("jr-fail"),jrErr=document.getElementById("jr-err"),
  jrRetry=document.getElementById("jr-retry");
function jobFailed(msg,wasCodex){{
  jrStage.textContent="Run failed";jobrow.classList.add("failed");
  jrNote.textContent="";jrFail.hidden=false;jrErr.textContent=msg;
  go.disabled=false;go.textContent="Run Espresso";
}}
async function startRun(){{
  const text=textEl.value.trim();
  if(!text){{err.textContent="Enter a claim first.";return;}}
  const mode=selectedMode(),slow=mode==="codex";
  go.disabled=true;go.textContent="Starting run…";err.textContent="";
  jobrow.hidden=false;jobrow.classList.remove("failed");jrFail.hidden=true;
  jrStage.textContent=slow?"Starting Codex deep run…":"Starting run…";
  jrNote.textContent="Espresso is creating the run. This page stays usable while it works.";
  jrElapsed.textContent="";
  const t0=Date.now();
  try{{
    const r=await fetch("/api/runs",{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{text,mode}})}});
    const d=await r.json();
    if(!r.ok)throw new Error(d.error||"failed");
    const jobId=d.job_id;
    for(let i=0;i<2000;i++){{
      await new Promise(res=>setTimeout(res,800));
      let j;
      try{{j=await (await fetch("/api/jobs/"+encodeURIComponent(jobId))).json();}}
      catch{{jrNote.textContent="The local server did not answer. Check that Espresso is still running, then retry.";continue;}}
      const secs=Math.round((Date.now()-t0)/1000);
      jrElapsed.textContent=secs+"s";
      jrStage.textContent=slow?(secs>=30?"Codex is still working — deep runs take a while":"Codex is working…"):"Working…";
      if(j.state==="done"&&j.result&&j.result.run_id){{jrStage.textContent="Run ready — opening…";location.href="/run/"+encodeURIComponent(j.result.run_id);return;}}
      if(j.state==="failed"){{jobFailed(j.error||"The run could not be created.",slow);return;}}
    }}
    jobFailed("Timed out starting the run.",slow);
  }}catch(e){{jobFailed(e.message,slow);}}
}}
go.addEventListener("click",startRun);
jrRetry.addEventListener("click",startRun);
</script></body></html>"""


_INDEX_STYLE = """
:root{--paper:#faf7f1;--card:#fffdf9;--ink:#211d16;--muted:#7a7367;--line:#e6dfd2;--esp:#6b4a2f;--esp-deep:#4d3521;--esp-soft:#f3ebe2;--esp-line:#d9c9b8;--green:#2e7d54}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:linear-gradient(180deg,#fbfaf6,var(--paper) 360px);color:var(--ink);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.top{display:flex;align-items:baseline;gap:16px;padding:20px clamp(28px,4vw,72px);border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:9px;font-weight:700}
.bean{width:14px;height:14px;border-radius:50% 50% 50% 50%/60% 60% 40% 40%;background:radial-gradient(circle at 35% 30%,#9a6f4b,#5a3d24)}
.tagline{color:var(--muted);font-style:italic;font-family:Georgia,serif}
.grid{width:min(1720px,calc(100vw - 72px));margin:0 auto;padding:42px 0 96px;display:grid;grid-template-columns:minmax(620px,1.15fr) minmax(500px,.85fr);gap:32px;align-items:start}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:0 1px 2px rgba(43,33,21,.05),0 12px 32px rgba(43,33,21,.06)}
.enter{padding:38px 42px}
.enter h1{font-family:Georgia,serif;font-weight:500;font-size:clamp(40px,4.2vw,64px);line-height:1.02;margin:0 0 14px;max-width:12ch}
.lead{color:var(--muted);margin:0 0 24px;max-width:68ch;font-size:17px}
textarea{width:100%;min-height:220px;padding:18px 20px;border:1px solid var(--esp-line);border-radius:14px;font:inherit;font-size:18px;line-height:1.55;background:#fffefb;resize:vertical}
.modes{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0}
.mode{border:1px solid var(--line);border-radius:12px;padding:14px 15px;cursor:pointer;background:#fffefb;display:block;min-height:86px}
.mode:hover{border-color:var(--esp-line)}
.mode input{margin-right:7px}
.mode b{font-size:14px}.mode span{display:block;color:var(--muted);font-size:13px;margin-top:4px;padding-left:21px;line-height:1.45}
.mode:has(input:checked){background:var(--esp-soft);border-color:var(--esp)}
.go-row{display:flex;align-items:center;gap:14px;margin-top:8px}
.go{appearance:none;border:0;background:var(--esp);color:#fff;font:inherit;font-weight:700;font-size:17px;padding:15px 32px;border-radius:12px;cursor:pointer}
.go:hover{background:var(--esp-deep)}.go:disabled{opacity:.6;cursor:progress}
.status{color:var(--muted);font-size:14px}
.err{color:#9c3f2d;font-size:14px;margin:10px 0 0;min-height:1.2em}
.jobrow{margin-top:16px;border:1px solid var(--esp-line);border-radius:11px;background:var(--esp-soft);padding:14px 16px;transition:border-color .2s ease,background .2s ease}
.jobrow.failed{border-color:#dcb4a8;background:#faf0ec}
.jr-head{display:flex;align-items:center;gap:10px}
.jr-head b{font-size:14.5px}
.jr-elapsed{margin-left:auto;color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.jr-spin{width:13px;height:13px;border:2px solid rgba(107,74,47,.25);border-top-color:var(--esp);border-radius:50%;animation:jrspin .8s linear infinite;flex:0 0 auto}
.jobrow.failed .jr-spin{display:none}
@keyframes jrspin{to{transform:rotate(360deg)}}
.jr-bar{height:4px;border-radius:99px;background:rgba(107,74,47,.12);overflow:hidden;margin:10px 0 8px}
.jr-bar i{display:block;height:100%;width:34%;border-radius:99px;background:var(--esp);animation:jrslide 1.5s ease-in-out infinite}
.jobrow.failed .jr-bar{display:none}
@keyframes jrslide{0%{transform:translateX(-110%)}100%{transform:translateX(320%)}}
.jr-note{color:var(--muted);font-size:13px;margin:0}
.jr-actions{display:flex;gap:10px;margin-top:8px}
.go.small{padding:8px 16px;font-size:14px}
.ghost.small{appearance:none;border:1px solid var(--esp-line);background:transparent;color:var(--esp);font:inherit;font-size:14px;font-weight:650;padding:8px 16px;border-radius:10px;cursor:pointer}
.ghost.small:hover{background:var(--esp-soft)}
.side{display:grid;gap:20px}
.card.how,.card.demos,.card.runs{padding:24px 26px}
.side h2{font-size:12px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin:0 0 12px}
.how ol{margin:0;padding-left:20px;display:grid;gap:9px}.how li{font-size:15px}.how b{color:var(--esp-deep)}
.demo-note{color:var(--muted);font-size:13.5px;margin:-4px 0 14px}
.demo-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.demo-card{border:1px solid var(--line);background:#fffefb;border-radius:12px;padding:15px 16px;min-height:154px;display:flex;flex-direction:column}
.demo-card b{display:block;font-size:15px}
.demo-what{display:block;color:var(--ink);font-size:13px;margin-top:3px}
.demo-lesson{display:block;color:var(--muted);font-size:12.5px;margin-top:5px;line-height:1.5}
.demo-open{appearance:none;margin-top:auto;align-self:flex-start;border:1px solid var(--esp-line);background:transparent;color:var(--esp);font:inherit;font-size:12.5px;font-weight:650;padding:7px 14px;border-radius:8px;cursor:pointer}
.demo-open:hover{background:var(--esp-soft)}
.demo-open:disabled{opacity:.6;cursor:progress}
.runs ul{list-style:none;padding:0;margin:0;display:grid;gap:7px}
.runs li{display:flex;justify-content:space-between;gap:10px;padding:11px 13px;border:1px solid var(--line);border-radius:10px;background:#fffefb}
.runs a{color:var(--ink);text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px}
.runs a:hover{color:var(--esp)}
.st{color:var(--muted);font-size:12.5px;white-space:nowrap}
@media (max-width:1224px){.grid{grid-template-columns:1fr;width:min(900px,calc(100vw - 40px));padding:28px 0 72px}.enter h1{max-width:none}.demo-grid{grid-template-columns:1fr}}
@media (max-width:700px){.grid{width:calc(100vw - 28px);padding:18px 0 54px}.top{padding:15px 16px}.tagline{display:none}.enter{padding:24px 22px}.enter h1{font-size:36px}.modes{grid-template-columns:1fr}textarea{min-height:180px}.card.how,.card.demos,.card.runs{padding:20px}}
""".strip()


def serve(runs_root: Path, *, host: str = "127.0.0.1", port: int = 8765, provider: str = "external") -> None:
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
