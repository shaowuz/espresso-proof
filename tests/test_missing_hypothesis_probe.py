"""Goal 42E cycle 8: deterministic missing-hypothesis counterexample probe."""

from __future__ import annotations

from pathlib import Path

from espresso_proof.counterexample_probes import propose_deterministic_counterexamples
from espresso_proof.io import read_yaml, write_yaml

MAX_CLAIM = "Every continuous function f: R -> R attains a maximum value.\n"


def _claim(statement: str, hypotheses: list[str] | None = None) -> dict:
    return {
        "id": "claim_001",
        "normalized_statement": statement.strip(),
        "hypotheses": [
            {"name": n, "status": "explicit", "source": "user"}
            for n in (hypotheses or [])
        ],
    }


def test_probe_matches_only_the_anchored_form() -> None:
    record = propose_deterministic_counterexamples(_claim(MAX_CLAIM))
    assert record and record[0]["operator_id"] == "deterministic_missing_hypothesis_probe"
    # negations, weakened domains, and different conclusions never match.
    for variant in (
        "No continuous function f: R -> R attains a maximum value.",
        "Every continuous function f: [a, b] -> R attains a maximum value.",
        "Every continuous function f: R -> R attains a minimum value.",
        "Every continuous function f: R -> R is bounded.",
        "Every bounded continuous function f: R -> R attains a maximum value.",
    ):
        assert propose_deterministic_counterexamples(_claim(variant)) == [], variant


def test_record_discipline_no_refutation_ever() -> None:
    # undetermined hypotheses: admissibility unclear, never a refutation.
    record = propose_deterministic_counterexamples(_claim(MAX_CLAIM))[0]
    assert record["satisfies_all_claim_hypotheses"] == "unclear"
    assert record["refutes_claim"] is False
    assert record["exposes_missing_hypothesis"] is True
    assert record["result"] == "failed"
    assert "compact" in record["missing_hypothesis"]
    assert "extreme value theorem" in record["repaired_claim_suggestion"]
    assert "exact" in record["support"]
    # trivial determined hypotheses: admissible, still never a refutation
    # (exposing a missing hypothesis stays below refuted).
    record = propose_deterministic_counterexamples(
        _claim(MAX_CLAIM, ["f is continuous", "f: R -> R"])
    )[0]
    assert record["satisfies_all_claim_hypotheses"] is True
    assert record["refutes_claim"] is False
    # any non-trivial hypothesis (it might exclude the witness): fail closed.
    assert (
        propose_deterministic_counterexamples(
            _claim(MAX_CLAIM, ["f is continuous", "f is bounded"])
        )
        == []
    )


def test_run_records_counterexample_and_validates(tmp_path: Path) -> None:
    from espresso_proof.io import write_text
    from espresso_proof.orchestrator import orchestrate_run
    from espresso_proof.validators import validate_run

    input_path = tmp_path / "input.md"
    write_text(input_path, MAX_CLAIM)
    result = orchestrate_run(
        project="probe_missing_hyp",
        input_path=input_path,
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    validate_run(run_dir)
    bank = read_yaml(run_dir / "counterexamples.yaml")
    exposing = [
        t for t in bank["tests"] if t.get("exposes_missing_hypothesis") is True
    ]
    assert exposing, "the missing-hypothesis record must reach the bank"
    assert all(t["refutes_claim"] is False for t in exposing)
    # the grade must NOT become refuted from this record.
    grade = read_yaml(run_dir / "idea_grade.yaml")
    assert grade["status"] != "refuted"


def test_cycle_clears_the_no_activity_finding(tmp_path: Path) -> None:
    from espresso_proof.loop_harness import run_loop_cycle
    from espresso_proof.loop_models import ReviewReport
    from espresso_proof.loop_reviewers import run_review_pass

    cases_file = tmp_path / "cases.yaml"
    write_yaml(
        cases_file,
        {
            "cases": [
                {
                    "case_id": "continuous_max_missing_hypothesis",
                    "claim": MAX_CLAIM.strip(),
                    "case_family": "missing_hypothesis",
                    "expected_difficulty": "normal",
                    "known_truth_status": "evaluator_only_false",
                    "what_this_case_tests": [
                        "missing-hypothesis detection",
                        "dropped-hypothesis non-refutation",
                    ],
                    "must_not_claim": [
                        "a dropped-hypothesis counterexample refutes the claim"
                    ],
                }
            ]
        },
    )
    cycle_dir = run_loop_cycle(
        out_root=tmp_path / "loop_runs", mode="smoke", cases_path=cases_file
    )
    refutations = read_yaml(cycle_dir / "refutations.yaml")
    records = [
        r
        for r in refutations["refutations"]
        if r["case_id"] == "continuous_max_missing_hypothesis"
    ]
    assert records, "counterexample activity must reach the refutation ledger"
    assert all(r["failure_class"] == "theorem_condition_gap" for r in records)
    run_review_pass(cycle_dir)
    report = ReviewReport.model_validate(
        read_yaml(cycle_dir / "review_reports" / "counterexample.yaml")
    )
    assert not any(
        "no counterexample activity" in f.what_happened
        and "continuous_max_missing_hypothesis" in f.what_happened
        for f in report.actionable_findings
    )


def test_replay_probe_missing_hypothesis(tmp_path: Path) -> None:
    from espresso_proof.loop_replay import (
        _probe_missing_hypothesis_counterexample_recorded as probe,
    )

    ok, note = probe(tmp_path)
    assert not ok and "no counterexamples.yaml" in note
    write_yaml(tmp_path / "counterexamples.yaml", {"claim_id": "c", "tests": []})
    ok, note = probe(tmp_path)
    assert not ok and "empty counterexample bank" in note
    base = {
        "id": "t1",
        "operator_id": "p",
        "example_id": "e",
        "satisfies_all_claim_hypotheses": "unclear",
        "target": "theorem",
        "result": "failed",
        "refutes_claim": False,
        "refutes_step_only": False,
        "exposes_missing_hypothesis": True,
        "missing_hypothesis": "compact domain",
        "explanation": "x",
        "repaired_claim_suggestion": "on [a, b]",
    }
    # missing repair suggestion fails closed.
    write_yaml(
        tmp_path / "counterexamples.yaml",
        {"claim_id": "c", "tests": [{**base, "repaired_claim_suggestion": ""}]},
    )
    ok, note = probe(tmp_path)
    assert not ok and "repaired-claim suggestion" in note
    # a refutation claim on the exposing record fails closed.
    write_yaml(
        tmp_path / "counterexamples.yaml",
        {
            "claim_id": "c",
            "tests": [{**base, "refutes_claim": True, "satisfies_all_claim_hypotheses": True}],
        },
    )
    ok, note = probe(tmp_path)
    assert not ok and "must stay below refuted" in note
    write_yaml(tmp_path / "counterexamples.yaml", {"claim_id": "c", "tests": [base]})
    ok, note = probe(tmp_path)
    assert ok and "no refutation claimed" in note
