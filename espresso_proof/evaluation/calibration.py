"""Calibration report (Goal 11).

A deterministic, gate-friendly meta-eval over the EXISTING mock fixtures that
aggregates the product's core SAFETY metrics, so we can tell whether Espresso is
still conservative as it changes:

- every fixture run validates (validate_run);
- source-support false positives: a step marked source_backed whose cited ref
  lacks an exact locator + attribution (must be 0 — the kernel guarantees this);
- proof-language violations: a memo containing proof-complete language (must be 0);
- strategy high-score false positives: a route scoring >= the high-score
  threshold while the idea status is weak (must be 0 — strategy_rules guarantee);
- memo honesty: validated AND no proof-complete language.

It earns no status and never asserts a claim is proved. Any nonzero safety metric
is a regression (the CLI exits non-zero).
"""

from __future__ import annotations

from pathlib import Path

from espresso_proof.evaluation.benchmark import FIXTURES_DIR, FORBIDDEN_PHRASES
from espresso_proof.kernel.io import read_yaml
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.kernel.strategy_rules import HIGH_SCORE_THRESHOLD
from espresso_proof.kernel.validators import validate_run

_WEAK_STATUSES = {"unclear", "likely_false", "refuted"}


def all_fixtures() -> list[str]:
    return sorted(p.name for p in FIXTURES_DIR.iterdir() if (p / "input.md").exists())


def _source_support_false_positives(run_dir: Path) -> int:
    # A redundant cross-check layered on top of validate_run, which (via
    # SourceRef.validate_source_backed) remains the authoritative source_backed
    # gate. Use the kernel's own Locator.is_exact so this cannot drift looser than
    # the gate it shadows.
    from espresso_proof.kernel.models import Locator

    sg = read_yaml(run_dir / "source_grounding.yaml") or {}
    refs = {r.get("id"): r for r in sg.get("source_refs", [])}
    fp = 0
    for step in sg.get("steps", []):
        if step.get("status") != "source_backed":
            continue
        for rid in step.get("source_refs", []):
            ref = refs.get(rid, {})
            try:
                exact = Locator.model_validate(ref.get("locator", {}) or {}).is_exact
            except Exception:  # noqa: BLE001 - a malformed locator is not exact
                exact = False
            attributed = bool([a for a in ref.get("authors", []) if str(a).strip()]) and bool(ref.get("title"))
            if not (exact and attributed and ref.get("status") == "source_backed"):
                fp += 1
    return fp


def _strategy_high_score_false_positives(run_dir: Path) -> int:
    grade = read_yaml(run_dir / "idea_grade.yaml") or {}
    if grade.get("status") not in _WEAK_STATUSES:
        return 0
    brief = read_yaml(run_dir / "strategy_brief.yaml") or {}
    return sum(
        1 for r in brief.get("top_strategies", [])
        if int(r.get("score", 0) or 0) >= HIGH_SCORE_THRESHOLD
    )


def run_calibration(output_root: Path, *, fixtures: list[str] | None = None) -> dict:
    fixtures = fixtures if fixtures is not None else all_fixtures()
    rows: list[dict] = []
    metrics = {
        "fixtures_total": 0,
        "fixtures_validated": 0,
        "source_support_false_positives": 0,
        "proof_language_violations": 0,
        "strategy_high_score_false_positives": 0,
        "memo_honest": 0,
    }
    for fx in fixtures:
        run_dir = run_mock_critique("calib", FIXTURES_DIR / fx / "input.md", output_root)
        try:
            validate_run(run_dir)
            validated = True
        except Exception:  # noqa: BLE001 - record the regression, do not abort the report
            validated = False
        memo = build_concise_memo(run_dir).lower()
        proof_language = [p for p in FORBIDDEN_PHRASES if p in memo]
        sb_fp = _source_support_false_positives(run_dir)
        hs_fp = _strategy_high_score_false_positives(run_dir)
        honest = validated and not proof_language
        rows.append({
            "fixture": fx,
            "validated": validated,
            "proof_language": proof_language,
            "source_support_false_positives": sb_fp,
            "strategy_high_score_false_positives": hs_fp,
            "memo_honest": honest,
        })
        metrics["fixtures_total"] += 1
        metrics["fixtures_validated"] += int(validated)
        metrics["source_support_false_positives"] += sb_fp
        metrics["proof_language_violations"] += len(proof_language)
        metrics["strategy_high_score_false_positives"] += hs_fp
        metrics["memo_honest"] += int(honest)
    metrics["clean"] = (
        metrics["fixtures_validated"] == metrics["fixtures_total"]
        and metrics["source_support_false_positives"] == 0
        and metrics["proof_language_violations"] == 0
        and metrics["strategy_high_score_false_positives"] == 0
    )
    return {"rows": rows, "metrics": metrics}
