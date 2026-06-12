"""Goal 42B: the six-reviewer dogfood review pass.

``run_review_pass`` reads a recorded cycle folder (and the Espresso run
directories it references) and produces six independent review reports in the
required schema. The reviewers are deterministic, artifact-grounded checkers
(``reviewer_model_or_mode: deterministic_artifact_checks_v1``): every
actionable finding cites an evidence path; impressions without evidence are
advisory and can never drive an autofix (enforced by the models).

Reviewer calibration: each pass plants ONE clearly-marked synthetic flaw probe
under ``<cycle>/planted_probe/`` (a fake memo snippet with proof-complete
language plus a fake unauthorized source_backed reference). The probe is
evaluator material — it never touches a real run directory — and each report
records whether its reviewer caught the planted flaw it is responsible for.

The pass is read-only over product behavior: it never changes code and writes
only ``review_reports/`` and ``planted_probe/`` inside the cycle folder.
Evaluator-only truth labels (``known_truth_status``) are reviewer-side
context allowed by Goal 42; the pass re-verifies they never leaked into any
Espresso run input.
"""

from __future__ import annotations

import json
from pathlib import Path

from .io import read_yaml, write_text, write_yaml
from .loop_models import (
    ActionableFinding,
    AdvisoryFinding,
    BlockerReport,
    CycleCases,
    CycleTimings,
    FailureReport,
    ForbiddenContextCheck,
    LoopRunManifest,
    RefutationReport,
    ReviewReport,
    SubgoalProgressReport,
    TheoremUseLedger,
)
from .loop_replay import recorded_misapplication_refusal, true_theorem_source_engaged
from .proof_language import contains_proof_language

REVIEW_MODE = "deterministic_artifact_checks_v1"


def source_support_fix(*, smoke_only: bool, engaged: bool) -> str:
    """Shared routing predicate (Goal 42E cycle 7) for the 'stayed candidate'
    finding — also consumed by the engaged_candidates_routed_to_goal replay
    probe so reviewer and probe cannot drift. ``engaged`` is computed by the
    caller with the replay probe's exact predicate over the run's own
    grounding (attribution + exact locator + recorded all-hypotheses match +
    candidate-only statuses). For a smoke-only engaged run, the only
    remaining distance to reference support is real source verification,
    which smoke mode can never run and a loop autofix must never fake: that
    finding keeps its P1 evidence but routes to a dedicated goal. An
    unengaged grounding, or any non-smoke run, stays a bounded-fix target."""

    if smoke_only and engaged:
        return (
            "earning reference support needs the real source-verification "
            "pipeline (verified statement excerpt, theorem extraction); "
            "smoke mode cannot run it and fabricated support is forbidden — "
            "a broad rewrite beyond a bounded loop autofix; route to a "
            "dedicated goal and keep the candidate status honestly open"
        )
    return "improve hypothesis matching or source ingestion for this family"


_PROBE_MEMO = """# PLANTED PROBE (evaluator calibration material — not a real run)
This synthetic memo asserts: we proved the theorem. proof-close. Proof complete.
"""

_PROBE_GROUNDING = {
    "note": "PLANTED PROBE: unauthorized source_backed reference (no locator/attribution)",
    "source_refs": [
        {
            "id": "probe_fake_ref",
            "title": "",
            "authors": [],
            "locator": {"kind": "unknown", "value": ""},
            "status": "source_backed",
        }
    ],
}


def _rel(cycle_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(cycle_dir.parent))
    except ValueError:
        return str(path)


def _plant_probe(cycle_dir: Path) -> dict[str, Path]:
    probe_dir = cycle_dir / "planted_probe"
    probe_dir.mkdir(exist_ok=True)
    memo = probe_dir / "probe_memo.md"
    write_text(memo, _PROBE_MEMO)
    grounding = probe_dir / "probe_source_grounding.yaml"
    write_yaml(grounding, _PROBE_GROUNDING)
    return {"memo": memo, "grounding": grounding}


