"""Focused evidence-rule validators for the Espresso Proof kernel.

Extracted verbatim from validators.py without behavior change. Each function
takes already-parsed artifact models and raises KernelValidationError on a
violation, preserving the exact messages other code and tests depend on.

Evidence invariants enforced here:

- unchecked examples cannot justify survived/failed tests,
- counterexample tests must trace to known examples/counterexamples,
- machine-refuted computation checks cannot claim checked/source_checked.

The counterexample refutation-scope rules (a dropped-hypothesis failure and a
proof-step attack cannot refute the original claim) are enforced authoritatively
at parse time by CounterexampleTest.validate_refutation_scope in models.py, so
they are intentionally not duplicated here.
"""

from __future__ import annotations

from espresso_proof.kernel.models import (
    ComputationChecks,
    ComputationStatus,
    ComputationVerificationReport,
    CounterexampleReport,
    ExampleSelection,
    ExampleStatus,
    IdeaGrade,
    TestReport,
    VerificationVerdict,
)
from .errors import KernelValidationError


def _validate_example_evidence(
    computations: ComputationChecks,
    counterexamples: CounterexampleReport,
    idea_grade: IdeaGrade,
) -> None:
    tests_by_id = {test.id: test for test in counterexamples.tests}
    checks_by_example: dict[str, list[str]] = {}
    for check in computations.checks:
        checks_by_example.setdefault(check.example_id, []).append(check.status.value)
    checked_examples = {
        example_id
        for example_id, statuses in checks_by_example.items()
        if statuses and all(status in {"checked", "source_checked"} for status in statuses)
    }

    evidence_test_ids = set(idea_grade.survived_tests) | set(idea_grade.failed_tests)
    for test_id in evidence_test_ids:
        test = tests_by_id.get(test_id)
        if test is None:
            raise KernelValidationError(f"{test_id}: idea grade references unknown test")
        if test.result not in {ExampleStatus.survived, ExampleStatus.failed}:
            raise KernelValidationError(f"{test_id}: evidence test result is not survived/failed")
        if test.example_id not in checked_examples:
            raise KernelValidationError(
                f"{test_id}: unchecked example {test.example_id} cannot support survived/failed evidence"
            )


def _validate_test_report_evidence(
    computations: ComputationChecks,
    examples: ExampleSelection,
    counterexamples: CounterexampleReport,
    tests: TestReport,
) -> None:
    example_ids = {example.id for example in examples.examples}
    counterexamples_by_id = {test.id: test for test in counterexamples.tests}
    counterexample_ids = set(counterexamples_by_id)
    checks_by_example: dict[str, list[str]] = {}
    for check in computations.checks:
        checks_by_example.setdefault(check.example_id, []).append(check.status.value)
    checked_examples = {
        example_id
        for example_id, statuses in checks_by_example.items()
        if statuses and all(status in {"checked", "source_checked"} for status in statuses)
    }

    for test in tests.tests:
        unknown_examples = set(test.uses_examples).difference(example_ids)
        if unknown_examples:
            raise KernelValidationError(f"{test.id}: unknown example ids {sorted(unknown_examples)}")
        unknown_counterexamples = set(test.uses_counterexamples).difference(counterexample_ids)
        if unknown_counterexamples:
            raise KernelValidationError(
                f"{test.id}: unknown counterexample ids {sorted(unknown_counterexamples)}"
            )
        if test.result in {ExampleStatus.survived, ExampleStatus.failed}:
            if test.evidence_status not in {
                ComputationStatus.checked,
                ComputationStatus.source_checked,
            }:
                raise KernelValidationError(
                    f"{test.id}: survived/failed tests require checked/source_checked evidence"
                )
            for example_id in test.uses_examples:
                if example_id not in checked_examples:
                    raise KernelValidationError(
                        f"{test.id}: unchecked example {example_id} cannot support test result"
                    )
            for counterexample_id in test.uses_counterexamples:
                counterexample = counterexamples_by_id[counterexample_id]
                if counterexample.example_id not in checked_examples:
                    raise KernelValidationError(
                        f"{test.id}: unchecked counterexample example "
                        f"{counterexample.example_id} cannot support test result"
                    )


