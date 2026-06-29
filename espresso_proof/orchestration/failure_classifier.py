"""Failure Classifier: turn reviewer objections and failed tests into typed,
actionable failure categories with routing.

Step 5 (AGENT_AUTOMATION_ENGINEERING_PLAN; contract
the failure classifier design notes).

This is advisory-only and deterministic: it reads already-validated run artifacts
and classifies the current failure signals into categories that route the next
action. It NEVER upgrades proof/source/evidence status (the report model carries
no status field), and it classifies ``claim_refuted`` only from an admissible
all-hypotheses counterexample -- a dropped-hypothesis or proof-step failure is
never promoted to a claim refutation here (the validator re-checks this).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import FailureClassificationReport

_UNCHECKED_COMPUTATION = {"inconclusive", "human_needed"}
_WEAK_SOURCE = {"unverified", "not_found"}


def build_failure_classification(run_dir: Path) -> dict:
    """Read a run's artifacts and return a deterministic failure-classification dict."""
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts", {})
    optional = manifest.get("optional_artifacts", {})

    def artifact(key: str) -> dict:
        return read_yaml(run_dir / artifacts[key])

    idea_grade = artifact("idea_grade")
    review = artifact("review")
    counterexamples = artifact("counterexamples")
    source_grounding = artifact("source_grounding")
    computations = artifact("computation_checks")
    loop_control = artifact("loop_control")

    classifications: list[dict] = []
    seen: set[str] = set()

    def add(category: str, signal: str, route: str, explanation: str) -> None:
        if category in seen:
            return
        seen.add(category)
        classifications.append(
            {
                "category": category,
                "signal": signal,
                "recommended_route": route,
                "explanation": explanation,
            }
        )

    # Most-specific first.
    if idea_grade.get("status") == "unclear":
        add(
            "claim_ambiguous",
            "idea_grade.status=unclear",
            "Understanding Claim",
            "The claim is not precise enough to test; lock a precise statement first.",
        )

    tests = counterexamples.get("tests", [])
    if any(
        test.get("satisfies_all_claim_hypotheses") is True and test.get("refutes_claim") is True
        for test in tests
    ):
        add(
            "claim_refuted",
            "admissible all-hypotheses counterexample",
            "stop / report refutation",
            "A counterexample satisfying all claim hypotheses refutes the claim.",
        )
    if any(test.get("refutes_step_only") or test.get("satisfies_all_claim_hypotheses") is False for test in tests):
        add(
            "strategy_failed_by_example",
            "counterexample pressure (proof-step or dropped-hypothesis)",
            "Strategy Generator",
            "An example attacks a proof step or drops a hypothesis; narrow or repair the route, not the claim.",
        )

    source_statuses = {
        ref.get("status") for ref in source_grounding.get("source_refs", []) if ref.get("status")
    } | {step.get("status") for step in source_grounding.get("steps", []) if step.get("status")}
    if "misapplied" in source_statuses:
        add(
            "misapplied_source",
            "source_grounding status=misapplied",
            "Source Matching / Referencing",
            "A cited source is misapplied; re-match the source or downgrade the step.",
        )

    theorem_rel = optional.get("theorem_applicability")
    if theorem_rel:
        theorem_applicability = read_yaml(run_dir / theorem_rel)
        if any(
            usage.get("blocks_source_backed") or usage.get("applicability_status") != "applicable"
            for usage in theorem_applicability.get("usages", [])
        ) or any(test.get("exposes_missing_hypothesis") for test in tests):
            add(
                "theorem_condition_gap",
                "blocking theorem applicability usage / missing hypothesis",
                "Theorem Applicability Auditor / claim repair",
                "A theorem condition is unmet; check hypotheses or repair the claim.",
            )
    elif any(test.get("exposes_missing_hypothesis") for test in tests):
        add(
            "theorem_condition_gap",
            "counterexample exposes a missing hypothesis",
            "Theorem Applicability Auditor / claim repair",
            "A test exposes a missing hypothesis; check theorem conditions.",
        )

    if (idea_grade.get("source_gaps") or []) or (source_statuses & _WEAK_SOURCE):
        add(
            "source_gap",
            "idea_grade.source_gaps / weak source status",
            "Source Matching",
            "An essential source is unverified or not found; pin the exact source.",
        )

    if any(check.get("status") in _UNCHECKED_COMPUTATION for check in computations.get("checks", [])):
        add(
            "computation_unchecked",
            "computation_checks status inconclusive/human_needed",
            "Examples / Evidence Auditor",
            "A computation is unchecked; it cannot support survived/failed evidence.",
        )

    human_gate = None
    prover_rel = optional.get("prover_loop")
    if prover_rel:
        human_gate = read_yaml(run_dir / prover_rel).get("human_gate_status")
    if human_gate == "pending":
        add(
            "human_blocked",
            "prover_loop human_gate_status=pending",
            "Human gate",
            "Work is paused at an unresolved human gate.",
        )

    if loop_control.get("budget_exhausted") is True:
        add(
            "budget_exhausted",
            "loop_control.budget_exhausted=true",
            "stop / ask human",
            "The loop budget is exhausted; stop or ask the human instead of looping.",
        )
    if (
        loop_control.get("no_new_counterexamples_cycles", 0) >= 2
        and loop_control.get("no_new_references_cycles", 0) >= 2
        and loop_control.get("grade_delta_last_two", 0) <= 0
    ):
        add(
            "no_progress",
            "loop_control: >=2 cycles with no new counterexamples/references and no grade gain",
            "stop / ask human",
            "Repeated cycles made no progress; stop or ask the human rather than continue.",
        )

    failed_reviewers = [
        reviewer.get("reviewer_id")
        for reviewer in review.get("reviewers", [])
        if reviewer.get("verdict") == "fail"
    ]
    if failed_reviewers:
        add(
            "proof_gap",
            f"failed reviewers: {', '.join(failed_reviewers)}",
            "Prover / repair",
            (review.get("strongest_objection") or "Reviewer objection requires repair.").strip(),
        )

    report = {
        "schema_version": 1,
        "claim_id": idea_grade["claim_id"],
        "run_id": manifest["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "classifications": classifications,
        "notes": [
            "Advisory routing only; classification never changes proof/source/evidence status.",
            "claim_refuted requires an admissible all-hypotheses counterexample.",
        ],
    }
    FailureClassificationReport.model_validate(report)
    return report


def classify_failures(run_dir: Path) -> dict:
    """Write failure_classification.yaml and register it as an optional artifact."""
    run_dir = Path(run_dir)
    report = build_failure_classification(run_dir)
    write_yaml(run_dir / "failure_classification.yaml", report)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["failure_classification"] = (
        "failure_classification.yaml"
    )
    write_yaml(manifest_path, manifest)
    return report
