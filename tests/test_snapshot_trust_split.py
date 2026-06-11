"""Goal 36: snapshot trust split.

``load_run_snapshot`` exposes parsed artifact content as TRUSTED only for runs
that pass the full kernel validation. An invalid/partial run — including one
carrying a fake ``source_backed`` or a tampered grade — surfaces its content
under ``diagnostic_previews`` only, so downstream consumers cannot mistake it
for validated fact.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.kernel import load_run_snapshot, snapshot
from espresso_proof.mock_pipeline import run_mock_critique

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path) -> Path:
    return run_mock_critique(
        "trustsplit", FIXTURES / "promising_but_unproved" / "input.md", tmp_path
    )


def test_valid_run_snapshot_is_trusted(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    snap = load_run_snapshot(tmp_path.resolve(), run_dir)
    assert snap["trusted"] is True
    assert snap["validation"]["status"] == "valid"
    assert snap["artifacts"] is snap["trusted_artifacts"]
    assert snap["diagnostic_previews"] == {}
    assert snap["trusted_artifacts"]["locked_claim"]["id"]
    assert "idea_grade" in snap["trusted_artifacts"]


def test_invalid_run_with_fake_source_backed_is_diagnostic_only(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    sg_path = run_dir / artifacts["source_grounding"]
    sg = read_yaml(sg_path)
    # Plant an unauthorized source_backed step: SCHEMA-VALID shape (cloned from
    # a real step) so the rejection comes from the source-backed authorization
    # gate (no matching hypotheses / no exact locator behind it), not from
    # pydantic field validation. validate_run must reject the run, and the
    # snapshot must stop trusting it.
    fake = dict(sg["steps"][0])
    fake["proof_step_id"] = "step_001"
    fake["status"] = "source_backed"
    sg["steps"][0] = fake
    write_yaml(sg_path, sg)

    snap = load_run_snapshot(tmp_path.resolve(), run_dir)
    assert snap["trusted"] is False
    assert snap["validation"]["status"] != "valid"
    assert snap["state"] in {"partial", "error"}
    # Trusted view exposes no grade/source/evidence summaries...
    assert "idea_grade" not in snap["trusted_artifacts"]
    assert "source_grounding" not in snap["trusted_artifacts"]
    assert "idea_grade" not in snap["artifacts"]  # back-compat alias is also safe
    # ...while the full content stays available, explicitly as diagnostics.
    assert "source_grounding" in snap["diagnostic_previews"]
    assert "idea_grade" in snap["diagnostic_previews"]


def test_missing_required_artifact_is_not_trusted(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    (run_dir / artifacts["idea_grade"]).unlink()
    snap = load_run_snapshot(tmp_path.resolve(), run_dir)
    assert snap["trusted"] is False
    assert "idea_grade" not in snap["trusted_artifacts"]
    assert snap["state"] == "partial"


def test_workspace_snapshot_carries_trust_fields(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    payload = snapshot(tmp_path, run_dir)
    assert payload["current"]["trusted"] is True
    assert "diagnostic_previews" in payload["current"]