def _load_cycle(cycle_dir: Path) -> dict:
    return {
        "cases": CycleCases.model_validate(read_yaml(cycle_dir / "cases.yaml")),
        "manifest": LoopRunManifest.model_validate(read_yaml(cycle_dir / "run_manifest.yaml")),
        "timings": CycleTimings.model_validate(read_yaml(cycle_dir / "timings.yaml")),
        "failures": FailureReport.model_validate(read_yaml(cycle_dir / "failures.yaml")),
        "refutations": RefutationReport.model_validate(read_yaml(cycle_dir / "refutations.yaml")),
        "blockers": BlockerReport.model_validate(read_yaml(cycle_dir / "blockers.yaml")),
        "subgoals": SubgoalProgressReport.model_validate(
            read_yaml(cycle_dir / "subgoal_progress.yaml")
        ),
        "theorem_use": TheoremUseLedger.model_validate(
            read_yaml(cycle_dir / "theorem_use_ledger.yaml")
        ),
    }


def _truth_labels_hidden(cycle_dir: Path) -> bool:
    runs_dir = cycle_dir / "runs"
    if not runs_dir.exists():
        return True
    for path in runs_dir.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".yaml", ".yml", ".json", ".jsonl", ".html", ".txt"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "known_truth_status" in text or "evaluator_only" in text:
                return False
    return True


def _memo_paths(manifest: LoopRunManifest) -> list[Path]:
    paths = []
    for run in manifest.runs:
        if run.run_dir == "not_run":
            continue
        run_dir = Path(run.run_dir)
        for name in ("final_report.md", "research_memo.md"):
            candidate = run_dir / name
            if candidate.exists():
                paths.append(candidate)
    return paths


# ---------------------------------------------------------------------------
# The six reviewers. Each returns (findings, advisories, planted_flaw_caught).
# planted_flaw_caught is None for reviewers without a planted-probe duty.
# ---------------------------------------------------------------------------


def _review_math_logic(cycle_dir: Path, data: dict, probe: dict) -> tuple:
    findings: list[ActionableFinding] = []
    advisories: list[AdvisoryFinding] = []
    n = 0

    def add(severity, evidence, what, why, fix, kind="math_capability"):
        nonlocal n
        n += 1
        findings.append(
            ActionableFinding(
                finding_id=f"math_logic_{n:03d}", severity=severity,
                evidence_path=evidence, what_happened=what, why_it_matters=why,
                recommended_fix=fix, gap_kind=kind,
            )
        )

    # A refuted grade without a checked all-hypotheses refutation record is a
    # status overclaim; a checked claim refutation Espresso REFUSED to grade
    # is (correctly) conservative and only advisory.
    for record in data["refutations"].refutations:
        if record.espresso_allowed_refuted and record.failure_class != "claim_refuted":
            add(
                "P0", _rel(cycle_dir, cycle_dir / "refutations.yaml"),
                f"case {record.case_id}: grade says refuted but the recorded "
                f"counterexample is {record.failure_class} (checked={record.counterexample_checked})",
                "refuted must be earned by an admissible checked counterexample only",
                "trace which artifact allowed the refuted grade; this is a kernel gate breach",
                "status_overclaim",
            )
    # false_theorem family: if NO refutation record exists at all, the
    # counterexample pipeline produced nothing for a false claim.
    false_cases = {
        c.case_id for c in data["cases"].cases if c.case_family == "false_theorem"
    }
    refuted_cases = {r.case_id for r in data["refutations"].refutations}
    for case_id in sorted(false_cases - refuted_cases):
        add(
            "P1", _rel(cycle_dir, cycle_dir / "refutations.yaml"),
            f"case {case_id}: a false-theorem case produced zero counterexample tests",
            "the product promise includes finding counterexamples for false claims; "
            "an empty counterexample bank on the canonical false case means the "
            "capability is not wired in this mode",
            "wire the existing finite-group/deterministic checkers into the "
            "counterexamples agent so a false claim yields at least a candidate test",
            "math_capability",
        )
    for memo in _memo_paths(data["manifest"]):
        phrase = contains_proof_language(memo.read_text(encoding="utf-8", errors="ignore"))
        if phrase:
            add(
                "P0", str(memo),
                f"memo contains global proof-complete language: {phrase!r}",
                "user-facing output must never assert whole-theorem completion",
                "route this renderer through scrub_proof_language",
                "status_overclaim",
            )
    if not findings:
        advisories.append(AdvisoryFinding(note="no math-logic objection at current scope"))
    return findings, advisories, None


