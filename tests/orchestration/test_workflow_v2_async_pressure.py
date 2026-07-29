from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.kernel.agent_run_status import set_agent_status
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import CounterexampleTest
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.orchestration.orchestrator import orchestrate_run
from espresso_proof.kernel.pressure_status import build_pressure_status
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.kernel.validators import validate_run

CLAIM = "Every group of order p^2, where p is prime, is abelian."
FIXTURES = Path("tests/fixtures")


def _write_claim(tmp_path: Path, text: str = CLAIM) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(text, encoding="utf-8")
    return path


def _fast_run(tmp_path: Path, name: str = "async_pressure") -> Path:
    result = orchestrate_run(
        project=name,
        input_path=_write_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    run_dir = Path(result["run_dir"])
    validate_run(run_dir)
    return run_dir


def _pressure(run_dir: Path) -> dict[str, dict]:
    pressure = read_yaml(run_dir / "pressure_status.yaml")
    return {entry["kind"]: entry for entry in pressure["entries"]}


def _candidate_counterexample(**overrides) -> dict:
    data = {
        "id": "cex_pending_001",
        "operator_id": "manual_pressure",
        "example_id": "example_001",
        "satisfies_all_claim_hypotheses": "unclear",
        "target": "theorem",
        "result": "failed",
        "refutes_claim": False,
        "refutes_step_only": False,
        "exposes_missing_hypothesis": False,
        "missing_hypothesis": None,
        "support": "candidate pressure only",
        "explanation": "Potential counterexample needs admissibility checking.",
        "repaired_claim_suggestion": None,
    }
    data.update(overrides)
    return data


def _partial_example() -> dict:
    return {
        "id": "ex_partial_001",
        "template_id": "template_001",
        "name": "Partial example",
        "satisfies_all_claim_hypotheses": "unclear",
        "hypothesis_mismatches": [],
        "what_it_tests": ["Whether the route has obvious example pressure."],
        "expected_conclusion": "Claim conclusion should hold if applicable.",
        "observed_or_inferred_behavior": "Not checked.",
        "result_for_current_idea": "not_tested",
        "source_or_reason": "partial async run",
        "nontriviality": "standard",
    }


def test_clear_claim_can_reach_strategy_generator_while_pressure_pending(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(run_dir / manifest["artifacts"]["strategy_brief"])
    pressure = _pressure(run_dir)

    assert brief["top_strategies"]
    assert pressure["examples"]["pressure_state"] == "pending_not_run_or_partial"
    assert pressure["counterexamples"]["pressure_state"] == "pending_not_run_or_partial"
    assert pressure["examples"]["agent_status"] == "not_run"
    assert pressure["counterexamples"]["agent_status"] == "not_run"


def test_pending_pressure_remains_visible(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    pressure = read_yaml(run_dir / "pressure_status.yaml")

    assert read_yaml(run_dir / "run_manifest.yaml")["optional_artifacts"]["pressure_status"] == (
        "pressure_status.yaml"
    )
    assert {entry["pressure_state"] for entry in pressure["entries"]} == {
        "pending_not_run_or_partial"
    }
    validate_run(run_dir)


def test_pressure_status_survives_examples_counterexamples_bank_materialization(
    tmp_path: Path,
) -> None:
    from espresso_proof.kernel.research_bank import is_bank_dir, materialize_run_banks

    run_dir = run_mock_critique(
        "pressure_bank",
        FIXTURES / "all_matrices_diagonalizable" / "input.md",
        tmp_path,
    )
    build_pressure_status(run_dir, late_after_strategy=False)
    validate_run(run_dir)

    converted = materialize_run_banks(run_dir, keys=["examples", "counterexamples"])
    assert converted == ["examples", "counterexamples"]
    validate_run(run_dir)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    pressure = _pressure(run_dir)
    assert pressure["examples"]["artifact_path"] == manifest["artifacts"]["examples"]
    assert pressure["counterexamples"]["artifact_path"] == manifest["artifacts"]["counterexamples"]
    assert is_bank_dir(run_dir / pressure["examples"]["artifact_path"])
    assert is_bank_dir(run_dir / pressure["counterexamples"]["artifact_path"])
    assert pressure["examples"]["late_markers"] == []
    assert pressure["counterexamples"]["late_markers"] == []


def test_missing_examples_are_not_survived_tests(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    grade = read_yaml(run_dir / manifest["artifacts"]["idea_grade"])
    synthesis = read_yaml(run_dir / manifest["artifacts"]["strategy_synthesis"])

    assert read_yaml(run_dir / manifest["artifacts"]["examples"])["examples"] == []
    assert grade["survived_tests"] == []
    assert all(route["survived_tests"] == [] for route in synthesis["routes"])


def test_missing_counterexamples_are_not_no_pressure_evidence(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    pressure = _pressure(run_dir)
    memo = (run_dir / manifest["artifacts"]["final_report"]).read_text(encoding="utf-8")

    assert read_yaml(run_dir / manifest["artifacts"]["counterexamples"])["tests"] == []
    assert pressure["counterexamples"]["pressure_state"] == "pending_not_run_or_partial"
    assert "no counterexamples found" not in memo.lower()
    assert "no-pressure" not in memo.lower()


def test_partial_examples_and_counterexamples_produce_no_survived_or_failed_evidence(
    tmp_path: Path,
) -> None:
    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    examples_path = run_dir / manifest["artifacts"]["examples"]
    counterexamples_path = run_dir / manifest["artifacts"]["counterexamples"]
    examples = read_yaml(examples_path)
    examples["examples"] = [_partial_example()]
    write_yaml(examples_path, examples)
    counterexamples = read_yaml(counterexamples_path)
    counterexamples["tests"] = [_candidate_counterexample()]
    write_yaml(counterexamples_path, counterexamples)
    set_agent_status(run_dir, "examples", "running")
    set_agent_status(run_dir, "counterexamples", "running")
    build_pressure_status(run_dir)
    validate_run(run_dir)

    pressure = _pressure(run_dir)
    grade = read_yaml(run_dir / manifest["artifacts"]["idea_grade"])
    assert pressure["examples"]["pressure_state"] == "pending_not_run_or_partial"
    assert pressure["counterexamples"]["pressure_state"] == "pending_not_run_or_partial"
    assert grade["survived_tests"] == []
    assert grade["failed_tests"] == []


@pytest.mark.parametrize("result", ["not_tested", "inconclusive"])
def test_done_counterexample_partial_candidate_is_not_no_pressure(
    tmp_path: Path, result: str
) -> None:
    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    counterexamples = read_yaml(run_dir / manifest["artifacts"]["counterexamples"])
    counterexamples["tests"] = [
        _candidate_counterexample(
            result=result,
            support=None,
            explanation="Partial candidate; admissibility has not completed.",
        )
    ]
    write_yaml(run_dir / manifest["artifacts"]["counterexamples"], counterexamples)
    set_agent_status(run_dir, "counterexamples", "done")
    build_pressure_status(run_dir)
    validate_run(run_dir)

    pressure = _pressure(run_dir)
    assert pressure["counterexamples"]["pressure_state"] == (
        "candidate_present_needs_admissibility"
    )
    assert pressure["counterexamples"]["pressure_state"] != (
        "completed_no_candidate_found_in_scope"
    )


def test_candidate_needing_admissibility_cannot_refute_claim_or_grade(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="unclear admissibility cannot refute"):
        CounterexampleTest.model_validate(_candidate_counterexample(refutes_claim=True))

    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    counterexamples_path = run_dir / manifest["artifacts"]["counterexamples"]
    counterexamples = read_yaml(counterexamples_path)
    counterexamples["tests"] = [_candidate_counterexample()]
    write_yaml(counterexamples_path, counterexamples)
    set_agent_status(run_dir, "counterexamples", "done")
    build_pressure_status(run_dir)
    grade_path = run_dir / manifest["artifacts"]["idea_grade"]
    grade = read_yaml(grade_path)
    grade["status"] = "refuted"
    write_yaml(grade_path, grade)

    with pytest.raises(KernelValidationError, match="requires an admissible checked counterexample"):
        validate_run(run_dir)


def test_admissible_refutation_requires_failed_all_hypotheses_and_not_step_only() -> None:
    admissible = CounterexampleTest.model_validate(
        _candidate_counterexample(
            id="cex_admissible",
            satisfies_all_claim_hypotheses=True,
            refutes_claim=True,
            result="failed",
        )
    )
    assert admissible.refutes_claim is True

    bad_cases = [
        _candidate_counterexample(
            result="inconclusive",
            satisfies_all_claim_hypotheses=True,
            refutes_claim=True,
        ),
        _candidate_counterexample(refutes_claim=True),
        _candidate_counterexample(
            satisfies_all_claim_hypotheses=True,
            refutes_claim=True,
            refutes_step_only=True,
            target="proof_step",
        ),
    ]
    for bad in bad_cases:
        with pytest.raises(ValidationError):
            CounterexampleTest.model_validate(bad)


def test_dropped_hypothesis_candidate_is_not_claim_refutation(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="dropped-hypothesis"):
        CounterexampleTest.model_validate(
            _candidate_counterexample(
                satisfies_all_claim_hypotheses=False,
                refutes_claim=True,
                target="hypothesis",
            )
        )

    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    counterexamples = read_yaml(run_dir / manifest["artifacts"]["counterexamples"])
    counterexamples["tests"] = [
        _candidate_counterexample(
            satisfies_all_claim_hypotheses=False,
            refutes_claim=False,
            target="hypothesis",
            exposes_missing_hypothesis=True,
            missing_hypothesis="prime-power order",
        )
    ]
    write_yaml(run_dir / manifest["artifacts"]["counterexamples"], counterexamples)
    set_agent_status(run_dir, "counterexamples", "done")
    build_pressure_status(run_dir)
    validate_run(run_dir)

    pressure = _pressure(run_dir)
    assert pressure["counterexamples"]["pressure_state"] == "inadmissible_pressure"
    assert pressure["counterexamples"]["admissible_refutation_count"] == 0


def test_missing_hypothesis_counterexample_pressure_is_not_no_candidate(
    tmp_path: Path,
) -> None:
    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    counterexamples = read_yaml(run_dir / manifest["artifacts"]["counterexamples"])
    counterexamples["tests"] = [
        _candidate_counterexample(
            id="cex_missing_hypothesis_001",
            satisfies_all_claim_hypotheses=True,
            target="theorem",
            result="failed",
            refutes_claim=False,
            refutes_step_only=False,
            exposes_missing_hypothesis=True,
            missing_hypothesis="the candidate exposes an extra needed hypothesis",
            explanation=(
                "The checked pressure exposes a missing hypothesis, so it is visible "
                "pressure but not a claim refutation."
            ),
        )
    ]
    write_yaml(run_dir / manifest["artifacts"]["counterexamples"], counterexamples)
    set_agent_status(run_dir, "counterexamples", "done")
    build_pressure_status(run_dir)
    validate_run(run_dir)

    pressure = _pressure(run_dir)
    grade = read_yaml(run_dir / manifest["artifacts"]["idea_grade"])
    assert pressure["counterexamples"]["pressure_state"] == "inadmissible_pressure"
    assert pressure["counterexamples"]["pressure_state"] != "completed_no_candidate_found_in_scope"
    assert pressure["counterexamples"]["admissible_refutation_count"] == 0
    assert grade["status"] != "refuted"

    raw_pressure = read_yaml(run_dir / "pressure_status.yaml")
    for entry in raw_pressure["entries"]:
        if entry["kind"] == "counterexamples":
            entry["pressure_state"] = "completed_no_candidate_found_in_scope"
            entry["inadmissible_pressure_count"] = 0
            entry["late_markers"] = []
    write_yaml(run_dir / "pressure_status.yaml", raw_pressure)
    with pytest.raises(KernelValidationError, match="pressure_state is stale"):
        validate_run(run_dir)


def test_missing_hypothesis_pressure_cannot_be_admissible_refutation(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="missing-hypothesis pressure cannot refute"):
        CounterexampleTest.model_validate(
            _candidate_counterexample(
                satisfies_all_claim_hypotheses=True,
                target="theorem",
                result="failed",
                refutes_claim=True,
                refutes_step_only=False,
                exposes_missing_hypothesis=True,
                missing_hypothesis="extra needed hypothesis",
            )
        )

    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    counterexamples = read_yaml(run_dir / manifest["artifacts"]["counterexamples"])
    counterexamples["tests"] = [
        _candidate_counterexample(
            id="cex_missing_hypothesis_nonrefuting",
            satisfies_all_claim_hypotheses=True,
            target="theorem",
            result="failed",
            refutes_claim=False,
            refutes_step_only=False,
            exposes_missing_hypothesis=True,
            missing_hypothesis="extra needed hypothesis",
        )
    ]
    write_yaml(run_dir / manifest["artifacts"]["counterexamples"], counterexamples)
    set_agent_status(run_dir, "counterexamples", "done")
    build_pressure_status(run_dir)
    grade_path = run_dir / manifest["artifacts"]["idea_grade"]
    grade = read_yaml(grade_path)
    grade["status"] = "refuted"
    write_yaml(grade_path, grade)

    with pytest.raises(KernelValidationError, match="requires an admissible checked counterexample"):
        validate_run(run_dir)


@pytest.mark.parametrize("target", ["proof_step", "hypothesis", "analogy"])
def test_non_theorem_target_cannot_set_claim_refutation(target: str) -> None:
    with pytest.raises(ValidationError, match="target='theorem'"):
        CounterexampleTest.model_validate(
            _candidate_counterexample(
                satisfies_all_claim_hypotheses=True,
                target=target,
                result="failed",
                refutes_claim=True,
                refutes_step_only=False,
            )
        )


@pytest.mark.parametrize("target", ["proof_step", "hypothesis", "analogy"])
def test_non_theorem_pressure_cannot_support_refuted_grade(
    tmp_path: Path, target: str
) -> None:
    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    counterexamples = read_yaml(run_dir / manifest["artifacts"]["counterexamples"])
    counterexamples["tests"] = [
        _candidate_counterexample(
            id=f"cex_non_theorem_{target}",
            satisfies_all_claim_hypotheses=True,
            target=target,
            result="failed",
            refutes_claim=False,
            refutes_step_only=False,
            explanation=f"{target} pressure only; not a whole-claim refutation.",
        )
    ]
    write_yaml(run_dir / manifest["artifacts"]["counterexamples"], counterexamples)
    set_agent_status(run_dir, "counterexamples", "done")
    build_pressure_status(run_dir)
    validate_run(run_dir)
    pressure = _pressure(run_dir)
    assert pressure["counterexamples"]["pressure_state"] == "inadmissible_pressure"
    assert pressure["counterexamples"]["admissible_refutation_count"] == 0

    grade_path = run_dir / manifest["artifacts"]["idea_grade"]
    grade = read_yaml(grade_path)
    grade["status"] = "refuted"
    write_yaml(grade_path, grade)
    with pytest.raises(KernelValidationError, match="requires an admissible checked counterexample"):
        validate_run(run_dir)


def test_proof_step_attack_is_not_theorem_refutation(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="proof-step attack"):
        CounterexampleTest.model_validate(
            _candidate_counterexample(
                satisfies_all_claim_hypotheses=True,
                refutes_claim=True,
                refutes_step_only=True,
                target="proof_step",
            )
        )

    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    counterexamples = read_yaml(run_dir / manifest["artifacts"]["counterexamples"])
    counterexamples["tests"] = [
        _candidate_counterexample(
            satisfies_all_claim_hypotheses=True,
            refutes_claim=False,
            refutes_step_only=True,
            target="proof_step",
        )
    ]
    write_yaml(run_dir / manifest["artifacts"]["counterexamples"], counterexamples)
    set_agent_status(run_dir, "counterexamples", "done")
    build_pressure_status(run_dir)
    validate_run(run_dir)

    pressure = _pressure(run_dir)
    assert pressure["counterexamples"]["pressure_state"] == "inadmissible_pressure"
    assert pressure["counterexamples"]["admissible_refutation_count"] == 0


@pytest.mark.parametrize("kind", ["examples", "counterexamples"])
def test_tampered_not_run_pressure_cannot_claim_no_candidate_found(
    tmp_path: Path, kind: str
) -> None:
    run_dir = _fast_run(tmp_path)
    pressure = read_yaml(run_dir / "pressure_status.yaml")
    for entry in pressure["entries"]:
        if entry["kind"] == kind:
            entry["pressure_state"] = "completed_no_candidate_found_in_scope"
    write_yaml(run_dir / "pressure_status.yaml", pressure)

    with pytest.raises(KernelValidationError, match="pressure_state is stale"):
        validate_run(run_dir)


def test_pressure_status_missing_required_entry_fails_validation(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    pressure = read_yaml(run_dir / "pressure_status.yaml")
    pressure["entries"] = [entry for entry in pressure["entries"] if entry["kind"] == "examples"]
    write_yaml(run_dir / "pressure_status.yaml", pressure)

    with pytest.raises(KernelValidationError, match="requires exactly examples and counterexamples"):
        validate_run(run_dir)


def test_late_pressure_dedupe_key_changes_with_artifact_revision(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    counterexamples_path = run_dir / manifest["artifacts"]["counterexamples"]
    counterexamples = read_yaml(counterexamples_path)
    counterexamples["tests"] = [
        _candidate_counterexample(
            satisfies_all_claim_hypotheses=False,
            target="hypothesis",
            exposes_missing_hypothesis=True,
            missing_hypothesis="extra hypothesis",
        )
    ]
    write_yaml(counterexamples_path, counterexamples)
    set_agent_status(run_dir, "counterexamples", "done")
    build_pressure_status(run_dir)
    first = _pressure(run_dir)["counterexamples"]["late_markers"][0]

    counterexamples["tests"][0]["explanation"] += " Revised pressure details."
    write_yaml(counterexamples_path, counterexamples)
    build_pressure_status(run_dir)
    second = _pressure(run_dir)["counterexamples"]["late_markers"][0]

    assert first["origin_artifact_id"] == second["origin_artifact_id"]
    assert first["pressure_state"] == second["pressure_state"]
    assert first["artifact_revision_or_hash"] != second["artifact_revision_or_hash"]
    assert first["dedupe_key"] != second["dedupe_key"]


def test_local_late_pressure_marker_without_update_inbox_dependency(tmp_path: Path) -> None:
    run_dir = _fast_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    counterexamples = read_yaml(run_dir / manifest["artifacts"]["counterexamples"])
    counterexamples["tests"] = [
        _candidate_counterexample(
            satisfies_all_claim_hypotheses=False,
            target="hypothesis",
            exposes_missing_hypothesis=True,
            missing_hypothesis="extra hypothesis",
        )
    ]
    write_yaml(run_dir / manifest["artifacts"]["counterexamples"], counterexamples)
    set_agent_status(run_dir, "counterexamples", "done")
    build_pressure_status(run_dir)
    validate_run(run_dir)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    pressure = _pressure(run_dir)
    markers = pressure["counterexamples"]["late_markers"]
    assert "update_inbox" not in manifest["optional_artifacts"]
    assert len(markers) == 1
    marker = markers[0]
    assert marker["effect"] == "scheduling_only"
    assert marker["advisory_only"] is True
    assert marker["update_inbox_entry"] is False
    assert marker["scope"] == "whole_claim"
    assert marker["route_id_or_step_id"] == "whole_claim"
    assert marker["late_after_strategy"] is True
    assert marker["artifact_revision_or_hash"]
    assert "counterexample_pressure:claim_001:counterexamples.yaml:" in marker["dedupe_key"]
    assert marker["dedupe_key"].endswith(":whole_claim:cex_pending_001:inadmissible_pressure")
