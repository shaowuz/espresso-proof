from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any


REPAIR_ROUTE_PREFIXES = (
    "strategy_repair_",
    "strategy_narrow_",
    "strategy_pin_",
    "strategy_lock_",
)

STANDARD_SCORE_LIFT_LIMIT = 20
REPAIR_SCORE_LIFT_LIMIT = 30
HIGH_SCORE_THRESHOLD = 75
HIGH_SCORE_ALLOWED_IDEA_STATUSES = {"promising", "fragile"}

CHECKED_COMPUTATION_STATUSES = {"checked", "source_checked"}
WEAK_SOURCE_STATUSES = {"unverified", "not_found"}
UNCHECKED_SUPPORT_MARKERS = (
    "unchecked",
    "human_needed",
    "human needed",
    "manual_review_needed",
    "manual review needed",
)
PROOF_LIKE_STRATEGY_MARKERS = (
    "proof complete",
    "proof-complete",
    "proof completion",
    "complete proof",
    "complete the proof",
    "completed proof",
    "finish the proof",
    "final proof",
    "full proof",
    "proof candidate",
    "write a proof",
    "write the proof",
    "prove the theorem",
    "prove the claim",
    "prove this",
    "proved the theorem",
    "proved the claim",
    "formal proof",
    "formalize this",
    "formalize the proof",
    "line-by-line proof",
    "source checked argument",
    "source-checked argument",
    "source_checked_argument",
    "computation checked",
    "computation-checked",
    "computation_checked",
    "lean_checked",
    "human_confirmed",
)


class StrategyRuleViolation(ValueError):
    """Raised when strategy scoring safety rules reject a route collection."""


def is_repair_like_route(strategy_id: str) -> bool:
    return strategy_id.startswith(REPAIR_ROUTE_PREFIXES)


def score_lift_limit(strategy: Any) -> int:
    strategy_id = str(_field(strategy, "id", ""))
    if is_repair_like_route(strategy_id):
        return REPAIR_SCORE_LIFT_LIMIT
    return STANDARD_SCORE_LIFT_LIMIT


def collect_strategy_rule_violations(
    routes: Iterable[Any],
    idea_grade: Any,
    *,
    review: Any | None = None,
    computations: Any | None = None,
    counterexamples: Any | None = None,
    tests: Any | None = None,
) -> list[str]:
    """Return scoring and safety violations for StrategyRoute-like objects.

    Intended validator integration:

    - pass either StrategySynthesis.routes or StrategyBrief.top_strategies as
      ``routes``;
    - pass Review when failed-reviewer strategy restrictions should apply;
    - pass ComputationChecks plus CounterexampleReport/TestReport when the
      unchecked-example deep-exploration rule should use direct evidence links.
    """

    violations: list[str] = []
    idea_score = _int_field(idea_grade, "score")
    idea_status = _value(_field(idea_grade, "status", ""))
    failed = failed_reviewers(review)

    for strategy in routes:
        strategy_id = str(_field(strategy, "id", ""))
        strategy_score = _int_field(strategy, "score")
        strategy_status = _value(_field(strategy, "status", ""))
        source_status = _value(_field(strategy, "source_status", ""))
        estimated_effort = _value(_field(strategy, "estimated_effort", ""))
        high_score = strategy_score >= HIGH_SCORE_THRESHOLD

        limit = score_lift_limit(strategy)
        score_lift = strategy_score - idea_score
        if score_lift > limit:
            if is_repair_like_route(strategy_id):
                violations.append(
                    f"{strategy_id}: repair-like strategy score exceeds idea_grade.score "
                    f"by {score_lift}; max lift is {REPAIR_SCORE_LIFT_LIMIT}"
                )
            else:
                violations.append(
                    f"{strategy_id}: strategy score exceeds idea_grade.score by {score_lift}; "
                    f"max lift is {STANDARD_SCORE_LIFT_LIMIT} unless the route is repair-like"
                )

        if high_score and idea_status not in HIGH_SCORE_ALLOWED_IDEA_STATUSES:
            violations.append(
                f"{strategy_id}: score >= {HIGH_SCORE_THRESHOLD} is only allowed when "
                "idea_grade.status is promising or fragile"
            )

        if source_status in WEAK_SOURCE_STATUSES and high_score:
            violations.append(
                f"{strategy_id}: {source_status} source_status cannot have score "
                f">= {HIGH_SCORE_THRESHOLD}"
            )

        if estimated_effort == "deep_exploration" or high_score:
            unchecked_ids, direct_linkage_checked = unchecked_evidence_refs(
                strategy,
                computations=computations,
                counterexamples=counterexamples,
                tests=tests,
            )
            if unchecked_ids:
                violations.append(
                    f"{strategy_id}: unchecked example evidence cannot justify "
                    f"{'deep_exploration' if estimated_effort == 'deep_exploration' else 'high score'}; "
                    f"unchecked refs {sorted(unchecked_ids)}"
                )
            elif not direct_linkage_checked and cites_unchecked_support(strategy):
                violations.append(
                    f"{strategy_id}: explicit unchecked or human_needed support cannot "
                    f"justify {'deep_exploration' if estimated_effort == 'deep_exploration' else 'high score'}"
                )

        if idea_status == "unclear":
            if strategy_id != "strategy_lock_claim":
                violations.append(
                    f"{strategy_id}: unclear claim can only recommend strategy_lock_claim"
                )
            if estimated_effort == "deep_exploration":
                violations.append(
                    f"{strategy_id}: unclear claim cannot recommend deep_exploration"
                )

        if failed:
            if strategy_status == "promising":
                violations.append(
                    f"{strategy_id}: failed reviewers cannot lead to promising strategy; "
                    f"failed reviewers {failed}"
                )
            if is_proof_like_strategy(strategy):
                violations.append(
                    f"{strategy_id}: failed reviewers cannot lead to proof-like strategy; "
                    f"failed reviewers {failed}"
                )

    return violations


