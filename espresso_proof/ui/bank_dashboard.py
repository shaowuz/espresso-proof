"""Research Bank Dashboard V0 (read-only static HTML).

Render a small self-contained HTML page summarizing a run's examples,
counterexamples, and strategies. It reads the three artifacts through the
existing bank-aware loaders, so it works for BOTH the per-item bank directory
layout and the old single-YAML layout, and it NEVER mutates run artifacts.

Safety: this is a read-only view. It invents no statuses; it maps the existing
example/counterexample/strategy statuses to human-readable wording (per
``docs/goals/HUMAN_READABLE_OUTPUT_LABELS.md``) and emits no proof-complete
language. Missing optional fields render conservatively as an em dash.
"""

from __future__ import annotations

import html
from pathlib import Path

from espresso_proof.ui.descriptions import describe_idea_status, route_source_phrase
from espresso_proof.kernel.io import read_yaml
from espresso_proof.kernel.proof_language import scrub_source_support_language
from espresso_proof.kernel.research_bank import load_bankable_dict

_NONE = "—"

# ExampleStatus -> human wording. Examples/counterexamples are tests, never a
# proof: the wording deliberately avoids proof-complete language.
_EXAMPLE_RESULT_PHRASES = {
    "survived": "Survived testing on examples (does not prove the claim)",
    "failed": "A test found a problem on examples",
    "inconclusive": "Not enough information to assess safely",
    "not_tested": "Not yet tested",
}


def _h(value: object) -> str:
    """HTML-escape a value; conservative em dash for empty/None."""

    text = "" if value is None else str(value)
    text = scrub_source_support_language(text.strip())
    return html.escape(text) if text else _NONE


def _example_result(value: object) -> str:
    return _EXAMPLE_RESULT_PHRASES.get(str(value), "Reported on examples (needs a check)")


def _route_source_phrase(value: object) -> str:
    if not value:
        return _NONE
    return html.escape(route_source_phrase(str(value)))


def _row(label: str, value: str) -> str:
    return f'<div class="field"><span class="k">{html.escape(label)}:</span> <span class="v">{value}</span></div>'


def _list_field(values: list | None) -> str:
    items = [
        scrub_source_support_language(str(v).strip())
        for v in (values or [])
        if str(v).strip()
    ]
    if not items:
        return _NONE
    return "; ".join(html.escape(v) for v in items)


def _example_card(ex: dict) -> str:
    # ``what_it_tests`` is a list[str]; render it joined, and use the name as the
    # primary summary (falling back to the joined tests, never a list literal).
    what = _list_field(ex.get("what_it_tests"))
    summary = _h(ex.get("name")) if ex.get("name") else what
    lines = [f'<div class="item"><div class="id">{_h(ex.get("id"))}</div>']
    lines.append(_row("Summary", summary))
    if ex.get("what_it_tests"):
        lines.append(_row("What it tests", what))
    lines.append(_row("Result on examples", html.escape(_example_result(ex.get("result_for_current_idea")))))
    lines.append(_row("Source / reason", _h(ex.get("source_or_reason"))))
    lines.append(_row("Satisfies all claim hypotheses", _h(ex.get("satisfies_all_claim_hypotheses"))))
    notes = []
    if ex.get("nontriviality"):
        notes.append(str(ex["nontriviality"]))
    if ex.get("hypothesis_mismatches"):
        notes.append("dropped/mismatched: " + ", ".join(str(m) for m in ex["hypothesis_mismatches"]))
    lines.append(_row("Notes", _h("; ".join(notes)) if notes else _NONE))
    lines.append("</div>")
    return "\n".join(lines)


