"""Goal 32 Demo Experience: one-click, fresh-checkout-reproducible showcase runs
that never overclaim and never bypass a validator."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from espresso_proof.demos import DEMOS, create_demo_run, demo_for_project
from espresso_proof.io import read_yaml
from espresso_proof.memo import build_concise_memo
from espresso_proof.run_dashboard import render_run_dashboard
from espresso_proof.server import Workbench, _index_page, _make_handler
from espresso_proof.validators import validate_run

_PROOF_WORDS = ("proof-close", "proof complete", "the theorem is proved", "we have proved")


# --------------------------------------------------------------------------- #
# registry completeness (Goal 32 §2/§7)
# --------------------------------------------------------------------------- #


def test_every_demo_has_all_required_card_fields() -> None:
    required = ("title", "claim", "demonstrates", "lesson", "first_result", "next_click", "trust_boundary")
    assert set(DEMOS) == {"p2-group", "order6-false", "evt-compactness", "matrix-rank"}
    for demo_id, card in DEMOS.items():
        for field in required:
            assert card.get(field), f"{demo_id}.{field}"


# --------------------------------------------------------------------------- #
# demos validate from a fresh root and stay honest
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("demo_id", sorted(DEMOS))
def test_demo_validates_from_fresh_root(tmp_path: Path, demo_id: str) -> None:
    run_dir = create_demo_run(tmp_path, demo_id)
    validate_run(run_dir)
    memo = build_concise_memo(run_dir).lower()
    for phrase in _PROOF_WORDS:
        assert phrase not in memo


def test_false_claim_demo_is_refuted_not_proved(tmp_path: Path) -> None:
    run_dir = create_demo_run(tmp_path, "order6-false")
    grade = read_yaml(run_dir / "idea_grade.yaml")
    assert grade["status"] == "refuted"
    assert grade["proof_language_status"] == "idea_sketch"
    # the refutation is admissible by the kernel's own gate
    tests = read_yaml(run_dir / "counterexamples.yaml")["tests"]
    assert any(
        t["refutes_claim"] and t["satisfies_all_claim_hypotheses"] is True for t in tests
    )
    html = render_run_dashboard(run_dir, interactive=True)
    assert "A checked counterexample refutes the claim" in html
    assert "Read the counterexample" in html


def test_p2_demo_earns_scoped_checks_honestly(tmp_path: Path) -> None:
    run_dir = create_demo_run(tmp_path, "p2-group")
    pv = read_yaml(run_dir / "proof_verification.yaml")
    checked = [e for e in pv["entries"] if e.get("earned_status") == "instance_checked"]
    assert checked  # machine-earned by the trusted finite-group verifier
    grade = read_yaml(run_dir / "idea_grade.yaml")
    assert grade["status"] != "refuted"
    memo = build_concise_memo(run_dir)
    assert "NOT a proof" in memo or "not a proof" in memo


def test_matrix_demo_is_scoped_only(tmp_path: Path) -> None:
    run_dir = create_demo_run(tmp_path, "matrix-rank")
    pv = read_yaml(run_dir / "proof_verification.yaml")
    entries = [e for e in pv["entries"] if e.get("earned_status") == "instance_checked"]
    assert entries
    memo = build_concise_memo(run_dir)
    assert "scoped" in memo.lower() or "finite/computational" in memo.lower()
    assert "do NOT prove the universal claim" in memo or "not a proof" in memo.lower()


def test_evt_demo_flags_risk_without_refuting(tmp_path: Path) -> None:
    run_dir = create_demo_run(tmp_path, "evt-compactness")
    grade = read_yaml(run_dir / "idea_grade.yaml")
    assert grade["status"] != "refuted"  # a missing hypothesis never refutes
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "mathematical_judgment" in manifest.get("optional_artifacts", {})


# --------------------------------------------------------------------------- #
# demo panel + entry section + privacy
# --------------------------------------------------------------------------- #


def test_demo_run_page_shows_what_this_demo_shows(tmp_path: Path) -> None:
    run_dir = create_demo_run(tmp_path, "p2-group")
    html = render_run_dashboard(run_dir, interactive=True)
    assert "What this demo shows" in html
    assert "Trust boundary:" in html
    assert DEMOS["p2-group"]["next_click"] in html
    # non-demo runs get no panel
    assert demo_for_project("workbench") is None


def test_entry_page_open_demo_section(tmp_path: Path) -> None:
    wb = Workbench(tmp_path / "root", provider="stub")
    html = _index_page(wb)
    assert "Open demo" in html
    assert "Demo mode" not in html  # demos are not a mode
    for card in DEMOS.values():
        assert card["title"] in html
        assert card["demonstrates"] in html
    assert 'data-demo="p2-group"' in html


def test_demo_pages_show_no_private_paths(tmp_path: Path) -> None:
    """The main path never displays the local filesystem location of a run."""
    import re

    run_dir = create_demo_run(tmp_path, "p2-group")
    html = render_run_dashboard(run_dir, interactive=True)
    body = html.split("<script>")[0]
    body = re.sub(r"<style>.*?</style>", " ", body, flags=re.S)
    body = re.sub(r"<details class=\"debug\".*?</details>", " ", body, flags=re.S)
    visible = re.sub(r"<[^>]+>", " ", body)
    assert str(tmp_path) not in visible
    assert "/tmp/" not in visible and "/Users/" not in visible


# --------------------------------------------------------------------------- #
# one-click via the API
# --------------------------------------------------------------------------- #


def test_open_demo_endpoint_one_click(tmp_path: Path) -> None:
    wb = Workbench(tmp_path / "root", provider="stub")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(wb))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/demos/order6-false", method="POST"
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 202
            d = json.loads(resp.read())
        deadline = time.time() + 30
        while time.time() < deadline:
            job = wb.get_job(d["job_id"])
            if job["state"] in ("done", "failed"):
                break
            time.sleep(0.2)
        assert job["state"] == "done", job
        run_id = job["result"]["run_id"]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/run/{run_id}") as resp:
            page = resp.read().decode()
        assert "What this demo shows" in page
        # unknown demo rejected
        req2 = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/demos/nope", method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req2)
        assert exc.value.code == 404
    finally:
        httpd.shutdown()