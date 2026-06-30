"""Benchmark / Eval Set V1.

A small deterministic regression benchmark over existing mock fixtures covering
the five required categories: true claim, false claim, missing hypothesis, source
not applicable, checked counterexample. It checks CONSERVATIVE behaviour, never
asserts a proof, and is stable across runs.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.evaluation.benchmark import BENCHMARK_V1, run_benchmark
from espresso_proof.cli import app

_CATEGORIES = {
    "true_claim",
    "false_claim",
    "missing_hypothesis",
    "source_not_applicable",
    "checked_counterexample",
}


# ---------------------------------------------------------------------------
# 1) All benchmark fixtures validate (run + validate_run inside run_benchmark).
# ---------------------------------------------------------------------------


def test_all_benchmark_cases_run_and_pass(tmp_path: Path) -> None:
    report = run_benchmark(tmp_path)
    assert {row["category"] for row in report} == _CATEGORIES
    for row in report:
        assert row["passed"], (row["category"], row["problems"])


# ---------------------------------------------------------------------------
# 2) Expected conservative grade/status behaviour is checked.
# ---------------------------------------------------------------------------


def test_conservative_status_buckets(tmp_path: Path) -> None:
    report = {row["category"]: row for row in run_benchmark(tmp_path)}
    assert report["true_claim"]["idea_status"] == "promising"
    assert report["false_claim"]["idea_status"] == "likely_false"
    assert report["missing_hypothesis"]["idea_status"] == "unclear"
    assert report["checked_counterexample"]["idea_status"] == "refuted"
    # none of these claims is ever graded as a checked proof.


# ---------------------------------------------------------------------------
# 3) Source-not-applicable case cannot become reference-supported.
# ---------------------------------------------------------------------------


def test_source_not_applicable_is_not_reference_supported(tmp_path: Path) -> None:
    from espresso_proof.kernel.io import read_yaml
    from espresso_proof.orchestration.mock_pipeline import run_mock_critique
    from espresso_proof.kernel.validators import validate_run

    run_dir = run_mock_critique(
        "hodge_atoms",
        Path("tests/fixtures/source_misapplication_very_general_to_all/input.md"),
        tmp_path,
    )
    validate_run(run_dir)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    sg = read_yaml(run_dir / artifacts["source_grounding"])
    assert all(r.get("status") != "source_backed" for r in sg.get("source_refs", []))
    assert all(s.get("status") != "source_backed" for s in sg.get("steps", []))


# ---------------------------------------------------------------------------
# 4) Checked-counterexample case is handled correctly (scope matches).
# ---------------------------------------------------------------------------


def test_checked_counterexample_scope_is_respected(tmp_path: Path) -> None:
    from espresso_proof.kernel.io import read_yaml
    from espresso_proof.orchestration.mock_pipeline import run_mock_critique
    from espresso_proof.kernel.validators import validate_run

    run_dir = run_mock_critique(
        "hodge_atoms",
        Path("tests/fixtures/refuted_by_all_hypotheses_counterexample/input.md"),
        tmp_path,
    )
    validate_run(run_dir)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    cx = read_yaml(run_dir / artifacts["counterexamples"])
    refuters = [t for t in cx.get("tests", []) if t.get("refutes_claim")]
    assert refuters, "expected a claim-refuting counterexample"
    for t in refuters:
        # a claim refutation is allowed only when all claim hypotheses are satisfied.
        assert t.get("satisfies_all_claim_hypotheses") is True


# ---------------------------------------------------------------------------
# 5) Benchmark command / report is stable.
# ---------------------------------------------------------------------------


def test_benchmark_report_is_deterministic(tmp_path: Path) -> None:
    a = run_benchmark(tmp_path / "a")
    b = run_benchmark(tmp_path / "b")
    norm = lambda rep: [(r["category"], r["fixture"], r["idea_status"], r["passed"]) for r in rep]
    assert norm(a) == norm(b)


def test_benchmark_cli_passes_and_reports(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["benchmark", "--out-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "case(s) passed" in result.output
    # report lists every category and asserts no proof-complete language leaked.
    for category in _CATEGORIES:
        assert category in result.output
    lower = result.output.lower()
    for phrase in ("proof complete", "we proved", "proof-close"):
        assert phrase not in lower


def test_benchmark_v1_covers_required_categories() -> None:
    assert {c["category"] for c in BENCHMARK_V1} == _CATEGORIES


def test_benchmark_cli_fails_on_planted_regression(tmp_path: Path, monkeypatch) -> None:
    # Goal 40A: the CI-gated command must EXIT NON-ZERO when a benchmark case
    # regresses. Plant one failing case through the real reporting path.
    # The `benchmark` command lives in espresso_proof.cli_eval (the eval command
    # group split out of cli.py), so patch run_benchmark where the command
    # resolves it.
    import espresso_proof.cli_eval as cli_mod

    real = cli_mod.run_benchmark

    def planted(output_root):
        report = real(output_root)
        bad = dict(report[0])
        bad["passed"] = False
        bad["problems"] = ["planted regression: unauthorized source_backed"]
        return [bad] + report[1:]

    monkeypatch.setattr(cli_mod, "run_benchmark", planted)
    res = CliRunner().invoke(app, ["benchmark", "--out-root", str(tmp_path)])
    assert res.exit_code == 1, res.output
    assert "planted regression" in res.output


def test_benchmark_cli_writes_machine_readable_json(tmp_path: Path) -> None:
    import json

    json_path = tmp_path / "out" / "benchmark.json"
    res = CliRunner().invoke(
        app, ["benchmark", "--out-root", str(tmp_path / "runs"), "--json-out", str(json_path)]
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["passed"] == payload["total"] == len(payload["cases"])
    assert all(case["passed"] for case in payload["cases"])