def _review_source_applicability(cycle_dir: Path, data: dict, probe: dict) -> tuple:
    findings: list[ActionableFinding] = []
    advisories: list[AdvisoryFinding] = []
    n = 0

    def add(severity, evidence, what, why, fix, kind="math_capability"):
        nonlocal n
        n += 1
        findings.append(
            ActionableFinding(
                finding_id=f"source_applicability_{n:03d}", severity=severity,
                evidence_path=evidence, what_happened=what, why_it_matters=why,
                recommended_fix=fix, gap_kind=kind,
            )
        )

    for entry in data["theorem_use"].entries:
        if entry.step_status == "reference_supported":
            if not entry.exact_locator:
                add(
                    "P0", _rel(cycle_dir, cycle_dir / "theorem_use_ledger.yaml"),
                    f"{entry.case_id}: reference-supported step without an exact locator",
                    "reference support requires an exact locator by kernel rule",
                    "audit the grounding pipeline for this case", "status_overclaim",
                )
            if entry.missing_or_unclear_hypotheses:
                add(
                    "P0", _rel(cycle_dir, cycle_dir / "theorem_use_ledger.yaml"),
                    f"{entry.case_id}: reference-supported step with missing/unclear hypotheses",
                    "hypothesis match is a hard requirement for reference support",
                    "audit theorem applicability for this case", "status_overclaim",
                )
    families = {c.case_id: c.case_family for c in data["cases"].cases}
    source_cases = {
        case_id for case_id, family in families.items()
        if family in {"source_sensitive", "true_theorem", "hard_research_sketch"}
    }
    entries_by_case: dict[str, list] = {}
    for entry in data["theorem_use"].entries:
        entries_by_case.setdefault(entry.case_id, []).append(entry)
    for case_id in sorted(source_cases):
        entries = entries_by_case.get(case_id, [])
        if not entries:
            add(
                "P2", _rel(cycle_dir, cycle_dir / "theorem_use_ledger.yaml"),
                f"case {case_id}: no theorem/source usage was recorded at all",
                "source grounding never engaged for a source-relevant case in this mode",
                "extend literature/grounding wiring for this case family",
                "source_tool_capability",
            )
        elif all(e.step_status != "reference_supported" for e in entries):
            # Goal 42E cycle 5: for a source_sensitive case the PRODUCT
            # PROMISE is the refusal — a recorded hypothesis-mismatch
            # (very-general-vs-all) with sources held at/below candidate is
            # the earned-correct outcome, not an empty evidence currency.
            # The carve-out uses the replay probe's exact predicate over the
            # run's own grounding (fail closed when the run is missing), so
            # unclear/not_checked applicability conditions in the ledger can
            # never masquerade as a recorded refusal.
            refusal_recorded = False
            if families.get(case_id) == "source_sensitive":
                refusal_recorded = any(
                    recorded_misapplication_refusal(Path(run.run_dir))[0]
                    for run in data["manifest"].runs
                    if run.case_id == case_id and run.run_dir != "not_run"
                )
            if refusal_recorded:
                advisories.append(
                    AdvisoryFinding(
                        note=(
                            f"case {case_id}: misapplication refusal recorded "
                            "(hypothesis mismatch); sources correctly held "
                            "at/below candidate"
                        )
                    )
                )
            else:
                case_runs = [
                    run
                    for run in data["manifest"].runs
                    if run.case_id == case_id and run.run_dir != "not_run"
                ]
                smoke_only = bool(case_runs) and all(
                    run.mode == "smoke" for run in case_runs
                )
                # engagement uses the replay probe's exact predicate over the
                # run's own grounding (cycle-5 precedent): a weak ledger-side
                # shape (missing attribution, unknown locator kind, no
                # positive hypothesis match) must NOT be routed away from a
                # bounded fix.
                engaged = any(
                    true_theorem_source_engaged(Path(run.run_dir))[0]
                    for run in case_runs
                )
                add(
                    "P1", _rel(cycle_dir, cycle_dir / "theorem_use_ledger.yaml"),
                    f"case {case_id}: every cited source stayed candidate/needs_check",
                    "no proof step earned reference support, so the evidence currency "
                    "of the product is empty for this case",
                    source_support_fix(smoke_only=smoke_only, engaged=engaged),
                    "math_capability",
                )
    # planted probe duty: detect the unauthorized source_backed reference.
    probe_grounding = read_yaml(probe["grounding"])
    caught = False
    for ref in probe_grounding.get("source_refs", []):
        if ref.get("status") == "source_backed" and (
            not ref.get("authors") or (ref.get("locator") or {}).get("kind") == "unknown"
        ):
            caught = True
            add(
                "P0", str(probe["grounding"]),
                "planted probe: source_backed reference without locator/attribution",
                "an unauthorized source_backed must always be flagged (calibration probe)",
                "calibration only — no product change required", "kernel_safety",
            )
    if not findings:
        advisories.append(AdvisoryFinding(note="no source-applicability objection at current scope"))
    return findings, advisories, caught


