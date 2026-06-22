"""Goal 43B: per-panel slice endpoints for independent artifact loading.

Each cockpit panel reads its own validated artifact slice so panels refresh
independently and show honest not_run/running/failed states. A cached or
polled slice can never turn a missing panel into earned evidence, and an
invalid run is refused fail-closed — the same contract as the snapshot.
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

from espresso_proof.ui.cockpit_view import PANELS, build_panel, build_stage_trace_panel
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures
from espresso_proof.orchestration.agent_flow_status import (
    STAGE_STATUS_FILENAME,
    write_stage_status_projection,
)
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.server import Workbench, _make_handler

CLAIM = "Every group of order p^2, where p is prime, is abelian."
FIXTURES = Path(__file__).parent / "fixtures"


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
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    yield wb, call
    httpd.shutdown()


def _create(call) -> str:
    status, d = call("POST", "/api/runs", {"text": CLAIM})
    assert status == 202
    deadline = time.time() + 20
    while time.time() < deadline:
        _, jd = call("GET", f"/api/jobs/{d['job_id']}")
        if jd.get("state") in ("done", "failed"):
            assert jd["state"] == "done", jd
            return jd["result"]["run_id"]
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_every_panel_serves_its_own_slice(client) -> None:
    wb, call = client
    run_id = _create(call)
    for panel in PANELS:
        status, body = call("GET", f"/api/runs/{run_id}/panel/{panel}")
        assert status == 200, (panel, body)
        assert body["panel"] == panel and body["run_id"] == run_id
        assert "missing" in body and "agent_state" in body
        assert body["agent_state"] in {
            "ready", "not_run", "running", "failed", "skipped", "queued", "done",
        }
    # the claim/strategies fast core is present right after scaffold...
    _, claim = call("GET", f"/api/runs/{run_id}/panel/claim")
    assert claim["missing"] is False
    assert claim["claim"]["text"].startswith("Every group of order p^2")
    _, strat = call("GET", f"/api/runs/{run_id}/panel/strategies")
    assert strat["missing"] is False and strat["strategies"]
    _, trace = call("GET", f"/api/runs/{run_id}/panel/trace")
    assert trace["missing"] is False and trace["items"]
    assert trace["items"][0]["state_word"] == "Stage finished"


def test_unknown_panel_is_404(client) -> None:
    wb, call = client
    run_id = _create(call)
    status, _ = call("GET", f"/api/runs/{run_id}/panel/secrets")
    assert status == 404


def test_panel_fails_closed_on_invalid_run(client) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    (run_dir / "idea_grade.yaml").write_text("status: proved\n", encoding="utf-8")
    status, body = call("GET", f"/api/runs/{run_id}/panel/strategies")
    assert status == 409 and "safety validation" in body["error"]


def test_panel_rejects_bad_run_id(client) -> None:
    wb, call = client
    status, _ = call("GET", "/api/runs/%2e%2e%2fx/panel/claim")
    assert status in (400, 404)


def test_missing_artifact_is_not_run_not_evidence(tmp_path: Path) -> None:
    # a bare run dir (no artifacts): every data panel is missing/not_run with
    # an empty slice — never a fabricated "examples survived" or
    # "no counterexamples" success.
    status = {"agents": [], "selected_route": {}}
    for panel in ("examples", "counterexamples", "computations", "literature"):
        slice_ = build_panel(tmp_path, "r", status, panel)
        assert slice_["missing"] is True
        assert slice_["items"] == []
        assert slice_["agent_state"] == "not_run"


def test_panel_agent_state_is_process_not_evidence(tmp_path: Path) -> None:
    # a running examples agent surfaces as "running" on the examples panel
    # even though no examples artifact exists yet — process state, not
    # evidence; the (empty) slice still carries no earned items.
    status = {
        "agents": [{"agent": "examples", "status": "running"}],
        "selected_route": {},
    }
    slice_ = build_panel(tmp_path, "r", status, "examples")
    assert slice_["agent_state"] == "running"
    assert slice_["missing"] is True and slice_["items"] == []
    # a failed agent is surfaced honestly, never as evidence.
    status["agents"][0]["status"] = "failed"
    assert build_panel(tmp_path, "r", status, "examples")["agent_state"] == "failed"


def test_panels_are_independent_slices(client) -> None:
    # fetching the examples panel returns ONLY example data, not the whole
    # snapshot — a slow/large sibling panel cannot bloat or block this one.
    wb, call = client
    run_id = _create(call)
    _, ex = call("GET", f"/api/runs/{run_id}/panel/examples")
    assert set(ex) <= {"run_id", "panel", "items", "missing", "agent_state", "pressure_lane"}
    assert "strategies" not in ex and "literature" not in ex
    _, lit = call("GET", f"/api/runs/{run_id}/panel/literature")
    assert "source_gaps" in lit and "items" in lit
    _, trace = call("GET", f"/api/runs/{run_id}/panel/trace")
    assert set(trace) <= {
        "run_id",
        "panel",
        "items",
        "missing",
        "agent_state",
        "unavailable",
        "advisory_note",
    }
    assert "strategies" not in trace and "literature" not in trace


def test_trace_panel_synthesizes_missing_stage_status(client) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.get("optional_artifacts", {}).pop("stage_status", None)
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    (run_dir / STAGE_STATUS_FILENAME).unlink(missing_ok=True)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "stage_status" not in manifest.get("optional_artifacts", {})

    status, body = call("GET", f"/api/runs/{run_id}/panel/trace")

    assert status == 200
    assert body["panel"] == "trace"
    assert body["missing"] is False
    assert body["agent_state"] == "ready"
    assert {row["stage_id"] for row in body["items"]} >= {
        "scaffold",
        "understanding_claim",
        "claim_clear_gate",
    }


def test_trace_panel_uses_display_safe_fields_only(client) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    write_stage_status_projection(run_dir)

    status, body = call("GET", f"/api/runs/{run_id}/panel/trace")

    assert status == 200
    assert body["items"]
    allowed = {
        "stage_id",
        "display_name",
        "state_word",
        "tone",
        "group",
        "evidence_role_word",
        "advisory_note",
        "blocked_reason",
        "status_detail",
        "updated_at",
    }
    assert all(set(row) == allowed for row in body["items"])
    rendered = json.dumps(body, sort_keys=True).lower()
    for forbidden in (
        "artifact_hashes",
        "raw yaml",
        "events.jsonl",
        "traceback",
        "proof_status",
        "source_backed",
        "verified",
        "proved",
        "green",
        "proof complete",
    ):
        assert forbidden not in rendered
    assert all(row["tone"] != "earned" for row in body["items"])


def test_trace_panel_surfaces_dependency_withheld_status_detail(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "stage_trace_dependencies",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    write_stage_status_projection(run_dir)

    panel = build_stage_trace_panel(run_dir)

    reviewers = next(row for row in panel["items"] if row["stage_id"] == "reviewers")
    assert reviewers["state_word"] == "Not started"
    assert "prover" in reviewers["status_detail"]
    seal = next(row for row in panel["items"] if row["stage_id"] == "agent_consensus_seal")
    assert seal["state_word"] == "Not started"
    assert "line_numbered_proof_writer" in seal["status_detail"]


def test_trace_panel_fails_closed_on_invalid_stage_status_direct(tmp_path: Path) -> None:
    panel = build_stage_trace_panel(tmp_path)
    assert panel["missing"] is True
    assert panel["unavailable"] is True
    assert panel["items"] == []
    assert "mathematical status changed" in panel["advisory_note"]


def test_trace_panel_direct_invalid_registered_stage_status_is_unavailable(client) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    write_stage_status_projection(run_dir)
    stage_status = read_yaml(run_dir / STAGE_STATUS_FILENAME)
    stage_status["stages"][0]["status_reason"] = "verified proof complete"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, stage_status)

    panel = build_stage_trace_panel(run_dir)

    assert panel["missing"] is True
    assert panel["unavailable"] is True
    assert panel["items"] == []
    rendered = json.dumps(panel, sort_keys=True).lower()
    assert "verified" not in rendered
    assert "proof complete" not in rendered


def test_trace_panel_endpoint_rejects_invalid_registered_stage_status(client) -> None:
    wb, call = client
    run_id = _create(call)
    run_dir = wb.resolve_run(run_id)
    write_stage_status_projection(run_dir)
    stage_status = read_yaml(run_dir / STAGE_STATUS_FILENAME)
    stage_status["stages"][0]["status_reason"] = "verified proof complete"
    write_yaml(run_dir / STAGE_STATUS_FILENAME, stage_status)

    status, body = call("GET", f"/api/runs/{run_id}/panel/trace")

    assert status == 409
    assert "safety validation" in body["error"]


def test_literature_panel_reads_source_grounding_candidates(client) -> None:
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
                    "id": "src_artin",
                    "type": "book",
                    "title": "Artin, Algebra",
                    "authors": ["Artin"],
                    "locator": {"kind": "section", "value": "Lagrange theorem"},
                    "statement_or_summary": "Subgroup order divisibility.",
                    "hypothesis_match": {"matches": "unclear", "mismatches": []},
                    "confidence": "medium",
                    "status": "candidate",
                }
            ],
            "steps": [],
        },
    )
    refresh_final_report_disclosures(run_dir)

    _, lit = call("GET", f"/api/runs/{run_id}/panel/literature")

    assert lit["missing"] is False
    assert lit["items"][0]["title"] == "Artin, Algebra"
    assert lit["items"][0]["tone"] != "earned"


def test_strategies_panel_includes_synthesis_summary(client) -> None:
    wb, call = client
    run_id = _create(call)
    _, strat = call("GET", f"/api/runs/{run_id}/panel/strategies")

    assert strat["strategy_summary"]["note"]
    assert "score" in strat["strategy_summary"]
