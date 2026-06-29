"""Goal 11: calibration report over the mock fixtures.

The calibration aggregates the product's core safety metrics; on the clean
fixture suite every safety metric must be 0 (no source-support false positives,
no proof-language violations, no strategy high-score false positives, all runs
validate and all memos honest). The detector functions also catch a planted
regression.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.evaluation.calibration import (
    _source_support_false_positives,
    all_fixtures,
    run_calibration,
)
from espresso_proof.cli import app
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.mock_pipeline import run_mock_critique

FIXTURES = Path("tests/fixtures")


def test_calibration_is_clean_over_all_fixtures(tmp_path: Path) -> None:
    report = run_calibration(tmp_path)
    m = report["metrics"]
    assert m["fixtures_total"] >= 12
    assert m["fixtures_validated"] == m["fixtures_total"]
    assert m["source_support_false_positives"] == 0
    assert m["proof_language_violations"] == 0
    assert m["strategy_high_score_false_positives"] == 0
    assert m["memo_honest"] == m["fixtures_total"]
    assert m["clean"] is True


def test_calibration_is_deterministic(tmp_path: Path) -> None:
    a = run_calibration(tmp_path / "a")["metrics"]
    b = run_calibration(tmp_path / "b")["metrics"]
    assert a == b


def test_calibration_covers_required_families() -> None:
    fx = set(all_fixtures())
    # representative fixtures for the required eval families
    for required in (
        "vague_claim_unclear",                       # vague claim requiring lock
        "source_misapplication_very_general_to_all",  # source supports narrower claim
        "dropped_hypothesis_not_refutation",          # dropped-hypothesis counterexample
        "unchecked_example_not_evidence",             # finite examples not universal
        "promising_but_unproved",                     # reviewer positive but unverified
        "likely_false_multiple_pressures",            # precise but false-ish
        "refuted_by_all_hypotheses_counterexample",   # checked counterexample
    ):
        assert required in fx, required


def test_source_support_false_positive_detector_flags_planted_regression(tmp_path: Path) -> None:
    # The detector must catch a source_backed step whose ref lacks attribution.
    run_dir = run_mock_critique("calib", FIXTURES / "vague_claim_unclear" / "input.md", tmp_path)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    sg["source_refs"] = [{
        "id": "bad", "type": "paper", "title": "", "authors": [],
        "locator": {"kind": "unknown", "value": ""},
        "statement_or_summary": "x", "hypothesis_match": {"matches": True, "mismatches": []},
        "confidence": "low", "status": "source_backed",
    }]
    sg["steps"] = [{
        "proof_step_id": "s1", "needed_theorem": "t", "status": "source_backed",
        "source_refs": ["bad"], "hypothesis_match": {"matches": True, "mismatches": []},
    }]
    write_yaml(run_dir / "source_grounding.yaml", sg)
    assert _source_support_false_positives(run_dir) > 0


def test_calibration_cli(tmp_path: Path) -> None:
    res = CliRunner().invoke(app, ["calibration", "--out-root", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "calibration:" in res.output
    assert "source-support false positives=0" in res.output


def test_calibration_cli_fails_on_planted_regression(tmp_path: Path, monkeypatch) -> None:
    # Goal 40A: the CI-gated command must EXIT NON-ZERO when any safety metric
    # regresses. Simulate one planted source-support false positive flowing
    # through the real aggregation (the detector itself is proven against a
    # real planted run by test_source_support_false_positive_detector_flags_
    # planted_regression above).
    import espresso_proof.evaluation.calibration as calibration_mod

    real = calibration_mod._source_support_false_positives
    flagged = {"count": 0}

    def planted(run_dir: Path) -> int:
        flagged["count"] += 1
        return 1 if flagged["count"] == 1 else real(run_dir)

    monkeypatch.setattr(calibration_mod, "_source_support_false_positives", planted)
    res = CliRunner().invoke(app, ["calibration", "--out-root", str(tmp_path)])
    assert res.exit_code == 1, res.output
    assert "source-support false positives=1" in res.output


def test_calibration_cli_writes_machine_readable_json(tmp_path: Path) -> None:
    import json

    json_path = tmp_path / "out" / "calibration.json"
    res = CliRunner().invoke(
        app, ["calibration", "--out-root", str(tmp_path / "runs"), "--json-out", str(json_path)]
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["metrics"]["clean"] is True
    assert payload["metrics"]["source_support_false_positives"] == 0
    assert len(payload["rows"]) == payload["metrics"]["fixtures_total"]