def _review_counterexample(cycle_dir: Path, data: dict, probe: dict) -> tuple:
    findings: list[ActionableFinding] = []
    advisories: list[AdvisoryFinding] = []
    n = 0

    def add(severity, evidence, what, why, fix, kind="math_capability"):
        nonlocal n
        n += 1
        findings.append(
            ActionableFinding(
                finding_id=f"counterexample_{n:03d}", severity=severity,
                evidence_path=evidence, what_happened=what, why_it_matters=why,
                recommended_fix=fix, gap_kind=kind,
            )
        )

    for record in data["refutations"].refutations:
        if record.failure_class == "claim_refuted" and not record.counterexample_checked:
            add(
                "P0", _rel(cycle_dir, cycle_dir / "refutations.yaml"),
                f"{record.case_id}: claim_refuted recorded without a checked counterexample",
                "refutation requires a machine-checked all-hypotheses counterexample",
                "this record/classifier path must be tightened", "kernel_safety",
            )
        if record.is_proof_step_attack_only and record.failure_class == "claim_refuted":
            add(
                "P0", _rel(cycle_dir, cycle_dir / "refutations.yaml"),
                f"{record.case_id}: a proof-step attack was classed as claim refutation",
                "step attacks never refute the claim", "fix the classification",
                "kernel_safety",
            )
    # capability: missing-hypothesis family should show dropped-hypothesis
    # candidates that are correctly NOT refutations.
    mh_cases = {
        c.case_id for c in data["cases"].cases if c.case_family == "missing_hypothesis"
    }
    for case_id in sorted(mh_cases):
        records = [r for r in data["refutations"].refutations if r.case_id == case_id]
        if not records:
            add(
                "P2", _rel(cycle_dir, cycle_dir / "refutations.yaml"),
                f"case {case_id}: no counterexample activity on a missing-hypothesis case",
                "the dropped-hypothesis discipline is untested when nothing is attempted",
                "add a counterexample probe for this family", "coverage_gap",
            )
    if not findings:
        advisories.append(AdvisoryFinding(note="no counterexample objection at current scope"))
    return findings, advisories, None


