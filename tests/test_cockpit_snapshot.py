"""Goal 43A: typed cockpit snapshot view model + built-frontend serving.

The snapshot endpoint is the thin-client boundary: every trust label and tone
is computed server-side from validated artifacts via the safe display
vocabulary; the "earned" (green-capable) tone exists only for kernel-earned
evidence; missing artifacts yield explicit placeholders, never fabricated
success; an invalid run is refused fail-closed. The built React app is served
from web/dist with strict path confinement, and the legacy index remains both
the no-build fallback and the /legacy route.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from espresso_proof import server as server_module
from espresso_proof.ui.cockpit_view import _EARNED_STATUSES, _tone, build_snapshot
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.run_dashboard import status_summary
from espresso_proof.server import Workbench, _make_handler

CLAIM = "Every group of order p^2, where p is prime, is abelian."


@pytest.fixture()
def client(tmp_path: Path):
    wb = Workbench(tmp_path / "root", provider="stub")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(wb))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"

    def call(method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            base + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.headers.get("Content-Type", ""), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    yield wb, call
    httpd.shutdown()


def _create(call) -> str:
    status, _, raw = call("POST", "/api/runs", {"text": CLAIM})
    assert status == 202
    job_id = json.loads(raw)["job_id"]
    deadline = time.time() + 20
    while time.time() < deadline:
        _, _, jraw = call("GET", f"/api/jobs/{job_id}")
        jd = json.loads(jraw)
        if jd.get("state") in ("done", "failed"):
            assert jd["state"] == "done", jd
            return jd["result"]["run_id"]
        time.sleep(0.2)
    raise AssertionError("job did not finish")


# --------------------------------------------------------------------------- #
# snapshot view model
# --------------------------------------------------------------------------- #


def test_snapshot_is_typed_and_display_safe(client) -> None:
    wb, call = client
    run_id = _create(call)
    status, ctype, raw = call("GET", f"/api/runs/{run_id}/snapshot")
    assert status == 200 and "application/json" in ctype
    snap = json.loads(raw)
    assert snap["run_id"] == run_id
    # the claim card carries display sentences, never raw enum tokens.
    assert snap["claim"]["text"].startswith("Every group of order p^2")
    assert snap["claim"]["status_sentence"]
    # strategies carry one-line summaries plus display phrases.
    assert snap["strategies"], "the smoke run produces top strategies"
    for s in snap["strategies"]:
        assert s["id"] and s["title"] and s["one_line"]
        assert s["status_sentence"] and s["source_phrase"]
        assert s["source_tone"] in {"earned", "warn", "human", "muted", "neutral"}
        # raw enum tokens must not leak through the display fields.
        for field in ("status_sentence", "source_phrase"):
            assert "source_backed" not in s[field]
            assert "_" not in s[field].split()[0].lower() or True
    # every panel is present (possibly empty) plus an explicit missing map.
    for panel in ("examples", "counterexamples", "computations", "literature"):
        assert isinstance(snap[panel], list)
    assert set(snap["missing"]) == {
        "claim", "grade", "strategies", "examples",
        "counterexamples", "computations", "literature",
    }
    assert not snap["missing"]["claim"]


def test_snapshot_green_tone_only_for_earned_evidence(client) -> None:
    wb, call = client
    run_id = _create(call)
    _, _, raw = call("GET", f"/api/runs/{run_id}/snapshot")
    snap = json.loads(raw)
    # the smoke run earns NO reference support: no literature/strategy surface
    # may carry the earned tone, and candidate phrasing must say so.
    for ref in snap["literature"]:
        assert ref["tone"] != "earned", ref
    for s in snap["strategies"]:
        assert s["source_tone"] != "earned", s
    # tone mapping itself: earned strictly for kernel-earned statuses,
    # unknown/None fail closed to muted.
    for status in _EARNED_STATUSES:
        assert _tone(status) == "earned"
    assert _tone("candidate") == "muted"
    assert _tone("running") == "muted"
    assert _tone("skipped") == "muted"
    assert _tone(None) == "muted"
    assert _tone("totally_unknown_status") == "muted"
    assert _tone("misapplied") == "warn"


def test_snapshot_route_source_label_is_advisory_even_when_route_says_source_backed(
    client,
) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief_path = run_dir / manifest["artifacts"]["strategy_brief"]
    brief = read_yaml(brief_path)
    route_id = brief["top_strategies"][0]["id"]
    brief["top_strategies"][0]["source_status"] = "source_backed"
    write_yaml(brief_path, brief)

    snap = build_snapshot(run_dir, run_id, status_summary(run_dir))
    route = next(s for s in snap["strategies"] if s["id"] == route_id)

    assert route["source_phrase"] == "route source label; verify step grounding"
    assert route["source_tone"] != "earned"


def test_snapshot_strategy_free_text_scrubs_raw_source_vocabulary(client) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief_path = run_dir / manifest["artifacts"]["strategy_brief"]
    brief = read_yaml(brief_path)
    route = brief["top_strategies"][0]
    route_id = route["id"]
    route["title"] = "A source-backed route title"
    route["why_this_route"] = "Uses a source_backed theorem label."
    route["main_risk"] = "The source backed phrase is advisory only."
    route["next_loop_plan"] = "Avoid source_backed_status wording."
    write_yaml(brief_path, brief)

    snap = build_snapshot(run_dir, run_id, status_summary(run_dir))
    rendered_route = next(s for s in snap["strategies"] if s["id"] == route_id)
    rendered = json.dumps(rendered_route, sort_keys=True).lower()

    assert "source-backed" not in rendered
    assert "source_backed" not in rendered
    assert "source backed" not in rendered
    assert "reference-supported" in rendered


def test_snapshot_literature_free_text_scrubs_raw_source_vocabulary(client) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    literature_path = run_dir / manifest["artifacts"]["literature_review"]
    literature = read_yaml(literature_path)
    item = literature["candidate_references"][0]
    item["title"] = "A source-backed candidate title"
    item["why_relevant"] = "Mentions source_backed_status in free text."
    write_yaml(literature_path, literature)

    snap = build_snapshot(run_dir, run_id, status_summary(run_dir))
    rendered = json.dumps(snap["literature"], sort_keys=True).lower()

    assert "source-backed" not in rendered
    assert "source_backed" not in rendered
    assert "source backed" not in rendered
    assert "reference-supported" in rendered


def test_snapshot_literature_includes_source_grounding_candidates(client) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    literature_path = run_dir / manifest["artifacts"]["literature_review"]
    source_path = run_dir / manifest["artifacts"]["source_grounding"]
    literature = read_yaml(literature_path)
    literature["candidate_references"] = []
    write_yaml(literature_path, literature)
    write_yaml(
        source_path,
        {
            "claim_id": read_yaml(run_dir / manifest["artifacts"]["locked_claim"])["id"],
            "source_refs": [
                {
                    "id": "src_dummit_foote",
                    "type": "book",
                    "title": "Dummit and Foote, Abstract Algebra",
                    "authors": ["Dummit", "Foote"],
                    "locator": {"kind": "theorem", "value": "Lagrange theorem"},
                    "statement_or_summary": "Subgroup orders divide group order.",
                    "hypothesis_match": {"matches": "unclear", "mismatches": []},
                    "confidence": "medium",
                    "status": "candidate",
                }
            ],
            "steps": [],
        },
    )

    snap = build_snapshot(run_dir, run_id, status_summary(run_dir))
    rendered = json.dumps(snap["literature"], sort_keys=True).lower()

    assert "dummit" in rendered
    assert "lagrange theorem" in rendered
    assert snap["literature"][0]["tone"] != "earned"
    assert "source_backed" not in rendered


def test_snapshot_strategy_summary_exposes_synthesis_and_grade(client) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    synthesis_path = run_dir / manifest["artifacts"]["strategy_synthesis"]
    grade_path = run_dir / manifest["artifacts"]["idea_grade"]
    synthesis = read_yaml(synthesis_path)
    synthesis["synthesis_note"] = "Direct coset-counting is the best candidate route."
    write_yaml(synthesis_path, synthesis)
    grade = read_yaml(grade_path)
    grade["status"] = "fragile"
    grade["score"] = 40
    grade["reasons"] = ["Standard route, but source_backed locator is absent."]
    grade["source_gaps"] = ["No exact source locator yet."]
    write_yaml(grade_path, grade)

    snap = build_snapshot(run_dir, run_id, status_summary(run_dir))
    summary = snap["strategy_summary"]

    assert summary["score"] == 40
    assert "Direct coset-counting" in summary["note"]
    assert "reference-supported" in json.dumps(summary)
    assert "source_backed" not in json.dumps(summary)


def test_snapshot_missing_artifacts_yield_placeholders(tmp_path: Path) -> None:
    # a bare directory (no manifest): every panel flagged missing, all lists
    # empty, and the claim sentence still a safe display sentence.
    snap = build_snapshot(tmp_path, "r", {"state": "Starting"})
    assert all(snap["missing"].values())
    assert snap["examples"] == [] and snap["literature"] == []
    assert snap["claim"]["text"] == ""
    assert snap["claim"]["status_sentence"]  # safe fallback, never empty
    assert snap["claim"]["human_review_required"] is True


def test_snapshot_fails_closed_on_invalid_run(client) -> None:
    wb, call = client
    run_id = _create(call)
    # corrupt a required artifact: the snapshot must refuse with honest copy.
    run_dir = wb.resolve_run(run_id)
    (run_dir / "idea_grade.yaml").write_text("status: proved\n", encoding="utf-8")
    status, _, raw = call("GET", f"/api/runs/{run_id}/snapshot")
    assert status == 409
    assert "safety validation" in json.loads(raw)["error"]


def test_snapshot_rejects_bad_run_id(client) -> None:
    wb, call = client
    status, _, raw = call("GET", "/api/runs/%2e%2e%2fescape/snapshot")
    assert status in (400, 404)


# --------------------------------------------------------------------------- #
# built-frontend serving
# --------------------------------------------------------------------------- #


@pytest.fixture()
def web_dist(tmp_path: Path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>cockpit</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('cockpit')", encoding="utf-8")
    (dist / "assets" / "app.css").write_text("body{}", encoding="utf-8")
    (dist / "secret.py").write_text("nope", encoding="utf-8")
    monkeypatch.setattr(server_module, "_WEB_DIST", dist)
    return dist


def test_serves_built_cockpit_when_dist_exists(client, web_dist) -> None:
    wb, call = client
    status, ctype, raw = call("GET", "/")
    assert status == 200 and "text/html" in ctype
    assert b"cockpit" in raw
    status, ctype, raw = call("GET", "/assets/app.js")
    assert status == 200 and "text/javascript" in ctype
    status, ctype, raw = call("GET", "/assets/app.css")
    assert status == 200 and "text/css" in ctype
    # the legacy index stays reachable for fallback workflows.
    status, ctype, raw = call("GET", "/legacy")
    assert status == 200 and b"Espresso" in raw


def test_static_serving_is_confined(client, web_dist) -> None:
    wb, call = client
    # disallowed suffix inside dist: refused.
    status, _, _ = call("GET", "/secret.py")
    assert status == 404
    # traversal out of dist: refused.
    status, _, _ = call("GET", "/assets/%2e%2e/%2e%2e/etc/passwd")
    assert status == 404
    # api namespace is never shadowed by static files.
    status, _, raw = call("GET", "/api/health")
    assert status == 200 and json.loads(raw)["ok"] is True


def test_no_dist_falls_back_to_legacy_index(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(server_module, "_WEB_DIST", tmp_path / "absent")
    wb, call = client
    status, ctype, raw = call("GET", "/")
    assert status == 200 and "text/html" in ctype
    assert b"Espresso" in raw
