"""Mathematical Judgment Layer V1: a conservative middle layer that records
mathematical RISK judgments without becoming a proof verifier.

Six-Hour Autonomous Completion Goal, Phase 1
(docs/goals/MATHEMATICAL_JUDGMENT_LAYER_GOAL.md).

This is advisory-only and deterministic: it reads already-validated run artifacts
and classifies mathematical risk into findings that warn, request more evidence,
downgrade confidence, or ask for human review. It NEVER upgrades
proof/source/evidence status -- the report model carries no status field and
StrictBaseModel forbids extras, so by construction a judgment cannot set
source_backed, mark a computation/example checked, mark a claim refuted, upgrade
proof status, or emit proof-complete language. The kernel validators remain the
only gate that earns any status.

Risk kinds (all advisory):

- ``claim_precision_risk``: the claim is not precise enough to test reliably.
- ``theorem_condition_risk``: a cited theorem's hypotheses are not confirmed to
  match the claim (still candidate).
- ``missing_hypothesis_in_prose``: a step's needed theorem requires a hypothesis
  term that is not pinned in the hypothesis ledger.
- ``route_validity_risk``: a top route rests on uncited / candidate-only lemmas.
- ``unsupported_confidence``: the route/grade has no source_backed step and no
  computation_checked evidence, so confidence must stay conservative.
- ``reviewer_objection_decisiveness``: a reviewer objected; route to repair (never
  a claim refutation).
- ``grade_route_conflict``: the heuristic grade is strongly negative
  (likely_false / refuted) yet NO checked counterexample refutes the claim and a
  candidate route is on record -- an unresolved state. The claim may be merely
  unsupported / not-yet-grounded, OR false with a counterexample that has not
  been machine-checked. Advisory: it asks for human judgment and never changes
  the stored grade or any status.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import MathematicalJudgmentReport

_CHECKED_COMPUTATION = {"checked", "source_checked"}


def build_mathematical_judgment(run_dir: Path) -> dict:
    """Read a run's validated artifacts and return a deterministic judgment dict.

    Only reads; never mutates artifacts and never sets any status. Optional
    artifacts (theorem_applicability, strategy_brief) are read defensively.
    """
    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts", {})
    optional = manifest.get("optional_artifacts", {})

    def artifact(key: str) -> dict:
        return read_yaml(run_dir / artifacts[key])

    claim = artifact("locked_claim")
    ledger = artifact("hypothesis_ledger")
    idea_grade = artifact("idea_grade")
    review = artifact("review")
    source_grounding = artifact("source_grounding")
    computations = artifact("computation_checks")
    counterexamples = artifact("counterexamples") if "counterexamples" in artifacts else {}
    strategy_brief = artifact("strategy_brief") if "strategy_brief" in artifacts else {}

    findings: list[dict] = []

    def add(kind: str, tier: str, subject: str, signal: str, action: str, explanation: str) -> None:
        findings.append(
            {
                "kind": kind,
                "tier": tier,
                "subject": subject,
                "signal": signal,
                "recommended_action": action,
                "explanation": explanation,
            }
        )

    # --- claim precision risk -------------------------------------------------
    precision = ledger.get("claim_precision")
    if precision in {"vague", "unclear"} or idea_grade.get("status") == "unclear":
        add(
            "claim_precision_risk",
            "warn",
            claim.get("id", "claim"),
            f"hypothesis_ledger.claim_precision={precision}; idea_grade.status={idea_grade.get('status')}",
            "request_human_review",
            "The claim may not be precise enough to test reliably; lock/clarify it before investing effort.",
        )

    # --- per-step theorem-condition + missing-hypothesis risk -----------------
    # A required hypothesis term that the source statement does not contain is a
    # missing-hypothesis-in-prose risk; an unclear/false hypothesis match is a
    # theorem-condition risk. Neither sets status; both keep the step candidate.
    for step in source_grounding.get("steps", []):
        step_id = step.get("proof_step_id", "step")
        match = (step.get("hypothesis_match") or {}).get("matches")
        mismatches = (step.get("hypothesis_match") or {}).get("mismatches") or []
        if step.get("status") == "source_backed":
            continue  # the kernel already earned it; nothing to flag
        if match in (False, "unclear"):
            add(
                "theorem_condition_risk",
                "block_candidate",
                step_id,
                f"hypothesis_match.matches={match!r}",
                "request_source",
                "The cited theorem's hypotheses are not confirmed to match the claim; "
                "keep this step candidate (not source_backed) until grounded.",
            )
        if mismatches:
            add(
                "missing_hypothesis_in_prose",
                "warn",
                step_id,
                f"{len(mismatches)} required term(s) not found in the source statement",
                "request_source",
                "A required hypothesis term is not present in the cited statement; "
                "pin the hypothesis and re-ground, do not assume the theorem applies.",
            )

    # --- theorem applicability (optional) -------------------------------------
    theorem_rel = optional.get("theorem_applicability")
    if theorem_rel:
        applicability = read_yaml(run_dir / theorem_rel)
        for usage in applicability.get("usages", []):
            if usage.get("blocks_source_backed") or usage.get("applicability_status") != "applicable":
                add(
                    "theorem_condition_risk",
                    "block_candidate",
                    usage.get("proof_step_id", usage.get("source_ref_id", "usage")),
                    f"applicability_status={usage.get('applicability_status')}, "
                    f"blocks_source_backed={usage.get('blocks_source_backed')}",
                    "request_source",
                    "Theorem applicability is unmet or blocking; the step cannot be source_backed "
                    "until the conditions are checked against the claim.",
                )

    # --- unsupported confidence ----------------------------------------------
    any_source_backed = any(
        step.get("status") == "source_backed" for step in source_grounding.get("steps", [])
    )
    any_checked = any(
        check.get("status") in _CHECKED_COMPUTATION for check in computations.get("checks", [])
    )
    if not any_source_backed and not any_checked:
        add(
            "unsupported_confidence",
            "warn",
            idea_grade.get("claim_id", "claim"),
            "no source_backed step and no computation_checked evidence present",
            "downgrade_confidence",
            "The route rests on candidate-only support; confidence must stay conservative "
            "and the score must not be read as evidence of truth.",
        )

    # --- route validity risk --------------------------------------------------
    for route in (strategy_brief.get("top_strategies") or [])[:3]:
        if route.get("source_status") == "candidate" and route.get("main_risk"):
            add(
                "route_validity_risk",
                "info",
                route.get("id", "route"),
                f"route source_status=candidate, score={route.get('score')}",
                "request_source",
                "This route depends on uncited / candidate-only lemmas; ground them before "
                "raising confidence in the route.",
            )

    # --- reviewer objection decisiveness -------------------------------------
    failed = [
        r.get("reviewer_id")
        for r in review.get("reviewers", [])
        if r.get("verdict") == "fail"
    ]
    objection = (review.get("strongest_objection") or "").strip()
    if failed or objection:
        add(
            "reviewer_objection_decisiveness",
            "warn",
            ", ".join([r for r in failed if r]) or "review",
            f"failed reviewers: {', '.join([r for r in failed if r]) or 'none'}",
            "request_human_review",
            "A reviewer objection requires repair or human judgment; an objection is never "
            "a claim refutation and never upgrades status.",
        )

    # --- reviewer approval without a verifier --------------------------------
    # A reviewer PASSING (positive verdict, no failures) is NOT proof
    # verification: if no step is source_backed and no computation is checked,
    # the approval must not be read as evidence. Advisory only; never upgrades.
    reviewer_verdicts = [r.get("verdict") for r in review.get("reviewers", [])]
    reviewers_approve = bool(reviewer_verdicts) and all(
        v == "pass" for v in reviewer_verdicts
    )
    if reviewers_approve and not any_source_backed and not any_checked:
        add(
            "reviewer_approval_without_verifier",
            "warn",
            idea_grade.get("claim_id", claim.get("id", "claim")),
            "all reviewers pass, but no source_backed step and no checked evidence",
            "request_human_review",
            "Reviewer approval is not proof verification: no trusted source or checker "
            "supports this yet, so the approval must not be read as proof.",
        )

    # --- grade / route conflict ----------------------------------------------
    # A strongly-negative heuristic grade (likely_false / refuted) is only honest
    # as "probably false" when something actually refutes the claim. If NO checked
    # counterexample refutes it (every counterexample has refutes_claim=False) yet
    # a candidate proof route exists, the negative grade more likely reflects
    # "unsupported / not yet grounded" than "false". Surface that conflict for
    # human judgment. This NEVER changes the stored grade or any status -- it is a
    # structured, advisory note (no keyword/prose matching).
    grade_status = idea_grade.get("status")
    has_refuting_counterexample = any(
        test.get("refutes_claim") is True for test in counterexamples.get("tests", [])
    )
    has_route = bool(strategy_brief.get("top_strategies"))
    if grade_status in {"likely_false", "refuted"} and not has_refuting_counterexample and has_route:
        add(
            "grade_route_conflict",
            "warn",
            idea_grade.get("claim_id", claim.get("id", "claim")),
            f"idea_grade.status={grade_status}; no counterexample has refutes_claim=true; "
            f"{len(strategy_brief.get('top_strategies') or [])} candidate route(s) present",
            "request_human_review",
            "The claim is graded as likely false, but no checked counterexample refutes it "
            "yet and a candidate route is on record. The grade is a heuristic -- neither a "
            "refutation nor a proof. This is unresolved: the claim may be merely unsupported / "
            "not-yet-grounded, OR false with a counterexample that has not been machine-checked. "
            "Needs human judgment; nothing here changes the grade or any status.",
        )

    report = {
        "schema_version": 1,
        "claim_id": idea_grade.get("claim_id", claim.get("id")),
        "run_id": manifest["run_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "findings": findings,
        "notes": [
            "Advisory mathematical-risk judgment only; never changes "
            "proof/source/evidence status.",
            "block_candidate is a recommendation to keep a step candidate; the kernel "
            "source-backing/applicability validators remain the only gate.",
            "A reviewer objection is never a claim refutation.",
        ],
    }
    MathematicalJudgmentReport.model_validate(report)
    return report


def judge_run(run_dir: Path) -> dict:
    """Write mathematical_judgment.yaml and register it as an optional artifact."""
    run_dir = Path(run_dir)
    report = build_mathematical_judgment(run_dir)
    write_yaml(run_dir / "mathematical_judgment.yaml", report)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["mathematical_judgment"] = (
        "mathematical_judgment.yaml"
    )
    write_yaml(manifest_path, manifest)
    from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures

    refresh_final_report_disclosures(run_dir)
    return report
