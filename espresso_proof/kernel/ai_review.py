"""Advisory "AI review passed" signal.

Records that N independent AI reviewers checked a run against the kernel-locked
checklist and found NO blocking objection. The signal is ADVISORY ONLY:

- it is NOT a verification, a proof, checked evidence, or source-backed support;
- it can never render GREEN (it maps to the amber ``ai_reviewed`` trust level,
  which is deliberately absent from ``EARNED_LEVELS``);
- it never sets or changes ``idea_grade`` / proof / source / evidence / claim or
  any trust status -- ``AIReviewAssessment`` carries no such field, and this
  builder mutates no other artifact.

The kernel's contribution is locking WHAT must be reviewed (so the signal is
earned from STRUCTURED review + the ABSENCE of blockers), but absence of an
objection is never proof. ``no_blocking_objection`` is the strongest verdict and
requires >=2 independent reviewers AND zero unresolved blocking objections AND no
admissible checked refutation AND idea_grade.status != refuted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import AIReviewAssessment, AIReviewVerdict
from espresso_proof.kernel.research_bank import load_counterexamples_artifact
from espresso_proof.kernel.subgoal_graph import _scoped_objection_reviews

AI_REVIEW_FILENAME = "ai_review.yaml"

# Fixed disclaimer pinned on every assessment. Never softened.
DISCLAIMER = (
    "AI review signal: independent AI reviewers checked this run against the "
    "kernel-locked checklist and recorded no blocking objection. Advisory only — "
    "this is NOT a verification, a proof, or checked evidence."
)

# Severities that count as a blocking objection (must be resolved to pass).
_BLOCKING_SEVERITIES = {"P0", "P1"}
_BLOCKING_VERDICTS = {"blocked", "fail", "failed", "needs_revision", "needs_sources"}
_BLOCKING_STATUSES = {"blocked", "needs_human", "error"}
_BLOCKING_ACTIONS = {"stop", "revise"}


def _has_admissible_checked_refutation(run_dir: Path, artifacts: dict) -> bool:
    """True iff the counterexamples artifact carries an admissible CHECKED
    refutation of the CLAIM (refutes_claim is True with a checked 'failed'
    result). The CounterexampleTest model already forbids refutes_claim without a
    checked failed result and forbids dropped-hypothesis / step-only refutations,
    so reading ``refutes_claim`` here is the conservative read of those guards.
    Fail-safe: a registered missing / malformed artifact raises instead of
    counting as "no refutation"."""

    rel = artifacts.get("counterexamples")
    if not rel:
        return False
    path = run_dir / rel
    if not path.exists():
        raise ValueError(f"registered counterexamples artifact is missing: {rel}")
    try:
        report = load_counterexamples_artifact(path)
    except Exception:
        raise ValueError(f"registered counterexamples artifact is malformed: {rel}") from None
    return any(test.refutes_claim for test in report.tests)


def _review_blocker_count(review: dict) -> int:
    """Count reviewer fields that must block an advisory no-objection pass.

    The pass cannot depend only on structured P0/P1 scoped objections: legacy and
    canonical reviewer fields are also blocking when present. Count instead of a
    boolean so the persisted assessment stays auditable.
    """

    count = 0
    if str(review.get("verdict") or "").strip() in _BLOCKING_VERDICTS:
        count += 1
    if str(review.get("status") or "").strip() in _BLOCKING_STATUSES:
        count += 1
    if str(review.get("recommended_human_action") or "").strip() in _BLOCKING_ACTIONS:
        count += 1
    for field in ("objections", "missing_hypotheses", "source_requests"):
        count += sum(1 for item in (review.get(field) or []) if str(item).strip())
    return count


def assess_ai_review(run_dir: Path) -> dict:
    """Read a run's reviewer artifacts + kernel checks and return the assessment
    fields (no I/O, no mutation). ``generated_at`` is a placeholder finalised by
    ``build_ai_review``.

    The verdict is DERIVED only from reviewer artifacts (the gate-time
    ``llm_agent_run`` verifier_reviews + every continuation round, read via the
    SAME path subgoal_graph uses) plus kernel checks (counterexamples + idea
    grade). Prose / memory / a human can never set it.
    """

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts", {})
    optional = manifest.get("optional_artifacts", {})

    claim = read_yaml(run_dir / artifacts["locked_claim"])
    idea_grade = read_yaml(run_dir / artifacts["idea_grade"])

    llm_agent_run = None
    _llm_rel = optional.get("llm_agent_run")
    if _llm_rel and (run_dir / _llm_rel).exists():
        llm_agent_run = read_yaml(run_dir / _llm_rel)

    reviews = _scoped_objection_reviews(run_dir, llm_agent_run)

    # Distinct reviewers across the gate-time run + every round.
    reviewer_ids: set[str] = set()
    dimensions: list[str] = []
    for review in reviews:
        if not isinstance(review, dict):
            continue
        vid = review.get("verifier_id")
        if vid:
            reviewer_ids.add(str(vid))
        # The reviewing lens/dimension: the explicit reviewer_role when set,
        # else the verifier_id (which encodes the lens, e.g. strategy_reviewer_*).
        lens = review.get("reviewer_role") or review.get("verifier_id")
        if lens and str(lens) not in dimensions:
            dimensions.append(str(lens))
    reviewer_count = len(reviewer_ids)

    # Unresolved blocking objections: canonical reviewer blocking fields, P0/P1
    # scoped objections, plus any prover blocker recorded on the latest
    # prover-loop attempt. A scoped objection has no "resolved" flag in the
    # model, so any P0/P1 objection present counts as unresolved (fail closed —
    # present means it still blocks).
    unresolved = 0
    for review in reviews:
        if not isinstance(review, dict):
            continue
        unresolved += _review_blocker_count(review)
        for so in review.get("scoped_objections") or []:
            if isinstance(so, dict) and so.get("severity") in _BLOCKING_SEVERITIES:
                unresolved += 1

    prover_loop = None
    _pl_rel = optional.get("prover_loop")
    if _pl_rel and (run_dir / _pl_rel).exists():
        prover_loop = read_yaml(run_dir / _pl_rel)
    if isinstance(prover_loop, dict):
        attempts = prover_loop.get("attempts") or []
        if attempts:
            latest = max(attempts, key=lambda a: a.get("round_index", 0))
            unresolved += len(latest.get("blockers") or [])

    claim_refuted = idea_grade.get("status") == "refuted"
    has_refutation = _has_admissible_checked_refutation(run_dir, artifacts)

    if reviewer_count >= 2 and unresolved == 0 and not has_refutation and not claim_refuted:
        verdict = AIReviewVerdict.no_blocking_objection
    elif unresolved > 0 or has_refutation or claim_refuted:
        verdict = AIReviewVerdict.objections_found
    else:
        # Fewer than 2 independent reviewers and nothing blocking: not enough
        # independent review to record a pass.
        verdict = AIReviewVerdict.insufficient_review

    return {
        "schema_version": 1,
        "claim_id": str(claim.get("id")),
        "run_id": str(manifest.get("run_id")),
        "generated_at": "",  # finalised by build_ai_review
        "verdict": verdict.value,
        "reviewer_count": reviewer_count,
        "dimensions_reviewed": dimensions,
        "unresolved_objection_count": unresolved,
        "disclaimer": DISCLAIMER,
        "notes": [],
    }


def build_ai_review(run_dir: Path) -> dict:
    """Assemble, validate, and write ``ai_review.yaml``; register it as an
    optional artifact and return the assessment dict.

    Fails closed: the assessment is validated through ``AIReviewAssessment``
    BEFORE anything is written. Idempotent like ``build_subgoal_graph``: if an
    equivalent assessment already exists (everything but ``generated_at``), the
    previous timestamp is preserved so a re-build leaves the file byte-identical.

    MUST NOT mutate ``idea_grade`` or any other artifact: it only writes its own
    ``ai_review.yaml`` and the manifest's ``optional_artifacts`` registration.
    """

    run_dir = Path(run_dir)
    assessment = assess_ai_review(run_dir)

    path = run_dir / AI_REVIEW_FILENAME
    generated_at = datetime.now(UTC).isoformat()
    if path.exists():
        try:
            existing = read_yaml(path)
        except Exception:  # noqa: BLE001 - a corrupt prior file is simply replaced
            existing = None
        if isinstance(existing, dict):
            previous_stamp = existing.get("generated_at")
            if {**existing, "generated_at": ""} == {**assessment, "generated_at": ""} and previous_stamp:
                generated_at = str(previous_stamp)
    assessment["generated_at"] = generated_at

    # Fail closed on a malformed assessment before touching the run. The model
    # has no status field, so a smuggled status/source/checked key is rejected.
    AIReviewAssessment.model_validate(assessment)

    write_yaml(path, assessment)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest.setdefault("optional_artifacts", {})["ai_review"] = AI_REVIEW_FILENAME
    write_yaml(manifest_path, manifest)
    return assessment


__all__ = [
    "AI_REVIEW_FILENAME",
    "DISCLAIMER",
    "assess_ai_review",
    "build_ai_review",
]
