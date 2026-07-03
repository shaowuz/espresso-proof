"""Validators — internal topological slice 2/6 (callees precede callers). Imported via espresso_proof.kernel.validators."""


from __future__ import annotations

import re
from pathlib import Path


from espresso_proof.kernel.io import path_exists, read_yaml
from espresso_proof.kernel.models import (
    Claim,
    ComputationVerificationReport,
    CounterexampleReport,
    FailureClassificationReport,
    FailurePatternAudit,
    LiteratureRetrievalReport,
    MathematicalJudgmentReport,
    ExampleFeedbackSummary,
    HumanExampleFeedback,
    IdeaGrade,
    IdeaStatus,
    RunManifest,
    StrategyBrief,
    StrategySynthesis,
    TimeAllocationPlan,
    UpdateInboxReport,
)
from espresso_proof.kernel.pressure_status import (
    is_admissible_refutation,
)
from espresso_proof.kernel.proof_language import contains_proof_language
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.validation.manifest import validate_relative_artifact_path


from espresso_proof.kernel.validators._part1 import validate_yaml_model


def _first_forbidden_proof_language(text: str) -> str | None:
    canonical_phrase = contains_proof_language(text)
    if canonical_phrase:
        return canonical_phrase
    forbidden_patterns = {
        r"\bq\.?e\.?d\.?\b": "proof-close",
        r"\bverified\b": "verified",
        r"\b(?:the\s+)?(?:theorem|claim|result)\s+(?:is|was|has\s+been)\s+settled\b": "settled result",
        r"\bsettled\s+the\s+(?:theorem|claim|result)\b": "settled result",
        r"\bsettles?\s+the\s+(?:theorem|claim|result)\b": "settles result",
        r"\bproof\s+complete\b": "proof complete",
        r"\bwe\s+proved\b": "we proved",
        r"\bproved\s+the\s+theorem\b": "proved the theorem",
        r"\bproven\b": "proven",
        r"\bproves\s+the\s+claim\b": "proves the claim",
        r"\bproves\s+the\s+theorem\b": "proves the theorem",
        r"\bhas\s+been\s+proven\b": "has been proven",
        r"\bthis\s+proof\b": "this proof",
        r"\bcomplete\s+proof\b": "complete proof",
        r"\bfull\s+proof\b": "full proof",
        r"\b(?:route|strategy)\s+(?:is|are|was|were)\s+(?:a\s+)?(?:proof|proof\s+status|source\s+support|evidence)\b": "route/strategy proof status",
        r"\broute\s+(?:is\s+)?(?:a\s+)?proof\b": "route is a proof",
        r"\broute\s+proves\s+the\s+(?:result|claim|theorem)\b": "route proves the result",
        r"\broute\s+establishes\s+the\s+(?:result|claim|theorem)\b": "route establishes the result",
        r"\b(?:route|strategy)\s+(?:show(?:s|ed|ing)?|demonstrat(?:e|es|ed|ing)|verif(?:y|ies|ied|ying)|validat(?:e|es|ed|ing)|support(?:s|ed|ing)?|certif(?:y|ies|ied|ying)|prove(?:s|d|n|ing)?)\s+(?:the\s+)?(?:theorem|claim|result|proof|proof\s+status|source\s+support|evidence)\b": "route/strategy status projection",
        r"\btheorem\s+applicability\s+(?:proves|establishes|confirms)\b": "theorem applicability proves",
        r"\bapplicability\s+(?:proves|establishes)\s+the\s+(?:route|claim|theorem|result)\b": "applicability proves",
        r"\bestablishes\s+the\s+theorem\b": "establishes the theorem",
        r"\bestablishes\s+the\s+claim\b": "establishes the claim",
        r"\bproof\s+establishes\b": "proof establishes",
        # Goal 41: Chinese global proof-completion markers (no \b — Python's
        # word boundary treats CJK as word chars, so \b would never fire inside
        # Chinese prose). Same curation as the display lexicon.
        r"证毕": "证毕",
        r"证明完毕": "证明完毕",
        r"证明完成": "证明完成",
        r"定理得证": "定理得证",
        r"命题得证": "命题得证",
        r"猜想得证": "猜想得证",
        r"定理已证明": "定理已证明",
        r"命题已证明": "命题已证明",
        r"我们证明了该定理": "我们证明了该定理",
        r"我们证明了这个定理": "我们证明了这个定理",
    }
    for pattern, phrase in forbidden_patterns.items():
        if re.search(pattern, text):
            return phrase
    return None


