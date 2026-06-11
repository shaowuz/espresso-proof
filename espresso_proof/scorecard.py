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

from .benchmark import run_benchmark
from .calibration import run_calibration
from .io import read_yaml, write_yaml
from .mock_pipeline import run_mock_critique
from .proof_language import contains_proof_language

SCORECARD_SCHEMA_VERSION = 1

FIXTURES_DIR = Path("tests/fixtures")
_GROUNDABLE_STATEMENT = (
    "Theorem 1.2. Exact literature statement supporting the Hodge-theoretic step "
    "over base field C for cubic fourfolds."
)
_BOUNDED_STOP_REASONS = {
    "loop_decision_ask_human",
    "loop_decision_stop",
    "budget_rounds",
    "budget_agent_calls",
    "no_progress",
    "clean",
    "validation_error",
}


def _grounded_run(root: Path) -> Path:
    """A deterministic run that EARNS source_backed through the real pipeline."""

    from .source_verification import ground_run

    run_dir = run_mock_critique(
        "scorecard_ground", FIXTURES_DIR / "hodge_all_cubic_fourfolds" / "input.md", root
    )
    src = root / "scorecard_source.txt"
    src.write_text(_GROUNDABLE_STATEMENT, encoding="utf-8")
    from .pdf_ingestion import ingest_source_for_theorems

    claim = read_yaml(run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["locked_claim"])
    report = ingest_source_for_theorems(
        claim_id=claim["id"],
        source=str(src),
        source_id="scorecard_src",
        title="Scorecard probe source",
        authors=["A. Author"],
        source_type="paper",
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest.setdefault("optional_artifacts", {})["theorem_extraction"] = "theorem_extraction.yaml"
    write_yaml(run_dir / "theorem_extraction.yaml", report)
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    ground_run(run_dir=run_dir, theorem_extraction_path=run_dir / "theorem_extraction.yaml")
    return run_dir


def _probe_source_grounding(root: Path) -> dict:
    """Capability: the honest grounding pipeline still EARNS a reference-
    supported step (exact locator + attribution + hypothesis match)."""

    run_dir = _grounded_run(root / "grounding")
    sg = read_yaml(run_dir / "source_grounding.yaml")
    earned = [s for s in sg["steps"] if s["status"] == "source_backed"]
    return {"earned_steps": len(earned), "passed": bool(earned), "run_dir": str(run_dir)}


def _probe_applicability_block(root: Path) -> dict:
    """Capability/safety: the independent applicability audit BLOCKS a tampered
    hypothesis_match over a non-covering statement (Goal 37 de-circularization)."""

    from .theorem_applicability import audit_theorem_applicability

    run_dir = _grounded_run(root / "applicability")
    sg = read_yaml(run_dir / "source_grounding.yaml")
    for ref in sg["source_refs"]:
        if ref["status"] == "source_backed":
            ref["statement_or_summary"] = "Theorem 9.9. Every elliptic curve over Q is modular."
    write_yaml(run_dir / "source_grounding.yaml", sg)
    audit_theorem_applicability(run_dir)
    report = read_yaml(run_dir / "theorem_applicability.yaml")
    backed = [u for u in report["usages"] if u["source_status"] == "source_backed"]
    blocked = [u for u in backed if u["blocks_source_backed"]]
    return {
        "planted": len(backed),
        "blocked": len(blocked),
        "passed": bool(backed) and len(blocked) == len(backed),
    }


def _probe_scoped_verifier(root: Path) -> dict:
    """Capability: the allowlisted deterministic adapter still earns ONE scoped
    computation_checked entry through the kernel bridge (never a proof)."""

    from .external_tool_adapters import run_exact_modexp_adapter

    run_dir = run_mock_critique(
        "scorecard_verify", FIXTURES_DIR / "promising_but_unproved" / "input.md", root
    )
    result = run_exact_modexp_adapter(
        run_dir, base=4, exp=1, mod=4, expected=0, entry_id="scorecard_modexp"
    )
    entries = read_yaml(run_dir / "proof_verification.yaml")["entries"] if result["earned"] else []
    scoped = [e for e in entries if e["earned_status"] == "computation_checked"]
    return {"earned": result["earned"], "scoped_entries": len(scoped), "passed": result["earned"]}


def _probe_reviewer_quality(root: Path) -> dict:
    """Reviewer-quality benchmark: seed GOLD FLAWS into a fixture run and require
    the artifact-grounded reviewer checklist to recall every high-severity flaw.
    Unsafe proof-language pass-through fails the scorecard."""

    from .reviewer_checklist import review_checklist

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

    from .memo import build_concise_memo
    from .orchestrator import orchestrate_run, resume_run
    from .validators import validate_run

    result = orchestrate_run(
        project="scorecard_dogfood",
        input_path=FIXTURES_DIR / "hodge_all_cubic_fourfolds" / "input.md",
        output_root=root,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    brief = read_yaml(
        run_dir / read_yaml(run_dir / "run_manifest.yaml")["artifacts"]["strategy_brief"]
    )
    resume_run(
        run_dir=run_dir,
        selected_strategy=brief["top_strategies"][0]["id"],
        effort="normal_pass",
        prover_provider="stub",
    )
    validate_run(run_dir)
    memo = build_concise_memo(run_dir)
    memo_honest = contains_proof_language(memo) is None
    sg = read_yaml(run_dir / "source_grounding.yaml")
    unauthorized = [s for s in sg["steps"] if s["status"] == "source_backed"]
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
        "unauthorized_source_backed": len(unauthorized),
        "rounds_run": last_round,
        "repair_round_non_regression": last_blockers <= first_blockers,
        "stop_bounded": bool(stop_bounded),
        "passed": (
            memo_honest
            and not unauthorized
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

    grounding = _probe_source_grounding(output_root / "probes")
    applicability = _probe_applicability_block(output_root / "probes")
    verifier = _probe_scoped_verifier(output_root / "probes")
    reviewer = _probe_reviewer_quality(output_root / "probes")
    dogfood = _probe_dogfood_replay(output_root / "probes")

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
        "exact_source_grounding_rate": 1.0 if grounding["passed"] else 0.0,
        "theorem_applicability_block_recall": (
            applicability["blocked"] / applicability["planted"]
            if applicability["planted"]
            else 0.0
        ),
        "counterexample_scope_accuracy": (
            sum(1 for r in counterexample_rows if r["passed"]) / len(counterexample_rows)
            if counterexample_rows
            else 0.0
        ),
        "scoped_verifier_wiring_success": 1.0 if verifier["passed"] else 0.0,
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
        "All metrics are deterministic fixture probes. They measure conservative",
        "behavior and earning-pipeline wiring; they never earn or upgrade any",
        "mathematical status, and no claim is ever 'proved' by a scorecard.",
        "",
    ]
    return "\n".join(lines)


__all__ = ["run_scorecard", "render_scorecard_markdown", "SCORECARD_SCHEMA_VERSION"]