def _counterexample_card(ce: dict) -> str:
    lines = [f'<div class="item"><div class="id">{_h(ce.get("id"))}</div>']
    lines.append(_row("Summary", _h(ce.get("explanation"))))
    lines.append(_row("Target", _h(ce.get("target"))))
    lines.append(_row("Result on examples", html.escape(_example_result(ce.get("result")))))
    # Refutation scope is shown as recorded; a dropped-hypothesis/proof-step test
    # is never a claim refutation (enforced by the model, surfaced here as-is).
    scope = []
    if ce.get("refutes_claim"):
        scope.append("reported as refuting the claim")
    if ce.get("refutes_step_only"):
        scope.append("attacks a proof step only (not the claim)")
    if ce.get("exposes_missing_hypothesis"):
        scope.append(f"exposes missing hypothesis: {ce.get('missing_hypothesis') or 'unspecified'}")
    lines.append(_row("Scope", _h("; ".join(scope)) if scope else _NONE))
    lines.append(_row("Source / reason", _h(ce.get("support"))))
    lines.append(_row("Notes", _h(ce.get("repaired_claim_suggestion"))))
    lines.append("</div>")
    return "\n".join(lines)


def _strategy_card(st: dict) -> str:
    lines = [f'<div class="item"><div class="id">{_h(st.get("id"))}</div>']
    lines.append(_row("Summary", _h(st.get("title"))))
    lines.append(_row("Status", html.escape(describe_idea_status(st.get("status")))))
    lines.append(_row("Score", _h(st.get("score"))))
    lines.append(_row("Source", _route_source_phrase(st.get("source_status"))))
    lines.append(_row("Reason", _h(st.get("why_this_route"))))
    lines.append(_row("Risk", _h(st.get("main_risk"))))
    lines.append(_row("Effort", _h(st.get("estimated_effort"))))
    lines.append(_row("Survived tests", _list_field(st.get("survived_tests"))))
    lines.append(_row("Next plan", _h(st.get("next_loop_plan"))))
    lines.append("</div>")
    return "\n".join(lines)


def _section(title: str, count: int, cards: list[str]) -> str:
    body = "\n".join(cards) if cards else '<div class="empty">No items recorded.</div>'
    return f'<section><h2>{html.escape(title)} ({count})</h2>{body}</section>'


_STYLE = """
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:2rem;color:#1b1b1b;background:#fafafa}
h1{font-size:1.4rem} h2{font-size:1.1rem;border-bottom:1px solid #ddd;padding-bottom:.2rem}
.note{color:#555;font-size:.85rem} .item{border:1px solid #e0e0e0;border-radius:6px;padding:.6rem .8rem;margin:.5rem 0;background:#fff}
.id{font-weight:600;font-family:ui-monospace,monospace;margin-bottom:.3rem}
.field{font-size:.9rem;margin:.1rem 0}.k{color:#555}.v{color:#111}.empty{color:#888;font-style:italic}
""".strip()


def render_bank_dashboard(run_dir: Path) -> str:
    """Render a read-only static HTML dashboard for a run's research banks.

    Reads examples / counterexamples / strategies through the bank-aware loaders
    (works for bank directories or single YAML files) and never mutates any
    artifact.
    """

    run_dir = Path(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_id = manifest.get("project_id", "")

    # Manifest-aware: works for single-file or per-item bank-directory layouts.
    examples = load_bankable_dict(run_dir, "examples", manifest)
    counterexamples = load_bankable_dict(run_dir, "counterexamples", manifest)
    strategies = load_bankable_dict(run_dir, "strategy_synthesis", manifest)

    ex_items = examples.get("examples", [])
    ce_items = counterexamples.get("tests", [])
    st_items = strategies.get("routes", [])

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>Research Bank Dashboard — {html.escape(str(run_dir.name))}</title>",
        f"<style>{_STYLE}</style></head><body>",
        f"<h1>Research Bank Dashboard</h1>",
        f'<div class="note">Run: {html.escape(str(run_dir.name))} · '
        f"Claim: {html.escape(str(examples.get('claim_id', claim_id)))} · "
        "Read-only view of recorded research items; statuses are as recorded and an "
        "assessment is not a proof.</div>",
        _section("Examples", len(ex_items), [_example_card(e) for e in ex_items]),
        _section("Counterexamples", len(ce_items), [_counterexample_card(c) for c in ce_items]),
        _section("Strategies", len(st_items), [_strategy_card(s) for s in st_items]),
        "</body></html>",
    ]
    return "\n".join(parts)
