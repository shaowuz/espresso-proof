"""Goal 42D: bounded auto-fix replay machinery + advisory delta writers.

``run_replay`` re-runs the triggering dogfood case (plus sibling and blinded
holdout cases) through the real runtime and evaluates a NAMED, deterministic
problem probe to decide whether the selected gap actually improved. A replay
without improvement, or without sibling/holdout no-regression evidence, can
never be marked successful (enforced by the ReplayResult model).

Three checkpoints per fix: ``baseline`` (before the fix), ``pre_merge`` (on
the PR branch — failure blocks merge), ``post_merge`` (on clean origin/main).

The delta writers (`write_learning_delta`, `write_next_cycle_policy`,
`write_capability_delta`) are thin validated writers for the advisory memory
files; nothing here is evidence and nothing can upgrade any mathematical
status.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.loop_models import (
    CapabilityDelta,
    CaseRegressionCheck,
    DogfoodCase,
    LearningDelta,
    NextCyclePolicy,
    ReplayEvidence,
    ReplayResult,
    TriageDecision,
)
from espresso_proof.kernel.proof_language import contains_proof_language

# ---------------------------------------------------------------------------
# Named problem probes. Each probe answers: "is the triggering problem fixed
# on a FRESH run of this case?" — deterministically, from run artifacts only.
# ---------------------------------------------------------------------------


def _probe_false_theorem_counterexample_present(run_dir: Path) -> tuple[bool, str]:
    counterexamples = read_yaml(run_dir / "counterexamples.yaml")
    tests = counterexamples.get("tests", [])
    checked = [t for t in tests if t.get("result") == "failed"]
    if not tests:
        return False, "counterexamples.yaml still has zero tests"
    if not checked:
        return False, f"{len(tests)} candidate test(s) but none machine-checked (result=failed)"
    return True, f"{len(checked)} checked counterexample test(s) present"


def _probe_scoped_computation_present(run_dir: Path) -> tuple[bool, str]:
    path = run_dir / "proof_verification.yaml"
    if not path.exists():
        return False, "no proof_verification.yaml was produced"
    entries = read_yaml(path).get("entries", [])
    earned = [
        e for e in entries
        if e.get("trusted") and e.get("confirmed")
        and e.get("earned_status") in {"instance_checked", "computation_checked"}
    ]
    if not earned:
        return False, "no trusted confirmed scoped verification entry"
    return True, f"{len(earned)} scoped checked entr(ies) present"


def _probe_reference_supported_present(run_dir: Path) -> tuple[bool, str]:
    grounding = read_yaml(run_dir / "source_grounding.yaml")
    backed = [s for s in grounding.get("steps", []) if s.get("status") == "source_backed"]
    if not backed:
        return False, "no proof step earned reference support"
    return True, f"{len(backed)} reference-supported step(s)"


def _probe_memo_proof_language_clean(run_dir: Path) -> tuple[bool, str]:
    from espresso_proof.ui.memo import build_concise_memo

    memo = build_concise_memo(run_dir)
    phrase = contains_proof_language(memo)
    if phrase:
        return False, f"memo still contains proof-complete language: {phrase!r}"
    return True, "memo is free of proof-complete language"


def _probe_examples_present(run_dir: Path) -> tuple[bool, str]:
    examples = read_yaml(run_dir / "examples.yaml")
    items = examples.get("examples", [])
    if not items:
        return False, "examples.yaml still has zero examples"
    return True, f"{len(items)} example(s) present"


def _probe_computation_request_blockers_resolved(run_dir: Path) -> tuple[bool, str]:
    """A run carrying trusted confirmed machine checks must not still list a
    reviewer blocker that asks for an exact computation: the loop is expected
    to answer the request with the evidence it already has."""

    pv_path = run_dir / "proof_verification.yaml"
    if not pv_path.exists():
        return False, "no proof_verification.yaml — nothing machine-answered"
    claim_path = run_dir / "locked_claim.yaml"
    if not claim_path.exists():
        return False, "no locked_claim.yaml — cannot bind evidence to the claim"
    claim_id = read_yaml(claim_path).get("id")
    confirmed = [
        e for e in read_yaml(pv_path).get("entries", [])
        if e.get("trusted") and e.get("confirmed")
        and e.get("earned_status") in {"instance_checked", "computation_checked"}
        and e.get("claim_id") == claim_id
    ]
    if not confirmed:
        return False, "no trusted confirmed machine-check entry to answer with"
    loop_path = run_dir / "prover_loop.yaml"
    if not loop_path.exists():
        return False, "no prover_loop.yaml was produced"
    stale = [
        text
        for attempt in read_yaml(loop_path).get("attempts", [])
        for text in attempt.get("blockers", [])
        if "exact computation" in str(text).lower()
    ]
    if stale:
        return False, (
            f"{len(stale)} exact-computation blocker(s) still open despite "
            f"{len(confirmed)} confirmed machine check(s)"
        )
    return True, (
        f"no exact-computation blocker remains open; "
        f"{len(confirmed)} confirmed machine check(s) present"
    )


def _probe_standing_risks_classified_human_gated(run_dir: Path) -> tuple[bool, str]:
    """Blockers that originated as reviewer OBJECTIONS (standing risks) or
    MISSING HYPOTHESES (claim/hypothesis work) are human-gated by nature: no
    loop autofix can resolve them. The loop's classifier must say so, so
    triage routes them to goal proposals instead of bounded-fix selection."""

    # Phase 3: reuse the shared blocker-provenance module directly (was importing
    # these from the harness-only loop_harness; now both share one leaf module).
    from espresso_proof.loop.blocker_provenance import (
        blocker_provenance as _blocker_provenance,
        classify_blocker,
        provenance_is_human_gated,
    )

    loop_path = run_dir / "prover_loop.yaml"
    if not loop_path.exists():
        return False, "no prover_loop.yaml was produced"
    provenance = _blocker_provenance(run_dir)
    misclassified: list[str] = []
    human_gated_seen = 0
    seen: set[str] = set()
    for attempt in read_yaml(loop_path).get("attempts", []):
        for text in attempt.get("blockers", []):
            text = str(text).strip()
            if text in seen:
                continue
            seen.add(text)
            kinds = provenance.get(text, set())
            if not provenance_is_human_gated(kinds):
                continue  # requests keep their request-kind classification
            human_gated_seen += 1
            label = classify_blocker(text, provenance=kinds)
            if label not in {"human_judgment_needed", "reviewer_objection"}:
                misclassified.append(text)
    if misclassified:
        return False, (
            f"{len(misclassified)} standing-risk/hypothesis blocker(s) still "
            f"classified as bounded-fix targets, e.g. {misclassified[0][:80]!r}"
        )
    if not human_gated_seen:
        # fail closed (mirrors the sibling probes): a run with nothing to
        # classify proves nothing about the fix.
        return False, "no objection/hypothesis blocker present to classify"
    return True, "all objection/hypothesis blockers classified human-gated"


def recorded_misapplication_refusal(run_dir: Path) -> tuple[bool, str]:
    """For a source-discipline case the earned outcome is a RECORDED refusal:
    a hypothesis mismatch (e.g. very-general vs all) with every source held
    at/below candidate. An unrecorded refusal (no mismatch anywhere) means
    the source machinery never engaged the misapplication.

    Public: the source-applicability reviewer keys its earned-refusal
    carve-out on this exact predicate so reviewer and probe cannot drift —
    unclear/not_checked applicability conditions alone never count as a
    recorded refusal."""

    grounding_path = run_dir / "source_grounding.yaml"
    if not grounding_path.exists():
        return False, "no source_grounding.yaml was produced"
    grounding = read_yaml(grounding_path)
    backed = [
        r for r in grounding.get("source_refs", [])
        if r.get("status") == "source_backed"
    ]
    if backed:
        return False, (
            f"{len(backed)} source_backed reference(s) on a discipline case — "
            "the misapplied source must never earn support"
        )
    mismatches = [
        m
        for item in (grounding.get("steps", []) + grounding.get("source_refs", []))
        for m in ((item.get("hypothesis_match") or {}).get("mismatches", []) or [])
        if str(m).strip()
    ]
    if not mismatches:
        return False, "no hypothesis-mismatch refusal recorded anywhere in the grounding"
    return True, (
        f"misapplication refusal recorded ({len(mismatches)} mismatch note(s)); "
        "sources held at/below candidate"
    )


def _probe_true_theorem_source_engaged(run_dir: Path) -> tuple[bool, str]:
    """For a true classical theorem the smoke-mode earned outcome is an
    ENGAGED grounding: at least one step and one cited reference with an
    exact locator, attribution, and a recorded all-hypotheses match — held
    honestly at candidate. Smoke mode can never earn reference support, so
    any source_backed status fails the probe (fail closed)."""

    grounding_path = run_dir / "source_grounding.yaml"
    if not grounding_path.exists():
        return False, "no source_grounding.yaml was produced"
    grounding = read_yaml(grounding_path)
    steps = grounding.get("steps", [])
    refs = grounding.get("source_refs", [])
    backed = [
        item for item in steps + refs if item.get("status") == "source_backed"
    ]
    if backed:
        return False, (
            f"{len(backed)} source_backed item(s) in a smoke run — reference "
            "support can never be earned without real source verification"
        )
    if not steps or not refs:
        return False, "source grounding never engaged (no steps or no references)"
    engaged = [
        ref for ref in refs
        if str(ref.get("title", "")).strip()
        and any(str(a).strip() for a in ref.get("authors", []))
        and ref.get("status") == "candidate"
        and (ref.get("locator") or {}).get("kind") not in (None, "", "unknown")
        and str((ref.get("locator") or {}).get("value", "")).strip()
        and (ref.get("hypothesis_match") or {}).get("matches") is True
        and not (ref.get("hypothesis_match") or {}).get("mismatches")
    ]
    if not engaged:
        return False, (
            "no candidate reference carries attribution, an exact locator, "
            "and a recorded all-hypotheses match"
        )
    return True, (
        f"classical source engaged ({len(engaged)} reference(s) with exact "
        "locator and matched hypotheses); held honestly at candidate"
    )


def _probe_engaged_candidates_routed_to_goal(run_dir: Path) -> tuple[bool, str]:
    """Cycle 7: an ENGAGED smoke grounding stuck at candidate (exact locator,
    matched hypotheses, no unearned support) is a standing verification gap —
    earning reference support needs the real source-verification pipeline and
    fabricated support is forbidden. The probe verifies the reviewer's shared
    routing predicate marks that shape oversized-for-loop (so triage writes a
    goal proposal instead of recycling an unfixable selected gap), keeping
    probe and reviewer from drifting. Fails closed when there is nothing to
    classify or the predicate is absent."""

    engaged, note = _probe_true_theorem_source_engaged(run_dir)
    if not engaged:
        return False, f"nothing to classify: {note}"
    try:
        from espresso_proof.loop.loop_reviewers import source_support_fix
    except ImportError:
        return False, (
            "no shared standing-route predicate: engaged candidate findings "
            "are still recycled as bounded-fix targets"
        )
    # the markers are triage's own oversize predicate (lowercased there) —
    # imported, not duplicated, so probe and triage cannot drift.
    from espresso_proof.loop.loop_triage import _BROAD_FIX_MARKERS

    standing = source_support_fix(smoke_only=True, engaged=True).lower()
    bounded = source_support_fix(smoke_only=False, engaged=True).lower()
    if not any(marker in standing for marker in _BROAD_FIX_MARKERS):
        return False, (
            "the smoke-engaged standing gap is not marked oversized for triage"
        )
    if any(marker in bounded for marker in _BROAD_FIX_MARKERS):
        return False, (
            "non-smoke engaged findings must STAY bounded-fix targets "
            "(routing must not over-generalize)"
        )
    return True, (
        "engaged candidate grounding routes to a dedicated goal "
        "(oversized for a loop autofix; P1 evidence kept)"
    )


def _probe_missing_hypothesis_counterexample_recorded(run_dir: Path) -> tuple[bool, str]:
    """Cycle 8: a missing-hypothesis case earns its outcome when the run
    records a CHECKED counterexample that exposes the missing hypothesis and
    proposes a repaired claim — WITHOUT claiming a refutation (undetermined
    hypotheses never earn one, and exposing a missing hypothesis never
    refutes the repaired claim). Fail closed on empty banks, unchecked
    results, missing repair suggestions, or any refutation claim."""

    bank_path = run_dir / "counterexamples.yaml"
    if not bank_path.exists():
        return False, "no counterexamples.yaml was produced"
    tests = read_yaml(bank_path).get("tests", [])
    if not tests:
        return False, "empty counterexample bank on a missing-hypothesis case"
    exposing = [
        t for t in tests
        if t.get("exposes_missing_hypothesis") is True
        and str(t.get("result")) == "failed"
        and str(t.get("missing_hypothesis") or "").strip()
        and str(t.get("repaired_claim_suggestion") or "").strip()
    ]
    if not exposing:
        return False, (
            "no checked counterexample exposes the missing hypothesis with a "
            "repaired-claim suggestion"
        )
    # the family's must_not_claim forbids ANY refutation claim on this case,
    # not just on the exposing records — scan the whole bank.
    overclaiming = [t for t in tests if t.get("refutes_claim")]
    if overclaiming:
        return False, (
            f"{len(overclaiming)} record(s) claim a refutation on a "
            "missing-hypothesis case — exposing a missing hypothesis must "
            "stay below refuted"
        )
    return True, (
        f"missing hypothesis exposed by {len(exposing)} checked record(s) "
        "with a repaired-claim suggestion; no refutation claimed"
    )


PROBLEM_PROBES = {
    "false_theorem_counterexample_present": _probe_false_theorem_counterexample_present,
    "scoped_computation_present": _probe_scoped_computation_present,
    "reference_supported_present": _probe_reference_supported_present,
    "memo_proof_language_clean": _probe_memo_proof_language_clean,
    "examples_present": _probe_examples_present,
    "computation_request_blockers_resolved": _probe_computation_request_blockers_resolved,
    "standing_risks_classified_human_gated": _probe_standing_risks_classified_human_gated,
    "source_mismatch_refusal_recorded": recorded_misapplication_refusal,
    "true_theorem_source_engaged": _probe_true_theorem_source_engaged,
    "engaged_candidates_routed_to_goal": _probe_engaged_candidates_routed_to_goal,
    "missing_hypothesis_counterexample_recorded": _probe_missing_hypothesis_counterexample_recorded,
}

# Public alias (cycle-5 precedent): the source-applicability reviewer keys
# its standing-route decision on this exact engagement predicate over the
# run's own grounding, so reviewer and probe cannot drift.
true_theorem_source_engaged = _probe_true_theorem_source_engaged


def _fresh_case_run(case: DogfoodCase, work_root: Path, mode: str) -> tuple[Path | None, str]:
    """Run one case through the real runtime for replay. Returns (run_dir, error)."""

    from espresso_proof.loop.loop_harness import _run_case  # same execution path as the recorder

    record = _run_case(
        case,
        runs_root=work_root,
        mode=mode,
        provider="stub" if mode == "smoke" else "external",
        front_half="mock" if mode == "smoke" else "external",
    )
    if record["failure"]:
        return None, str(record["failure"])
    if record["validation_result"] != "valid":
        return record["run_dir"], f"validate_run failed: {record['validation_error']}"
    return record["run_dir"], ""


def _no_regression_check(run_dir: Path | None, error: str) -> tuple[bool, str]:
    """A sibling/holdout case regresses if its run fails, goes kernel-invalid,
    leaks proof language into the memo, or upgrades refuted without a checked
    counterexample. Conservative: any uncertainty counts as regression."""

    if run_dir is None or error:
        return False, error or "run did not start"
    ok, detail = _probe_memo_proof_language_clean(run_dir)
    if not ok:
        return False, detail
    grade = read_yaml(run_dir / "idea_grade.yaml")
    if grade.get("status") == "refuted":
        counterexamples = read_yaml(run_dir / "counterexamples.yaml")
        admissible = [
            t for t in counterexamples.get("tests", [])
            if t.get("refutes_claim")
            and t.get("satisfies_all_claim_hypotheses") is True
            and t.get("result") == "failed"
        ]
        if not admissible:
            return False, "refuted grade without an admissible checked counterexample"
    return True, "valid run, honest memo, no unauthorized refutation"


def run_replay(
    cycle_dir: Path,
    *,
    checkpoint: str,
    probe: str,
    triggering_case_id: str,
    sibling_case_ids: list[str] | None = None,
    holdout_cases: list[DogfoodCase] | None = None,
    observed_problem: str,
    before_evidence_path: str,
    mode: str = "smoke",
) -> ReplayResult:
    """Replay the triggering case (+ siblings/holdouts) and write
    replay_result.yaml for the given checkpoint. Success requires the probe to
    report FIXED and every sibling/holdout to show no regression."""

    if probe not in PROBLEM_PROBES:
        raise ValueError(f"unknown problem probe {probe!r}; known: {sorted(PROBLEM_PROBES)}")
    if checkpoint not in {"baseline", "pre_merge", "post_merge"}:
        raise ValueError("checkpoint must be baseline, pre_merge, or post_merge")
    cycle_dir = Path(cycle_dir)
    triage = TriageDecision.model_validate(read_yaml(cycle_dir / "triage.yaml"))
    from espresso_proof.loop.loop_harness import load_cases

    cases = {c.case_id: c for c in load_cases(None, limit=None)}
    recorded = read_yaml(cycle_dir / "cases.yaml")
    for raw in recorded.get("cases", []):
        case = DogfoodCase.model_validate(raw)
        cases[case.case_id] = case
    if triggering_case_id not in cases:
        raise ValueError(f"unknown triggering case {triggering_case_id!r}")

    work_root = cycle_dir / "replays" / checkpoint
    work_root.mkdir(parents=True, exist_ok=True)

    run_dir, error = _fresh_case_run(cases[triggering_case_id], work_root / "trigger", mode)
    if run_dir is None or error:
        fixed, detail = False, error or "run did not start"
        validate_passed = False
    else:
        validate_passed = True
        fixed, detail = PROBLEM_PROBES[probe](run_dir)

    siblings: list[CaseRegressionCheck] = []
    for case_id in sibling_case_ids or []:
        if case_id not in cases:
            raise ValueError(f"unknown sibling case {case_id!r}")
        sibling_dir, sibling_error = _fresh_case_run(
            cases[case_id], work_root / f"sibling_{case_id}", mode
        )
        ok, note = _no_regression_check(sibling_dir, sibling_error)
        siblings.append(
            CaseRegressionCheck(
                case_id=case_id, no_regression=ok,
                evidence_path=str(sibling_dir) if sibling_dir else note[:200],
            )
        )

    holdouts: list[CaseRegressionCheck] = []
    for case in holdout_cases or []:
        holdout_dir, holdout_error = _fresh_case_run(
            case, work_root / f"holdout_{case.case_id}", mode
        )
        ok, note = _no_regression_check(holdout_dir, holdout_error)
        holdouts.append(
            CaseRegressionCheck(
                case_id=case.case_id, no_regression=ok,
                evidence_path=str(holdout_dir) if holdout_dir else note[:200],
            )
        )

    regressions = [c for c in siblings + holdouts if not c.no_regression]
    successful = bool(fixed and validate_passed and (siblings or holdouts) and not regressions)
    result = ReplayResult(
        cycle_id=cycle_dir.name,
        selected_gap_id=triage.selected_gap_id,
        checkpoint=checkpoint,  # type: ignore[arg-type]
        before=ReplayEvidence(
            evidence_path=before_evidence_path, observed_problem=observed_problem
        ),
        after=ReplayEvidence(
            evidence_path=str(run_dir) if run_dir else "run did not start",
            observed_problem=detail,
            fixed=fixed,
            no_new_p0_or_p1=not regressions,
            no_status_upgrade_violation=all(
                c.no_regression for c in siblings + holdouts
            ) if (siblings or holdouts) else None,
            validate_run_passed=validate_passed,
            first_feedback_regressed=False,
        ),
        sibling_cases=siblings,
        holdout_cases=holdouts,
        successful=successful,
    )
    suffix = "" if checkpoint == "pre_merge" else f"_{checkpoint}"
    write_yaml(cycle_dir / f"replay_result{suffix}.yaml", result.model_dump(mode="json"))
    if checkpoint == "pre_merge" and not successful:
        raise RuntimeError(
            "pre-merge replay did not demonstrate the fix without regressions; "
            "merge is blocked (Goal 42D)"
        )
    return result


# ---------------------------------------------------------------------------
# Advisory delta writers (suggestions only — never evidence).
# ---------------------------------------------------------------------------


def write_learning_delta(cycle_dir: Path, delta: dict) -> LearningDelta:
    model = LearningDelta.model_validate({"cycle_id": Path(cycle_dir).name, **delta})
    write_yaml(Path(cycle_dir) / "learning_delta.yaml", model.model_dump(mode="json"))
    return model


def write_next_cycle_policy(cycle_dir: Path, policy: dict) -> NextCyclePolicy:
    model = NextCyclePolicy.model_validate({"cycle_id": Path(cycle_dir).name, **policy})
    write_yaml(Path(cycle_dir) / "next_cycle_policy.yaml", model.model_dump(mode="json"))
    return model


def write_capability_delta(cycle_dir: Path, delta: dict) -> CapabilityDelta:
    model = CapabilityDelta.model_validate({"cycle_id": Path(cycle_dir).name, **delta})
    write_yaml(Path(cycle_dir) / "capability_delta.yaml", model.model_dump(mode="json"))
    return model


__all__ = [
    "run_replay",
    "PROBLEM_PROBES",
    "write_learning_delta",
    "write_next_cycle_policy",
    "write_capability_delta",
]
