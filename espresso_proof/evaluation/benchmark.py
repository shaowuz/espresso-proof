"""Regression benchmark / eval set V1.

A small, deterministic, gate-friendly behaviour benchmark over EXISTING mock
fixtures. It does not invent fixtures or a parallel framework: it curates the
five required regression categories, runs each fixture through the existing mock
pipeline + ``validate_run``, and checks CONSERVATIVE behaviour (status bucket,
no proof-complete language, source-not-applicable not reference-supported,
counterexample scope). It earns no status and never asserts a claim is proved.

Categories:

- ``true_claim``: a plausible (true-ish) but unproved claim stays "promising",
  never "proved";
- ``false_claim``: a false claim grades "likely_false" with candidate sources;
- ``missing_hypothesis``: an imprecise / hypothesis-missing claim stays
  "unclear" (needs a precise claim first);
- ``source_not_applicable``: a cited source whose theorem does not apply is NOT
  reference-supported (no source_backed);
- ``checked_counterexample``: an all-hypotheses counterexample refutes the claim
  only because its scope matches (satisfies all claim hypotheses).
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.io import read_text, read_yaml
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.validators import validate_run

FIXTURES_DIR = Path("tests/fixtures")

# Proof-complete language that must never appear in user-facing output. These are
# specific markers, NOT bare words: "unproved"/"not verified"/"no counterexample
# has been verified" are legitimate conservative phrasings.
FORBIDDEN_PHRASES = (
    "proof-close",
    "proof complete",
    "proof is complete",
    "we proved",
    "proved the claim",
    "proved the theorem",
    "espresso proved",
)

# Curated benchmark cases: category -> fixture + conservative expectation.
BENCHMARK_V1: list[dict] = [
    {"category": "true_claim", "fixture": "promising_but_unproved", "expect_status": ["promising"]},
    {"category": "false_claim", "fixture": "likely_false_multiple_pressures", "expect_status": ["likely_false"]},
    {"category": "missing_hypothesis", "fixture": "vague_claim_unclear", "expect_status": ["unclear"]},
    {"category": "source_not_applicable", "fixture": "source_misapplication_very_general_to_all",
     "expect_status": None, "expect_no_source_backed": True},
    {"category": "checked_counterexample", "fixture": "refuted_by_all_hypotheses_counterexample",
     "expect_status": ["refuted"], "expect_scoped_refutation": True},
]


def _artifact(run_dir: Path, key: str) -> dict:
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return read_yaml(run_dir / artifacts[key])


def _check_case(case: dict, run_dir: Path) -> list[str]:
    """Return a list of conservative-behaviour violations (empty == pass)."""

    problems: list[str] = []
    grade = _artifact(run_dir, "idea_grade")
    status = grade.get("status")

    # status bucket (when the category pins one)
    if case.get("expect_status") and status not in case["expect_status"]:
        problems.append(f"idea status {status!r} not in {case['expect_status']!r}")

    # no proof-complete language in user-facing output (final report + memo)
    final_report = read_text(run_dir / "final_report.md").lower()
    memo = build_concise_memo(run_dir).lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase in final_report:
            problems.append(f"final_report contains forbidden phrase {phrase!r}")
        if phrase in memo:
            problems.append(f"memo contains forbidden phrase {phrase!r}")

    # source-not-applicable: nothing may be reference-supported
    if case.get("expect_no_source_backed"):
        sg = _artifact(run_dir, "source_grounding")
        backed = [
            r.get("id")
            for r in sg.get("source_refs", [])
            if r.get("status") == "source_backed"
        ] + [s.get("proof_step_id") for s in sg.get("steps", []) if s.get("status") == "source_backed"]
        if backed:
            problems.append(f"source-not-applicable case has source_backed: {backed}")

    # checked counterexample: a claim refutation requires all-hypotheses scope
    if case.get("expect_scoped_refutation"):
        cx = _artifact(run_dir, "counterexamples")
        refuters = [t for t in cx.get("tests", []) if t.get("refutes_claim")]
        if not refuters:
            problems.append("checked-counterexample case has no claim-refuting test")
        for t in refuters:
            if t.get("satisfies_all_claim_hypotheses") is not True:
                problems.append(
                    f"counterexample {t.get('id')} refutes the claim without satisfying all hypotheses"
                )

    return problems


def run_benchmark(output_root: Path, *, cases: list[dict] | None = None) -> list[dict]:
    """Run the curated benchmark and return a stable (category-sorted) report.

    Each entry: ``{category, fixture, idea_status, passed, problems}``. Read-only
    with respect to the repository (runs land under ``output_root``); deterministic
    because the mock pipeline is deterministic.
    """

    output_root = Path(output_root)
    selected = cases if cases is not None else BENCHMARK_V1
    report: list[dict] = []
    for case in sorted(selected, key=lambda c: c["category"]):
        fixture = case["fixture"]
        input_path = FIXTURES_DIR / fixture / "input.md"
        run_dir = run_mock_critique("hodge_atoms", input_path, output_root / fixture)
        validate_run(run_dir)  # fail-closed: each benchmark run must be kernel-valid
        problems = _check_case(case, run_dir)
        report.append(
            {
                "category": case["category"],
                "fixture": fixture,
                "idea_status": _artifact(run_dir, "idea_grade").get("status"),
                "passed": not problems,
                "problems": problems,
            }
        )
    return report