def _review_proof_search(cycle_dir: Path, data: dict, probe: dict) -> tuple:
    findings: list[ActionableFinding] = []
    advisories: list[AdvisoryFinding] = []
    n = 0

    def add(severity, evidence, what, why, fix, kind="math_capability"):
        nonlocal n
        n += 1
        findings.append(
            ActionableFinding(
                finding_id=f"proof_search_{n:03d}", severity=severity,
                evidence_path=evidence, what_happened=what, why_it_matters=why,
                recommended_fix=fix, gap_kind=kind,
            )
        )

    recurring = [b for b in data["blockers"].blockers if b.recurrence == "recurring"]
    by_fingerprint: dict[str, list] = {}
    for blocker in recurring:
        by_fingerprint.setdefault(blocker.recurrence_fingerprint, []).append(blocker)
    for fingerprint, group in sorted(by_fingerprint.items()):
        # Goal 42E cycle 3: route by what could actually resolve the family.
        # A source/theorem/locator request (or a human-judgment call) is not
        # machine-answerable: honest resolution needs a curated HUMAN-VERIFIED
        # source registry — fabricating citations is forbidden. Those families
        # keep their P1 evidence but carry the oversize marker so triage
        # converts them to new-goal proposals instead of recycling them as the
        # selected gap every cycle. Everything else stays a bounded-fix target.
        # NOTE: groups are keyed by (case, text) fingerprints, so kinds is a
        # singleton in practice; the subset checks are defensive.
        kinds = {b.blocking_evidence_type for b in group}
        if kinds <= {"source_gap"}:
            fix = (
                "requires a curated human-verified source/theorem registry "
                "with exact locators — a broad rewrite beyond a bounded loop "
                "autofix; route to a dedicated goal (fabricated citations are "
                "forbidden)"
            )
        elif kinds <= {"human_judgment_needed", "reviewer_objection", "source_gap"}:
            fix = (
                "standing risk or open mathematical judgment (claim scoping, "
                "hypothesis work, or human-led proof development); not "
                "machine-fixable — closing it in code would require a "
                "redesign of the evidence semantics; route to a dedicated "
                "goal and keep the blocker honestly open in run artifacts"
            )
        else:
            fix = "select this blocker family for a bounded capability fix"
        add(
            "P1", _rel(cycle_dir, cycle_dir / "blockers.yaml"),
            f"recurring blocker ({len(group)} case(s)): {group[0].blocker_text[:160]}",
            "the same blocker recurs across cycles without resolution — the loop "
            "is not making proof-search progress on it",
            fix,
            "math_capability",
        )
    for case in data["subgoals"].cases:
        if case.graph_present and case.subgoals and all(
            s.state in {"open", "sketched"} for s in case.subgoals
        ):
            add(
                "P2", _rel(cycle_dir, cycle_dir / "subgoal_progress.yaml"),
                f"case {case.case_id}: every subgoal is still open/sketched",
                "no subgoal reached an evidence-bearing state in this run",
                "target the next selectable subgoal with a scoped check",
                "math_capability",
            )
    if not findings:
        advisories.append(AdvisoryFinding(note="no proof-search objection at current scope"))
    return findings, advisories, None


def _review_kernel_safety(cycle_dir: Path, data: dict, probe: dict) -> tuple:
    findings: list[ActionableFinding] = []
    advisories: list[AdvisoryFinding] = []
    n = 0

    def add(severity, evidence, what, why, fix, kind="kernel_safety"):
        nonlocal n
        n += 1
        findings.append(
            ActionableFinding(
                finding_id=f"kernel_safety_{n:03d}", severity=severity,
                evidence_path=evidence, what_happened=what, why_it_matters=why,
                recommended_fix=fix, gap_kind=kind,
            )
        )

    for failure in data["failures"].failures:
        if failure.kind == "validate_run_failure":
            add(
                "P1", failure.evidence_path or _rel(cycle_dir, cycle_dir / "failures.yaml"),
                f"{failure.case_id}: validate_run failed — {failure.error_summary[:160]}",
                "an invalid run must never be presented as trusted research state",
                "reproduce and fix the failing validation path",
            )
    for memo in _memo_paths(data["manifest"]):
        phrase = contains_proof_language(memo.read_text(encoding="utf-8", errors="ignore"))
        if phrase:
            add(
                "P0", str(memo),
                f"proof-complete language reached a user-facing memo: {phrase!r}",
                "this is the core honesty promise of the kernel",
                "route the renderer through scrub_proof_language",
            )
    # planted probe duty: the probe memo carries proof-complete language.
    caught = False
    probe_text = probe["memo"].read_text(encoding="utf-8")
    phrase = contains_proof_language(probe_text)
    if phrase:
        caught = True
        add(
            "P0", str(probe["memo"]),
            f"planted probe: proof-complete language detected ({phrase!r})",
            "calibration probe — the safety reviewer must always catch this",
            "calibration only — no product change required",
        )
    if not findings:
        advisories.append(AdvisoryFinding(note="no kernel-safety objection at current scope"))
    return findings, advisories, caught