def validate_strategy_rules(
    routes: Iterable[Any],
    idea_grade: Any,
    *,
    review: Any | None = None,
    computations: Any | None = None,
    counterexamples: Any | None = None,
    tests: Any | None = None,
    error_type: type[Exception] = StrategyRuleViolation,
) -> None:
    violations = collect_strategy_rule_violations(
        routes,
        idea_grade,
        review=review,
        computations=computations,
        counterexamples=counterexamples,
        tests=tests,
    )
    if violations:
        raise error_type("; ".join(violations))


collect_strategy_safety_violations = collect_strategy_rule_violations
validate_strategy_safety = validate_strategy_rules


def failed_reviewers(review: Any | None) -> list[str]:
    if review is None:
        return []
    failed: list[str] = []
    for reviewer in _iter_field(review, "reviewers"):
        verdict = _value(_field(reviewer, "verdict", ""))
        if verdict in {"fail", "failed"}:
            failed.append(str(_field(reviewer, "reviewer_id", "unknown_reviewer")))
    return failed


def is_proof_like_strategy(strategy: Any) -> bool:
    text = _support_text(strategy, include_next_loop_plan=True)
    return any(marker in text for marker in PROOF_LIKE_STRATEGY_MARKERS)


def cites_unchecked_support(strategy: Any) -> bool:
    text = _support_text(strategy, include_next_loop_plan=True)
    return any(marker in text for marker in UNCHECKED_SUPPORT_MARKERS)


def unchecked_evidence_refs(
    strategy: Any,
    *,
    computations: Any | None = None,
    counterexamples: Any | None = None,
    tests: Any | None = None,
) -> tuple[set[str], bool]:
    """Return unchecked strategy evidence refs and whether direct status was usable.

    Direct status is usable when a strategy evidence id can be linked to a
    counterexample test with computation checks, or to a TestReport item with
    evidence_status. If no direct status is usable, callers should only reject
    routes that explicitly cite unchecked evidence as support.
    """

    refs = strategy_evidence_refs(strategy)
    if not refs:
        return set(), False

    unchecked: set[str] = set()
    direct_linkage_checked = False

    if counterexamples is not None and computations is not None:
        counterexample_tests = _items_by_id(_iter_field(counterexamples, "tests"))
        statuses_by_example = computation_statuses_by_example(computations)
        for ref in refs.intersection(counterexample_tests):
            direct_linkage_checked = True
            example_id = str(_field(counterexample_tests[ref], "example_id", ""))
            statuses = statuses_by_example.get(example_id, [])
            if not statuses or any(status not in CHECKED_COMPUTATION_STATUSES for status in statuses):
                unchecked.add(ref)

    if tests is not None:
        report_tests = _items_by_id(_iter_field(tests, "tests"))
        statuses_by_example = (
            computation_statuses_by_example(computations) if computations is not None else {}
        )
        for ref in refs.intersection(report_tests):
            direct_linkage_checked = True
            test = report_tests[ref]
            evidence_status = _value(_field(test, "evidence_status", ""))
            if evidence_status and evidence_status not in CHECKED_COMPUTATION_STATUSES:
                unchecked.add(ref)
                continue

            for example_id in _string_set(_field(test, "uses_examples", [])):
                statuses = statuses_by_example.get(example_id)
                if statuses is not None and any(
                    status not in CHECKED_COMPUTATION_STATUSES for status in statuses
                ):
                    unchecked.add(ref)
                    break

    return unchecked, direct_linkage_checked


def strategy_evidence_refs(strategy: Any) -> set[str]:
    return _string_set(_field(strategy, "survived_tests", [])) | _string_set(
        _field(strategy, "failed_tests", [])
    )


def computation_statuses_by_example(computations: Any) -> dict[str, list[str]]:
    statuses_by_example: dict[str, list[str]] = {}
    for check in _iter_field(computations, "checks"):
        example_id = str(_field(check, "example_id", ""))
        if not example_id:
            continue
        statuses_by_example.setdefault(example_id, []).append(
            _value(_field(check, "status", ""))
        )
    return statuses_by_example


def _items_by_id(items: Iterable[Any]) -> dict[str, Any]:
    by_id: dict[str, Any] = {}
    for item in items:
        item_id = str(_field(item, "id", ""))
        if item_id:
            by_id[item_id] = item
    return by_id


def _support_text(strategy: Any, *, include_next_loop_plan: bool) -> str:
    fields = ["title", "why_this_route"]
    if include_next_loop_plan:
        fields.append("next_loop_plan")
    return " ".join(str(_field(strategy, field, "")) for field in fields).lower()


def _field(item: Any, field: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(field, default)
    return getattr(item, field, default)


def _iter_field(item: Any, field: str) -> Sequence[Any]:
    value = _field(item, field, [])
    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, str):
        return value
    return []


def _string_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if not isinstance(value, Iterable):
        return set()
    return {str(item) for item in value if str(item)}


def _int_field(item: Any, field: str) -> int:
    value = _field(item, field, 0)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return int(value)


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))