def _contains_forbidden_allocation_token(value, forbidden: set[str]) -> bool:
    if isinstance(value, str):
        normalized = value.lower()
        if _first_forbidden_proof_language(normalized):
            return True
        allocation_only_patterns = [
            r"\bproof-close\b",
            r"\bsettled\b",
            r"\bverified\s+by\s+examples\b",
        ]
        return any(token in normalized for token in forbidden) or any(
            re.search(pattern, normalized) for pattern in allocation_only_patterns
        )
    if isinstance(value, dict):
        return any(
            str(key) in forbidden or _contains_forbidden_allocation_token(item, forbidden)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_forbidden_allocation_token(item, forbidden) for item in value)
    return False


def _validate_time_allocation(
    time_allocation: TimeAllocationPlan | None,
    claim: Claim,
    manifest: RunManifest,
    run_dir: Path,
    strategy_synthesis: StrategySynthesis,
    strategy_brief: StrategyBrief,
) -> None:
    if "time_allocation" in manifest.artifacts:
        raise KernelValidationError("time_allocation must be an optional scheduling artifact")
    if time_allocation is None:
        return
    if manifest.optional_artifacts.get("time_allocation") != "time_allocation.yaml":
        raise KernelValidationError("time_allocation optional artifact must be time_allocation.yaml")
    if time_allocation.claim_id != claim.id:
        raise KernelValidationError("time_allocation claim_id does not match locked claim")
    if time_allocation.run_id != manifest.run_id:
        raise KernelValidationError("time_allocation run_id does not match run manifest")
    if not time_allocation.advisory_only or time_allocation.effect != "scheduling_only":
        raise KernelValidationError("time_allocation must be scheduling-only/advisory")
    if time_allocation.allocation_state != "TIME_ALLOCATION_STARTED":
        raise KernelValidationError("time_allocation must record TIME_ALLOCATION_STARTED")

    artifact_path = run_dir / manifest.optional_artifacts["time_allocation"]
    if not path_exists(artifact_path):
        raise KernelValidationError("time_allocation optional artifact is missing")

    if time_allocation.strategy_artifact == "strategy_brief":
        route_ids = {route.id for route in strategy_brief.top_strategies}
    else:
        route_ids = {route.id for route in strategy_synthesis.routes}
    for entry in time_allocation.entries:
        if entry.route_id is not None and entry.route_id not in route_ids:
            raise KernelValidationError(
                f"time_allocation entry references unknown route_id: {entry.route_id}"
            )
        if entry.action == "bounded_proof_exploration" and not entry.unchecked:
            raise KernelValidationError("bounded proof exploration must be marked unchecked")

    forbidden = {
        "proof_status",
        "source_status",
        "evidence_status",
        "theorem_applicability_status",
        "refutation_status",
        "grade_status",
        "source_backed",
        "checked_evidence",
        "proved",
        "verified",
    }
    raw = read_yaml(artifact_path)
    expected_forbidden_effect = {
        "proof",
        "source",
        "evidence",
        "theorem_applicability",
        "refutation",
        "grade",
    }
    raw_forbidden_effect = raw.get("forbidden_status_effect")
    if (
        not isinstance(raw_forbidden_effect, list)
        or set(raw_forbidden_effect) != expected_forbidden_effect
        or len(raw_forbidden_effect) != len(expected_forbidden_effect)
    ):
        raise KernelValidationError(
            "time_allocation forbidden_status_effect cannot be removed or weakened"
        )
    if _contains_forbidden_allocation_token(raw, forbidden):
        raise KernelValidationError(
            "time_allocation cannot write proof/source/evidence/theorem/refutation/grade status"
        )


