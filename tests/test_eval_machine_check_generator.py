"""Eval scoring for generated machine-check specs."""

from __future__ import annotations

from pathlib import Path

from espresso_proof.eval.loop import replay_decision
from espresso_proof.eval.runview import RunMetrics, RunView, score_run
from espresso_proof.eval.scorecard import PropOutcome, aggregate_repeat


def _view(checks: list[dict], *, step_status: str = "candidate") -> RunView:
    return RunView(
        prop_id="prop",
        run_dir=Path("."),
        manifest={},
        artifacts={
            "source_grounding": {
                "steps": [
                    {
                        "proof_step_id": "step_sum",
                        "status": step_status,
                    }
                ]
            },
            "computation_checks": {"checks": checks},
            "idea_grade": {
                "status": "fragile",
                "score": 40,
                "proof_language_status": "idea_sketch",
            },
            "locked_claim": {"human_confirmed": False},
        },
    )


def _checked_generated(spec: dict) -> dict:
    return {
        "id": "comp_gen_001",
        "status": "checked",
        "machine_check": spec,
    }


def _finite_spec(target: str) -> dict:
    return {
        "kind": "finite_enumeration",
        "expression": "@n[1:10] sum(1,n)",
        "expected": "@n[1:10] n*(n+1)/2",
        "target_step_id": target,
    }


def test_bound_nonvacuous_confirm_counts_grounding_and_generated_verdict() -> None:
    metrics = score_run(_view([_checked_generated(_finite_spec("step_sum"))]))

    assert metrics.n_grounded == 1
    assert metrics.grounding_coverage == 1.0
    assert metrics.irrelevant_confirmed == 0
    assert metrics.self_generated_verdict == "checked"


def test_vacuous_confirm_is_excluded_and_trips_honesty_gate() -> None:
    metrics = score_run(
        _view(
            [
                _checked_generated(
                    {
                        "kind": "exact_arithmetic",
                        "expression": "0",
                        "expected": "0",
                        "target_step_id": "step_sum",
                    }
                )
            ]
        )
    )
    outcome = PropOutcome(
        prop_id="true_prop",
        known_truth="true",
        has_oracle=True,
        metrics=metrics,
        final_report_text="candidate memo only",
    )

    assert metrics.n_grounded == 0
    assert metrics.irrelevant_confirmed == 1
    assert metrics.self_generated_verdict is None
    assert outcome.honesty_violation is True
    assert aggregate_repeat([outcome])["honesty_violations"] == 1


def test_unresolvable_target_is_excluded_and_flagged() -> None:
    metrics = score_run(_view([_checked_generated(_finite_spec("missing_step"))]))

    assert metrics.n_grounded == 0
    assert metrics.irrelevant_confirmed == 1
    assert metrics.self_generated_verdict is None


def test_generated_wrong_is_scoped_verdict_not_claim_refutation() -> None:
    metrics = score_run(
        _view(
            [
                {
                    "id": "comp_gen_001",
                    "status": "wrong",
                    "machine_check": _finite_spec("step_sum"),
                }
            ]
        )
    )

    assert metrics.n_refuted == 0
    assert metrics.falsification_rate == 0
    assert metrics.self_generated_verdict == "wrong"


def test_generated_wrong_needs_binding_for_verdict_equality() -> None:
    metrics = score_run(
        _view(
            [
                {
                    "id": "comp_gen_001",
                    "status": "wrong",
                    "machine_check": _finite_spec("missing_step"),
                }
            ]
        )
    )

    assert metrics.n_refuted == 0
    assert metrics.self_generated_verdict is None


def test_non_generated_wrong_still_drives_claim_falsification() -> None:
    metrics = score_run(_view([{"id": "comp_001", "status": "wrong"}]))

    assert metrics.n_refuted == 1
    assert metrics.falsification_rate == 1.0


def test_self_generated_confirm_rate_uses_verdict_equality() -> None:
    outcomes = [
        _outcome("true_prop", "true", "checked"),
        _outcome("false_prop", "false", "wrong"),
    ]

    aggregate = aggregate_repeat(outcomes)

    assert aggregate["self_generated_confirm_rate"] == 1.0
    assert aggregate["oracle_accuracy"] == 0.5
    assert aggregate["irrelevant_confirmed"] == 0.0


def test_replay_gate_treats_self_generated_confirm_rate_as_primary() -> None:
    baseline = {
        "noise_band": {"self_generated_confirm_rate": 0.0},
        "metrics": {
            "self_generated_confirm_rate": {"mean": 0.0},
            "false_positive_rate": {"mean": 0.0},
            "irrelevant_confirmed": {"mean": 0.0},
            "grounding_coverage": {"mean": 0.0},
            "oracle_accuracy": {"mean": 0.0},
            "true_positive_rate": {"mean": 0.0},
            "calibration_brier": {"mean": 0.5},
            "calibration_ece": {"mean": 0.5},
            "honesty_violations": 0,
        },
    }
    candidate = {
        "metrics": {
            "self_generated_confirm_rate": {"mean": 1.0},
            "false_positive_rate": {"mean": 0.0},
            "irrelevant_confirmed": {"mean": 0.0},
            "grounding_coverage": {"mean": 0.0},
            "oracle_accuracy": {"mean": 0.0},
            "true_positive_rate": {"mean": 0.0},
            "calibration_brier": {"mean": 0.5},
            "calibration_ece": {"mean": 0.5},
            "honesty_violations": 0,
        }
    }

    decision = replay_decision(baseline, candidate)

    assert decision["accept"] is True
    assert any("self_generated_confirm_rate" in item for item in decision["improved"])


def test_replay_gate_rejects_false_positive_rate_above_zero() -> None:
    baseline = {
        "noise_band": {},
        "metrics": {
            "self_generated_confirm_rate": {"mean": 0.0},
            "false_positive_rate": {"mean": 0.0},
            "honesty_violations": 0,
        },
    }
    candidate = {
        "metrics": {
            "self_generated_confirm_rate": {"mean": 1.0},
            "false_positive_rate": {"mean": 0.1},
            "honesty_violations": 0,
        }
    }

    assert replay_decision(baseline, candidate)["reasons"] == [
        "false_positive_rate != 0 (hard gate)"
    ]


def _outcome(prop_id: str, truth: str, generated_verdict: str | None) -> PropOutcome:
    return PropOutcome(
        prop_id=prop_id,
        known_truth=truth,
        has_oracle=True,
        metrics=RunMetrics(
            n_steps=1,
            n_refuted=0,
            n_grounded=0,
            falsification_rate=0.0,
            grounding_coverage=0.0,
            grade_status="fragile",
            grade_score=50,
            human_confirmed=False,
            proof_language_status="idea_sketch",
            irrelevant_confirmed=0,
            self_generated_verdict=generated_verdict,
        ),
        final_report_text="candidate memo only",
    )