def _require_recomputed_computation(result: ComputationVerificationResult) -> None:
    """Fail closed unless the kernel re-derives this confirming result itself."""

    from espresso_proof.kernel.recompute import recompute_computation_check

    recomputed = recompute_computation_check(result.kind, result.expression, result.expected)
    if recomputed is None:
        raise KernelValidationError(
            f"computation check {result.check_id} claims a confirmed machine result but the "
            f"kernel cannot re-derive it (kind={result.kind!r}); a declared verdict is not "
            f"machine evidence"
        )
    if recomputed["verdict"] != VerificationVerdict.confirmed.value:
        raise KernelValidationError(
            f"computation check {result.check_id} claims verdict 'confirmed' but the kernel "
            f"re-derived {recomputed['verdict']!r} for the recorded expression; a checked "
            f"status cannot rest on a result the kernel does not reproduce"
        )
    if recomputed["backend"] != result.backend:
        raise KernelValidationError(
            f"computation check {result.check_id} names backend {result.backend!r} but the "
            f"kernel re-derived it with {recomputed['backend']!r}; a confirming result cannot "
            f"be attributed to a backend that did not produce it"
        )
    if recomputed["computed"] != result.computed:
        raise KernelValidationError(
            f"computation check {result.check_id} records computed {result.computed!r} but the "
            f"kernel re-derived {recomputed['computed']!r} for the same expression"
        )


def _validate_computation_verification(
    computations: ComputationChecks,
    verification: ComputationVerificationReport | None,
) -> None:
    """Enforce honesty between machine verification and claimed computation status.

    Rules:

    - every verification result must reference a real check;
    - a check refuted by the machine cannot claim checked/source_checked;
    - a check that carries a machine_check spec and claims ``checked`` must have
      a confirming verification result (no asserting evidence the backend could
      have checked but did not confirm);
    - an executed, confirming result must survive the KERNEL re-deriving it.

    The recompute rule is the one that makes the rest mean anything. ``executed``
    and ``verdict`` are written by whoever wrote the file, and this artifact
    backs the grade-level ``computation_checked`` and a ``scoped_checked``
    subgoal node, so believing them handed out a checked status for a
    hand-written line — reproduced end to end during a hostile review.

    A MISSING artifact is not an exemption. This validator used to return early
    when ``computation_verification.yaml`` was absent, so a check claiming
    ``checked`` with a machine_check spec validated as long as the artifact that
    would have contradicted it was simply not shipped — the whole gate was
    opt-in for exactly the entries it existed to stop. The machine_check rule
    now runs unconditionally; with no artifact, nothing is confirmed, and a
    machine-checkable ``checked`` claim fails closed.
    """
    evidence_statuses = {ComputationStatus.checked, ComputationStatus.source_checked}
    if verification is None:
        for check in computations.checks:
            if check.machine_check is not None and check.status == ComputationStatus.checked:
                raise KernelValidationError(
                    f"computation check {check.id} claims 'checked' with a machine_check "
                    "spec but the run carries no computation_verification artifact; "
                    "omitting the machine's answer does not stand in for a confirming one"
                )
        return
    checks_by_id = {check.id: check for check in computations.checks}
    confirmed = {
        result.check_id
        for result in verification.results
        if result.verdict == VerificationVerdict.confirmed
    }
    for result in verification.results:
        check = checks_by_id.get(result.check_id)
        if check is None:
            raise KernelValidationError(
                f"computation_verification references unknown check id: {result.check_id}"
            )
        if result.executed and result.verdict == VerificationVerdict.confirmed:
            _require_recomputed_computation(result)
        if result.verdict == VerificationVerdict.refuted and check.status in evidence_statuses:
            raise KernelValidationError(
                f"computation check {result.check_id} claims status "
                f"'{check.status.value}' but machine verification refuted it "
                f"(computed {result.computed!r} != expected {result.expected!r})"
            )
    for check in computations.checks:
        if check.machine_check is not None and check.status == ComputationStatus.checked:
            if check.id not in confirmed:
                raise KernelValidationError(
                    f"computation check {check.id} claims 'checked' with a machine_check "
                    f"spec but has no confirming machine verification result"
                )