def _validate_update_inbox_applied_at_consumption(
    update_inbox: UpdateInboxReport,
    time_allocation: TimeAllocationPlan | None,
    manifest: RunManifest,
) -> None:
    applied_entries = [entry for entry in update_inbox.entries if entry.applied_at]
    if not applied_entries:
        return
    if time_allocation is None:
        raise KernelValidationError(
            "update_inbox applied_at requires time_allocation scheduling consumption"
        )
    time_allocation_dependencies = manifest.dependencies.get("time_allocation", {})
    if "update_inbox" not in time_allocation_dependencies.get("depends_on", []):
        raise KernelValidationError(
            "update_inbox applied_at requires time_allocation dependency on update_inbox"
        )

    allocation_by_route = {
        entry.route_id: entry
        for entry in time_allocation.entries
        if entry.route_id is not None
    }
    for entry in applied_entries:
        if entry.type != "reviewer_objection":
            raise KernelValidationError(
                "update_inbox applied_at is currently valid only for reviewer_objection "
                "scheduling consumption"
            )
        if entry.route_id_or_step_id is None:
            raise KernelValidationError(
                "update_inbox applied_at reviewer_objection requires a route binding"
            )
        allocation = allocation_by_route.get(entry.route_id_or_step_id)
        if allocation is None:
            raise KernelValidationError(
                "update_inbox applied_at requires matching time_allocation route"
            )
        if allocation.action not in {"repair_attempt", "ask_human"}:
            raise KernelValidationError(
                "update_inbox applied_at requires a reviewer-objection time_allocation response"
            )


def _canonical_update_inbox_dedupe_key(entry) -> str:
    route = entry.route_id_or_step_id
    route_key = "whole_claim" if route is None else str(route)
    return "|".join(
        [
            str(entry.type.value if hasattr(entry.type, "value") else entry.type),
            str(entry.locked_claim_id),
            str(entry.origin_artifact),
            str(entry.origin_artifact_id_or_path),
            str(entry.artifact_revision_or_hash),
            route_key,
        ]
    )


def _contains_forbidden_update_inbox_token(value, forbidden_fields: set[str]) -> bool:
    if isinstance(value, str):
        normalized = value.lower()
        if _first_forbidden_proof_language(normalized):
            return True
        if any(
            re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", normalized)
            for token in forbidden_fields
        ):
            return True
        return any(
            re.search(pattern, normalized)
            for pattern in [
                r"\bproof-close\b",
                r"\bsettled\b",
                r"\bverified\s+by\s+examples\b",
            ]
        )
    if isinstance(value, dict):
        return any(
            str(key) in forbidden_fields
            or _contains_forbidden_update_inbox_token(item, forbidden_fields)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_forbidden_update_inbox_token(item, forbidden_fields) for item in value)
    return False


def _validate_update_inbox(
    update_inbox: UpdateInboxReport | None,
    claim: Claim,
    manifest: RunManifest,
    run_dir: Path,
    time_allocation: TimeAllocationPlan | None,
) -> None:
    if "update_inbox" in manifest.artifacts:
        raise KernelValidationError("update_inbox must be an optional scheduling artifact")
    if update_inbox is None:
        return
    if manifest.optional_artifacts.get("update_inbox") != "update_inbox.yaml":
        raise KernelValidationError("update_inbox optional artifact must be update_inbox.yaml")
    if update_inbox.claim_id != claim.id:
        raise KernelValidationError("update_inbox claim_id does not match locked claim")
    if update_inbox.run_id != manifest.run_id:
        raise KernelValidationError("update_inbox run_id does not match run manifest")

    artifact_path = run_dir / manifest.optional_artifacts["update_inbox"]
    raw = read_yaml(artifact_path)
    forbidden_fields = {
        "proof_status",
        "source_backed",
        "source_status",
        "checked_evidence",
        "example_checked",
        "example_survived",
        "example_failed",
        "counterexample_refutes_claim",
        "theorem_applicability_status",
        "grade_status",
        "verified",
        "proved",
        "complete_proof",
        "final_status",
        "memory_evidence",
    }
    if _contains_forbidden_update_inbox_token(raw, forbidden_fields):
        raise KernelValidationError("update_inbox cannot write mathematical status fields")

    for entry in update_inbox.entries:
        if entry.locked_claim_id != claim.id:
            raise KernelValidationError("update_inbox entry locked_claim_id does not match claim")
        if entry.effect != "scheduling_only" or entry.advisory_only is not True:
            raise KernelValidationError("update_inbox entries must be scheduling-only/advisory")
        if entry.dedupe_key != _canonical_update_inbox_dedupe_key(entry):
            raise KernelValidationError("update_inbox entry dedupe_key is not canonical")
        validate_relative_artifact_path(
            f"update_inbox.{entry.id}.origin_artifact",
            entry.origin_artifact,
            path_name="origin artifact",
        )
    _validate_update_inbox_applied_at_consumption(
        update_inbox,
        time_allocation,
        manifest,
    )