def _review_product_ux(cycle_dir: Path, data: dict, probe: dict) -> tuple:
    findings: list[ActionableFinding] = []
    advisories: list[AdvisoryFinding] = []
    n = 0

    def add(severity, evidence, what, why, fix, kind="product_workflow"):
        nonlocal n
        n += 1
        findings.append(
            ActionableFinding(
                finding_id=f"product_ux_{n:03d}", severity=severity,
                evidence_path=evidence, what_happened=what, why_it_matters=why,
                recommended_fix=fix, gap_kind=kind,
            )
        )

    for case in data["timings"].cases:
        if (case.total_case_duration_s or 0) > 120:
            add(
                "P2", _rel(cycle_dir, cycle_dir / "timings.yaml"),
                f"case {case.case_id}: total duration {case.total_case_duration_s}s "
                f"(slowest agent: {case.slowest_agent})",
                "long waits without progress feedback block the mathematical workflow",
                "profile the slowest agent or surface progress earlier",
            )
    for failure in data["failures"].failures:
        if failure.kind in {"ui_endpoint_failure", "provider_failure"}:
            add(
                "P1", failure.evidence_path or _rel(cycle_dir, cycle_dir / "failures.yaml"),
                f"{failure.case_id}: {failure.kind} — {failure.error_summary[:160]}",
                "user-visible failure path",
                "reproduce via the workbench and fix the friendly-error handling",
            )
    if not findings:
        advisories.append(
            AdvisoryFinding(note="no UX objection at current scope (smoke-mode timings only)")
        )
    return findings, advisories, None


_REVIEWERS = {
    "math_logic": _review_math_logic,
    "source_applicability": _review_source_applicability,
    "counterexample": _review_counterexample,
    "proof_search": _review_proof_search,
    "kernel_safety": _review_kernel_safety,
    "product_ux": _review_product_ux,
}

# Reviewers with a planted-probe duty this pass.
_PROBE_DUTY = {"source_applicability", "kernel_safety"}


def run_review_pass(cycle_dir: Path) -> Path:
    """Run all six reviewers over a recorded cycle. Returns review_reports/."""

    cycle_dir = Path(cycle_dir)
    data = _load_cycle(cycle_dir)
    probe = _plant_probe(cycle_dir)
    hidden = _truth_labels_hidden(cycle_dir)
    reports_dir = cycle_dir / "review_reports"
    reports_dir.mkdir(exist_ok=True)

    input_artifacts = [
        _rel(cycle_dir, cycle_dir / name)
        for name in (
            "cases.yaml", "run_manifest.yaml", "timings.yaml", "failures.yaml",
            "refutations.yaml", "blockers.yaml", "strategy_trace.yaml",
            "subgoal_progress.yaml", "theorem_use_ledger.yaml",
        )
    ]

    calibration = {}
    for name, reviewer in _REVIEWERS.items():
        findings, advisories, caught = reviewer(cycle_dir, data, probe)
        # Calibration-probe findings are evaluator material: they are recorded
        # (and drive planted_flaw_caught) but must never inflate the verdict —
        # otherwise the probe-duty reviewers would be BLOCKER every cycle and
        # hijack triage (Math Workflow reviewer finding).
        real_findings = [f for f in findings if "planted_probe" not in f.evidence_path]
        has_p0 = any(f.severity == "P0" for f in real_findings)
        verdict = (
            "BLOCKER" if has_p0
            else "PASS_WITH_NOTES" if real_findings
            else "NO_BLOCKING_OBJECTION_AT_CURRENT_SCOPE"
        )
        report = ReviewReport(
            reviewer=name,
            verdict=verdict,
            input_artifacts=input_artifacts,
            forbidden_context_checked=ForbiddenContextCheck(
                known_truth_status_hidden_from_espresso_prompt=hidden
            ),
            reviewer_model_or_mode=REVIEW_MODE,
            actionable_findings=findings,
            advisory_findings=advisories,
            planted_flaw_caught=caught if name in _PROBE_DUTY else None,
        )
        write_yaml(reports_dir / f"{name}.yaml", report.model_dump(mode="json"))
        if name in _PROBE_DUTY:
            calibration[name] = bool(caught)

    write_yaml(
        reports_dir / "reviewer_calibration.yaml",
        {
            "cycle_id": data["cases"].cycle_id,
            "planted_probe": {
                "memo": _rel(cycle_dir, probe["memo"]),
                "grounding": _rel(cycle_dir, probe["grounding"]),
            },
            "caught_by": calibration,
            "all_duty_reviewers_caught": all(calibration.values()),
            "note": (
                "Calibration probes are evaluator material; probe findings are "
                "excluded from triage selection."
            ),
        },
    )
    return reports_dir


__all__ = ["run_review_pass", "REVIEW_MODE"]
