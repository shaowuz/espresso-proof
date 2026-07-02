"""Autoprover Behavior Scorecard (Goal 40B).

One deterministic command that reports BOTH safety and capability metrics over
the mock fixtures, so a PR that makes Espresso more overconfident — or less
capable at the behaviors it already earned — fails a single visible gate.

Composition, not reinvention: the safety half reuses the Goal 40A baseline
(``run_benchmark`` + ``run_calibration``); the capability half adds small
deterministic probes built from the SAME earning pipelines the product uses
(ground_run, audit_theorem_applicability, the exact_modexp adapter, the
artifact-grounded reviewer checklist, and the stub prover loop).

Hard rules: every probe is deterministic and fixture-derived (the "dogfood
replay" run is regenerated from committed fixtures at scorecard time, so it is
sanitized by construction — no private run directories, no secrets); metrics
NEVER earn or upgrade any mathematical status; nothing here weakens a gate.
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.evaluation.benchmark import run_benchmark
from espresso_proof.evaluation.calibration import run_calibration
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.kernel.proof_language import contains_proof_language

SCORECARD_SCHEMA_VERSION = 2

FIXTURES_DIR = Path("tests/fixtures")
_GROUNDABLE_STATEMENT = (
    "Theorem 1.2. Exact literature statement supporting the Hodge-theoretic step "
    "over base field C for cubic fourfolds."
)


def _mark_probe_claim_clear(run_dir: Path, *, state: str | None = None) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream

    ensure_claim_clear_for_downstream(run_dir)
    if state is not None:
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        manifest["current_state"] = state
        write_yaml(run_dir / "run_manifest.yaml", manifest)


def _grounded_run(
    root: Path,
    *,
    statement: str = _GROUNDABLE_STATEMENT,
    authors: list[str] | None = None,
    project: str = "scorecard_ground",
) -> Path:
    """A deterministic run pushed through the REAL grounding pipeline. With the
    default statement/attribution it EARNS source_backed; near-miss variants
    (missing attribution, no exact locator) must stay candidate."""

    from espresso_proof.verification.source_verification import ground_run

    run_dir = run_mock_critique(
        project, FIXTURES_DIR / "hodge_all_cubic_fourfolds" / "input.md", root
    )
    _mark_probe_claim_clear(run_dir)
    src = root / "scorecard_source.txt"
    src.write_text(statement, encoding="utf-8")
    from espresso_proof.literature.pdf_ingestion import ingest_source_for_theorems

    claim = read_yaml(run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["locked_claim"])
    report = ingest_source_for_theorems(
        claim_id=claim["id"],
        source=str(src),
        source_id="scorecard_src",
        title="Scorecard probe source",
        authors=["A. Author"] if authors is None else authors,
        source_type="paper",
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = "theorem_extraction.yaml"
    write_yaml(run_dir / "theorem_extraction.yaml", report)
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    ground_run(run_dir=run_dir, theorem_extraction_path=run_dir / "theorem_extraction.yaml")
    return run_dir


def _earned_steps(run_dir: Path) -> int:
    sg = read_yaml(run_dir / "source_grounding.yaml")
    return sum(1 for s in sg["steps"] if s["status"] == "source_backed")


def _probe_source_grounding(root: Path) -> dict:
    """Grounding family (Goal 40C, n=3): the honest pipeline EARNS a reference-
    supported step, and the two classic near-misses stay candidate."""

    cases = []
    honest = _grounded_run(root / "honest")
    cases.append({"case": "honest_earns", "correct": _earned_steps(honest) > 0})
    no_attr = _grounded_run(root / "no_attr", authors=[], project="scorecard_noattr")
    cases.append({"case": "missing_attribution_stays_candidate", "correct": _earned_steps(no_attr) == 0})
    no_loc = _grounded_run(
        root / "no_loc",
        statement=(
            "An informal remark supporting the Hodge-theoretic step over base "
            "field C for cubic fourfolds, with no theorem label."
        ),
        project="scorecard_noloc",
    )
    cases.append({"case": "no_exact_locator_stays_candidate", "correct": _earned_steps(no_loc) == 0})
    correct = sum(1 for c in cases if c["correct"])
    return {"cases": cases, "correct": correct, "total": len(cases), "passed": correct == len(cases)}


def _probe_applicability_block(root: Path) -> dict:
    """Applicability family (Goal 40C, n=3): honest grounding audits applicable;
    a tampered cited statement is detected (Goal 41 excerpt-hash binding); a
    legacy-style tamper without hashes is still blocked by the independent
    audit recheck (Goal 37 de-circularization)."""

    from espresso_proof.verification.theorem_applicability import audit_theorem_applicability
    from espresso_proof.validation.errors import KernelValidationError
    from espresso_proof.kernel.validators import validate_run

    cases = []

    honest = _grounded_run(root / "honest", project="scorecard_app_ok")
    report = read_yaml(honest / "theorem_applicability.yaml")
    backed = [u for u in report["usages"] if u["source_status"] == "source_backed"]
    cases.append(
        {
            "case": "honest_audits_applicable",
            "correct": bool(backed)
            and all(u["applicability_status"] == "applicable" for u in backed),
        }
    )

    hash_tamper = _grounded_run(root / "hash_tamper", project="scorecard_app_hash")
    sg = read_yaml(hash_tamper / "source_grounding.yaml")
    for ref in sg["source_refs"]:
        if ref["status"] == "source_backed":
            ref["statement_or_summary"] = "Theorem 9.9. Every elliptic curve over Q is modular."
    write_yaml(hash_tamper / "source_grounding.yaml", sg)
    try:
        validate_run(hash_tamper)
        detected = False
    except KernelValidationError:
        detected = True  # excerpt-hash (or stale-audit) binding caught the edit
    cases.append({"case": "statement_edit_detected", "correct": detected})

    legacy = _grounded_run(root / "legacy_tamper", project="scorecard_app_leg")
    sg = read_yaml(legacy / "source_grounding.yaml")
    for ref in sg["source_refs"]:
        ref.pop("statement_sha256", None)
        if ref["status"] == "source_backed":
            ref["statement_or_summary"] = "Theorem 9.9. Every elliptic curve over Q is modular."
    write_yaml(legacy / "source_grounding.yaml", sg)
    audit_theorem_applicability(legacy)  # re-audit: no hash to catch the edit...
    report = read_yaml(legacy / "theorem_applicability.yaml")
    backed = [u for u in report["usages"] if u["source_status"] == "source_backed"]
    cases.append(
        {
            "case": "legacy_tamper_blocked_by_independent_recheck",
            "correct": bool(backed) and all(u["blocks_source_backed"] for u in backed),
        }
    )

    correct = sum(1 for c in cases if c["correct"])
    return {"cases": cases, "correct": correct, "total": len(cases), "passed": correct == len(cases)}


def _probe_scoped_verifier(root: Path) -> dict:
    """Verifier family (Goal 40C, n=2): a confirming run of the allowlisted
    adapter earns ONE scoped entry through the kernel bridge; a non-confirming
    run earns nothing (fail closed)."""

    from espresso_proof.verification.external_tool_adapters import run_exact_modexp_adapter

    cases = []
    run_dir = run_mock_critique(
        "scorecard_verify", FIXTURES_DIR / "promising_but_unproved" / "input.md", root
    )
    confirmed = run_exact_modexp_adapter(
        run_dir, base=4, exp=1, mod=4, expected=0, entry_id="scorecard_modexp"
    )
    entries = read_yaml(run_dir / "proof_verification.yaml")["entries"] if confirmed["earned"] else []
    scoped = [e for e in entries if e["earned_status"] == "computation_checked"]
    cases.append({"case": "confirming_run_earns_scoped", "correct": confirmed["earned"] and bool(scoped)})

    wrong = run_exact_modexp_adapter(
        run_dir, base=4, exp=1, mod=4, expected=1, entry_id="scorecard_modexp_wrong"
    )
    pv = run_dir / "proof_verification.yaml"
    wrong_entries = (
        [e for e in read_yaml(pv)["entries"] if "wrong" in e["id"]] if pv.exists() else []
    )
    cases.append(
        {"case": "non_confirming_run_earns_nothing", "correct": not wrong["earned"] and not wrong_entries}
    )
    correct = sum(1 for c in cases if c["correct"])
    return {"cases": cases, "correct": correct, "total": len(cases), "passed": correct == len(cases)}


def _probe_matcher_discipline() -> dict:
    """Matcher family (Goal 40C, n=3): negated terms and substring aliases must
    not fake hypothesis coverage; honest coverage must still match (Goal 41)."""

    from espresso_proof.verification.source_verification import check_hypothesis_applicability, term_present

    projective = [{"name": "X is projective", "status": "explicit", "source": "agent"}]
    cases = [
        {
            "case": "negated_term_no_cover",
            "correct": term_present("proper", "let x be a non-proper variety.") is False,
        },
        {
            "case": "substring_alias_no_cover",
            "correct": check_hypothesis_applicability(
                "Theorem 5.1. Let X be a proper variety. For example, a curve.",
                projective,
                [],
            )["matches"] is not True,  # may be the string "unclear"
        },
        {
            "case": "honest_coverage_matches",
            "correct": term_present("proper", "let x be a proper variety.") is True,
        },
    ]
    correct = sum(1 for c in cases if c["correct"])
    return {"cases": cases, "correct": correct, "total": len(cases), "passed": correct == len(cases)}


def _probe_counterexample_discipline() -> dict:
    """Counterexample family (Goal 42E cycle 11, n=6): the deterministic
    probes from loop cycles 1/8/10 must keep their earned outcomes — an
    admissible checked refutation exactly where a non-abelian construction
    exists, silence on every true claim, and a non-refuting exposing record
    for the missing-hypothesis family."""

    from espresso_proof.verification.counterexample_probes import propose_deterministic_counterexamples

    def group_claim(order: int) -> dict:
        return {
            "normalized_statement": f"Every group of order {order} is abelian.",
            "hypotheses": [
                {"name": f"G is a group of order {order}", "status": "explicit", "source": "user"}
            ],
        }

    dihedral = propose_deterministic_counterexamples(group_claim(6))
    semidirect = propose_deterministic_counterexamples(group_claim(21))
    missing = propose_deterministic_counterexamples(
        {
            "normalized_statement": (
                "Every continuous function f: R -> R attains a maximum value."
            ),
            "hypotheses": [],
        }
    )
    cases = [
        {
            "case": "dihedral_refutes_order6",
            "correct": bool(dihedral) and dihedral[0]["refutes_claim"] is True,
        },
        {
            "case": "semidirect_refutes_order21",
            "correct": bool(semidirect) and semidirect[0]["refutes_claim"] is True,
        },
        {
            # 15: all groups cyclic; 27: prime power out of scope — silence.
            "case": "true_or_out_of_scope_claims_stay_silent",
            "correct": propose_deterministic_counterexamples(group_claim(15)) == []
            and propose_deterministic_counterexamples(group_claim(27)) == [],
        },
        {
            "case": "negated_claim_stays_silent",
            "correct": propose_deterministic_counterexamples(
                {"normalized_statement": "Every group of order 6 is non-abelian."}
            )
            == [],
        },
        {
            "case": "missing_hypothesis_exposes_without_refuting",
            "correct": bool(missing)
            and missing[0]["exposes_missing_hypothesis"] is True
            and missing[0]["refutes_claim"] is False
            and bool(str(missing[0].get("repaired_claim_suggestion") or "").strip()),
        },
        {
            # undetermined hypotheses never earn a refutation (cycle-1 rule).
            "case": "undetermined_hypotheses_downgrade",
            "correct": (
                lambda t: bool(t) and t[0]["refutes_claim"] is False
            )(
                propose_deterministic_counterexamples(
                    {"normalized_statement": "Every group of order 6 is abelian."}
                )
            ),
        },
    ]
    correct = sum(1 for c in cases if c["correct"])
    return {"cases": cases, "correct": correct, "total": len(cases), "passed": correct == len(cases)}


def _probe_reviewer_quality(root: Path) -> dict:
    """Reviewer-quality benchmark: seed GOLD FLAWS into a fixture run and require
    the artifact-grounded reviewer checklist to recall every high-severity flaw.
    Unsafe proof-language pass-through fails the scorecard."""

    from espresso_proof.evaluation.reviewer_checklist import review_checklist

    run_dir = run_mock_critique(
        "scorecard_review", FIXTURES_DIR / "promising_but_unproved" / "input.md", root
    )
    seeded = 2
    # Gold flaw 1: proof-complete language in the final report.
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    final_report = run_dir / manifest["artifacts"]["final_report"]
    final_report.write_text(
        final_report.read_text(encoding="utf-8") + "\nWe proved the theorem. proof-close.\n",
        encoding="utf-8",
    )
    # Gold flaw 2: an unauthorized source_backed ref (no locator / attribution).
    sg = read_yaml(run_dir / manifest["artifacts"]["source_grounding"])
    sg["source_refs"].append(
        {
            "id": "gold_flaw_ref",
            "type": "paper",
            "title": "",
            "authors": [],
            "locator": {"kind": "unknown", "value": ""},
            "statement_or_summary": "Seeded gold flaw.",
            "hypothesis_match": {"matches": "unclear", "mismatches": []},
            "confidence": "low",
            "status": "source_backed",
        }
    )
    write_yaml(run_dir / manifest["artifacts"]["source_grounding"], sg)

    checklist = review_checklist(run_dir)
    finding_texts = [str(f.get("text", "")) for f in checklist.get("findings", [])]
    found = 0
    if any("proof-complete language" in text for text in finding_texts):
        found += 1
    if any("gold_flaw_ref" in text for text in finding_texts):
        found += 1
    # seeded blockers must also block a merge (unsafe pass-through fails).
    merge_blocked = not checklist.get("merge_allowed", True)
    return {
        "seeded": seeded,
        "recalled": found,
        "merge_blocked": merge_blocked,
        "passed": found == seeded and merge_blocked,
    }


def _probe_dogfood_replay(root: Path) -> dict:
    """Sanitized dogfood replay: regenerate a deterministic fixture dogfood run
    (sanitized by construction — committed fixtures only, no private data), run
    the full resume loop, and replay-check the memo + statuses: honest memo, no
    unauthorized source_backed, bounded loop stop reason."""

    from espresso_proof.ui.memo import build_concise_memo
    from espresso_proof.orchestration.orchestrator import orchestrate_run, resume_run
    from espresso_proof.kernel.validators import validate_run

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    claim_path = root / "dogfood_claim.md"
    claim_path.write_text(
        "Let p be a prime. Every group G of order p^2 is abelian.\n",
        encoding="utf-8",
    )
    result = orchestrate_run(
        project="scorecard_dogfood",
        input_path=claim_path,
        output_root=root,
        provider="stub",
        front_half="stub",
    )
    run_dir = Path(result["run_dir"])
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(
        run_dir / manifest["artifacts"]["strategy_brief"]
    )
    # The scorecard picks the strategy itself — no human is in this loop — so it
    # must say so: selected_by="auto" records honest provenance and the persisted
    # human_choice / gate artifacts carry human_confirmed=false.
    resume_run(
        run_dir=run_dir,
        selected_strategy=brief["top_strategies"][0]["id"],
        effort="normal_pass",
        prover_provider="stub",
        selected_by="auto",
    )
    validate_run(run_dir)
    # Provenance honesty check: this automated replay must never leave artifacts
    # claiming a human confirmed anything.
    human_choice = read_yaml(run_dir / manifest["artifacts"]["human_choice"])
    agent_run_path = manifest.get("optional_artifacts", {}).get(
        "llm_agent_run", "llm_agent_run.yaml"
    )
    agent_gate = read_yaml(run_dir / agent_run_path).get("human_gate", {})
    auto_provenance_honest = (
        human_choice.get("selected_by") == "auto"
        and human_choice.get("human_confirmed") is False
        and agent_gate.get("resolved_by") == "auto"
        and agent_gate.get("human_confirmed") is False
    )
    memo = build_concise_memo(run_dir)
    memo_honest = contains_proof_language(memo) is None
    sg = read_yaml(run_dir / "source_grounding.yaml")
    unauthorized_steps = [
        s for s in sg.get("steps", []) if s.get("status") == "source_backed"
    ]
    unauthorized_refs = [
        ref for ref in sg.get("source_refs", []) if ref.get("status") == "source_backed"
    ]
    loop = read_yaml(run_dir / "prover_loop.yaml")
    rounds = {}
    for attempt in loop["attempts"]:
        rounds.setdefault(attempt["round_index"], []).append(attempt)
    last_round = max(rounds)
    # repair-round non-regression: the final round must not have MORE blockers
    # than the first (the stub loop is deterministic, so this is stable).
    first_blockers = min(len(a["blockers"]) for a in rounds[min(rounds)])
    last_blockers = min(len(a["blockers"]) for a in rounds[last_round])
    # bounded = a value the kernel's ProverLoopReport Literal admits (the driver
    # records the STOP REASON separately in its return value; the durable
    # artifact carries the bounded loop_decision).
    stop_bounded = loop.get("loop_decision") in {
        "continue", "repair", "switch_strategy", "ask_human", "stop",
    }
    return {
        "memo_honest": memo_honest,
        "unauthorized_source_backed": len(unauthorized_steps) + len(unauthorized_refs),
        "rounds_run": last_round,
        "repair_round_non_regression": last_blockers <= first_blockers,
        "stop_bounded": bool(stop_bounded),
        "auto_provenance_honest": bool(auto_provenance_honest),
        "passed": (
            memo_honest
            and auto_provenance_honest
            and not unauthorized_steps
            and not unauthorized_refs
            and stop_bounded
            and last_blockers <= first_blockers
        ),
    }


def run_scorecard(output_root: Path) -> dict:
    """Run the full Autoprover Behavior Scorecard. Deterministic; earns nothing."""

    output_root = Path(output_root)
    bench = run_benchmark(output_root / "benchmark")
    calib = run_calibration(output_root / "calibration")
    cm = calib["metrics"]
    bench_passed = sum(1 for row in bench if row["passed"])
    counterexample_rows = [r for r in bench if r["category"] == "checked_counterexample"]

    grounding = _probe_source_grounding(output_root / "probes" / "grounding")
    applicability = _probe_applicability_block(output_root / "probes" / "applicability")
    verifier = _probe_scoped_verifier(output_root / "probes" / "verifier")
    matcher = _probe_matcher_discipline()
    counterexample = _probe_counterexample_discipline()
    reviewer = _probe_reviewer_quality(output_root / "probes" / "reviewer")
    dogfood = _probe_dogfood_replay(output_root / "probes" / "dogfood")

    safety = {
        # both MUST be zero, always.
        "false_verified_count": cm["proof_language_violations"],
        "unauthorized_source_backed_count": cm["source_support_false_positives"]
        + dogfood["unauthorized_source_backed"],
        "strategy_high_score_false_positives": cm["strategy_high_score_false_positives"],
        "memo_honest_rate": f"{cm['memo_honest']}/{cm['fixtures_total']}",
        "benchmark_cases_passed": f"{bench_passed}/{len(bench)}",
    }
    capability = {
        "exact_source_grounding_rate": grounding["correct"] / grounding["total"],
        "theorem_applicability_block_recall": applicability["correct"] / applicability["total"],
        "counterexample_scope_accuracy": (
            sum(1 for r in counterexample_rows if r["passed"]) / len(counterexample_rows)
            if counterexample_rows
            else 0.0
        ),
        "scoped_verifier_wiring_success": verifier["correct"] / verifier["total"],
        "matcher_discipline_rate": matcher["correct"] / matcher["total"],
        "counterexample_discipline_rate": counterexample["correct"] / counterexample["total"],
        "reviewer_objection_recall": (
            reviewer["recalled"] / reviewer["seeded"] if reviewer["seeded"] else 0.0
        ),
        "repair_round_non_regression": dogfood["repair_round_non_regression"],
        "dogfood_replay_honest": dogfood["passed"],
    }
    clean = (
        cm["clean"]
        and bench_passed == len(bench)
        and safety["false_verified_count"] == 0
        and safety["unauthorized_source_backed_count"] == 0
        and grounding["passed"]
        and applicability["passed"]
        and verifier["passed"]
        and matcher["passed"]
        and counterexample["passed"]
        and reviewer["passed"]
        and dogfood["passed"]
    )
    return {
        "schema_version": SCORECARD_SCHEMA_VERSION,
        "safety": safety,
        "capability": capability,
        "probes": {
            "source_grounding": grounding,
            "applicability_block": applicability,
            "scoped_verifier": verifier,
            "matcher_discipline": matcher,
            "counterexample_discipline": counterexample,
            "reviewer_quality": reviewer,
            "dogfood_replay": dogfood,
        },
        "clean": clean,
    }


def render_scorecard_markdown(report: dict) -> str:
    """Stable, trend-friendly Markdown summary (no timestamps)."""

    lines = [
        "# Autoprover Behavior Scorecard",
        "",
        f"Schema version: {report['schema_version']}",
        f"Overall: {'CLEAN' if report['clean'] else 'REGRESSION DETECTED'}",
        "",
        "## Safety (must always be zero / full)",
        "",
    ]
    for key, value in report["safety"].items():
        lines.append(f"- {key}: {value}")
    lines += ["", "## Capability", ""]
    for key, value in report["capability"].items():
        lines.append(f"- {key}: {value}")
    lines += [
        "",
        "All metrics are deterministic fixture probes over small per-family case",
        "sets (grounding n=3, applicability n=3, verifier n=2, matcher n=3,",
        "counterexample n=6, reviewer gold flaws n=2, dogfood replay n=1):",
        "they detect regressions",
        "in conservative behavior and earning-pipeline wiring. The probes",
        "never earn or upgrade any mathematical status, and no claim is ever",
        "'proved' by a scorecard.",
        "",
    ]
    return "\n".join(lines)


__all__ = ["run_scorecard", "render_scorecard_markdown", "SCORECARD_SCHEMA_VERSION"]