def _load_optional_human_feedback(
    run_dir: Path,
    manifest: RunManifest,
) -> tuple[HumanExampleFeedback | None, ExampleFeedbackSummary | None]:
    optional = manifest.optional_artifacts
    feedback = None
    summary = None
    if "human_example_feedback" in optional:
        feedback = validate_yaml_model(
            run_dir / optional["human_example_feedback"],
            HumanExampleFeedback,
        )
    if "example_feedback_summary" in optional:
        summary = validate_yaml_model(
            run_dir / optional["example_feedback_summary"],
            ExampleFeedbackSummary,
        )
    return feedback, summary


def _load_optional_computation_verification(
    run_dir: Path,
    manifest: RunManifest,
) -> ComputationVerificationReport | None:
    optional = manifest.optional_artifacts
    if "computation_verification" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["computation_verification"],
        ComputationVerificationReport,
    )


def _load_optional_failure_classification(
    run_dir: Path,
    manifest: RunManifest,
) -> FailureClassificationReport | None:
    optional = manifest.optional_artifacts
    if "failure_classification" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["failure_classification"],
        FailureClassificationReport,
    )


def _has_admissible_counterexample(counterexamples: CounterexampleReport) -> bool:
    """Single source of truth for "what counts as a real refutation".

    True iff some counterexample is an admissible CHECKED refutation: it satisfies
    every claim hypothesis (``satisfies_all_claim_hypotheses is True``) AND refutes
    the claim. ``refutes_claim`` already requires a checked ``failed`` result and
    forbids dropped-hypothesis / proof-step-only cases at the model level
    (``CounterexampleTest.validate_refutation_scope``). Both the failure-
    classification gate and the idea-grade ``refuted`` gate use this, so the
    refutation definition cannot drift between them.
    """
    return any(
        is_admissible_refutation(test)
        for test in counterexamples.tests
    )


def _validate_failure_classification(
    failure_classification: FailureClassificationReport | None,
    counterexamples: CounterexampleReport,
    claim: Claim,
) -> None:
    """Failure classification is advisory routing; it must not fabricate a refutation.

    Only runs when the optional artifact is present. Rules:

    - claim_id must match the locked claim;
    - a ``claim_refuted`` classification requires an admissible all-hypotheses
      counterexample (satisfies_all_claim_hypotheses is True and refutes_claim),
      so a dropped-hypothesis or proof-step failure can never be promoted to a
      claim refutation through routing.

    The report model carries no source/evidence/proof status field, so a
    classification cannot upgrade any status by construction.
    """
    if failure_classification is None:
        return
    if failure_classification.claim_id != claim.id:
        raise KernelValidationError(
            "failure_classification claim_id does not match locked claim"
        )
    claims_refuted = any(
        item.category.value == "claim_refuted"
        for item in failure_classification.classifications
    )
    if claims_refuted and not _has_admissible_counterexample(counterexamples):
        raise KernelValidationError(
            "failure_classification marks claim_refuted without an admissible "
            "all-hypotheses counterexample"
        )


def _validate_refuted_grade_admissible(
    idea_grade: IdeaGrade,
    counterexamples: CounterexampleReport,
) -> None:
    """A claim may be graded ``refuted`` ONLY when an admissible checked
    counterexample exists: a counterexample that satisfies every claim hypothesis
    AND refutes the claim (``refutes_claim`` already requires a checked ``failed``
    result and forbids dropped-hypothesis / proof-step-only cases at the model
    level). This makes ``refuted`` a kernel-earned status, not something model
    prose, a reviewer objection, memory, or a forged grade can assert -- and it
    retroactively hardens the subgoal-graph ``contradicted`` gate, which trusts
    ``idea_grade.status == refuted``.
    """
    if idea_grade.status != IdeaStatus.refuted:
        return
    if not _has_admissible_counterexample(counterexamples):
        raise KernelValidationError(
            "idea_grade.status=refuted requires an admissible checked counterexample "
            "(satisfies every claim hypothesis and refutes the claim); a route attack, "
            "reviewer objection, dropped-hypothesis example, or unchecked counterexample "
            "cannot refute the claim"
        )


def _load_optional_mathematical_judgment(
    run_dir: Path,
    manifest: RunManifest,
) -> MathematicalJudgmentReport | None:
    optional = manifest.optional_artifacts
    if "mathematical_judgment" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["mathematical_judgment"],
        MathematicalJudgmentReport,
    )


def _validate_mathematical_judgment(
    mathematical_judgment: MathematicalJudgmentReport | None,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    """The mathematical judgment layer is advisory; it must never upgrade status.

    Only runs when the optional artifact is present. The report model carries no
    source/evidence/proof status field and StrictBaseModel forbids extras, so a
    YAML that smuggles in ``source_backed`` / checked evidence / a proof status is
    already rejected at load (validate_yaml_model). Here we additionally bind the
    report to this run: claim_id must match the locked claim and run_id must match
    the manifest, so a judgment for another claim/run cannot be attached here.
    """
    if mathematical_judgment is None:
        return
    if mathematical_judgment.claim_id != claim.id:
        raise KernelValidationError(
            "mathematical_judgment claim_id does not match locked claim"
        )
    if mathematical_judgment.run_id != manifest.run_id:
        raise KernelValidationError(
            "mathematical_judgment run_id does not match the run manifest"
        )


def _load_optional_failure_pattern_audit(
    run_dir: Path,
    manifest: RunManifest,
) -> FailurePatternAudit | None:
    optional = manifest.optional_artifacts
    if "failure_pattern_audit" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["failure_pattern_audit"],
        FailurePatternAudit,
    )


def _validate_failure_pattern_audit(
    failure_pattern_audit: FailurePatternAudit | None,
    claim: Claim,
    manifest: RunManifest,
) -> None:
    """The failure-pattern audit is advisory; it must not relabel or upgrade.

    Only runs when the optional artifact is present. Rules (fail closed):

    - claim_id / run_id must bind to this run;
    - every matched pattern_id must exist in the canonical SEED bank, and its
      tier / action / family / enforced_by must equal the bank verbatim -- so a
      run (or memory, or a human edit) can NEVER promote a candidate pattern to
      ``hard`` or change what it is allowed to do;
    - a ``hard`` match must point at an EXISTING kernel validator
      (``enforced_by`` in ENFORCING_VALIDATORS); it adds no new blocking.

    The audit model carries no source/evidence/proof status field (and extras are
    forbidden), so a match can never upgrade any status or refute the claim.
    """
    if failure_pattern_audit is None:
        return
    # Local import: failure_pattern_bank imports models+io only (no cycle).
    from espresso_proof.kernel.failure_pattern_bank import ENFORCING_VALIDATORS, seed_failure_pattern_bank

    if failure_pattern_audit.claim_id != claim.id:
        raise KernelValidationError(
            "failure_pattern_audit claim_id does not match locked claim"
        )
    if failure_pattern_audit.run_id != manifest.run_id:
        raise KernelValidationError(
            "failure_pattern_audit run_id does not match the run manifest"
        )
    canonical = {p.id: p for p in seed_failure_pattern_bank().patterns}
    for match in failure_pattern_audit.matches:
        pattern = canonical.get(match.pattern_id)
        if pattern is None:
            raise KernelValidationError(
                f"failure_pattern_audit references unknown pattern {match.pattern_id!r}"
            )
        if (
            match.tier != pattern.tier
            or match.action != pattern.action
            or match.family != pattern.family
            or match.enforced_by != pattern.enforced_by
        ):
            raise KernelValidationError(
                f"failure_pattern_audit relabels pattern {match.pattern_id!r} "
                "(tier/action/family/enforced_by must equal the canonical bank)"
            )
        if match.tier.value == "hard" and match.enforced_by not in ENFORCING_VALIDATORS:
            raise KernelValidationError(
                f"hard pattern {match.pattern_id!r} must be enforced by an existing "
                "kernel validator"
            )


def _load_optional_literature_retrieval(
    run_dir: Path,
    manifest: RunManifest,
) -> LiteratureRetrievalReport | None:
    optional = manifest.optional_artifacts
    if "literature_retrieval" not in optional:
        return None
    return validate_yaml_model(
        run_dir / optional["literature_retrieval"],
        LiteratureRetrievalReport,
    )


__all__ = [
    '_canonical_update_inbox_dedupe_key',
    '_contains_forbidden_allocation_token',
    '_contains_forbidden_update_inbox_token',
    '_first_forbidden_proof_language',
    '_has_admissible_counterexample',
    '_load_optional_computation_verification',
    '_load_optional_failure_classification',
    '_load_optional_failure_pattern_audit',
    '_load_optional_human_feedback',
    '_load_optional_literature_retrieval',
    '_load_optional_mathematical_judgment',
    '_validate_failure_classification',
    '_validate_failure_pattern_audit',
    '_validate_mathematical_judgment',
    '_validate_refuted_grade_admissible',
    '_validate_time_allocation',
    '_validate_update_inbox',
    '_validate_update_inbox_applied_at_consumption',
]
