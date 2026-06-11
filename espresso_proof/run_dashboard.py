"""Espresso Proof run dashboard — the Mathematical Proof Workspace.

A read-only research cockpit for one run (see docs/design/UI_DESIGN_DIRECTIONS.md).
The claim and proof routes are the protagonists; the right-hand evidence panel
lets a mathematician *read* the actual artifacts (candidate papers with clickable
locators, tested examples, counterexample attempts, failure-pattern warnings)
while agents are still running; the top bar shows honest process state and the
real prover-loop budget.

It translates validated run artifacts into a static HTML page. It never mutates
mathematical artifacts and never upgrades proof/source/evidence status: controls
are command hints, evidence wording goes through the shared display vocabulary
(`evidence_phrase` etc.), agent process status is honest (a running / failed /
skipped / not-run agent never counts as evidence), and the whole page passes
`scrub_proof_language`.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .agent_run_status import (
    AGENT_TITLES as _AGENT_TITLES,
    display_rows as _agent_display_rows,
    evidence_incomplete as _evidence_incomplete,
)
from .descriptions import evidence_phrase, objection_phrase
from .io import read_yaml
from .proof_language import scrub_proof_language
from .route_table import ROUTE_TABLE_COLUMNS, build_route_step_rows
from .subgoal_graph import (
    PROOF_SEARCH_NOTE as _PROOF_SEARCH_NOTE,
    graph_notes as _graph_notes,
    group_subgoals as _group_subgoals,
    state_words as _state_words,
)


DASHBOARD_FILENAME = "research_dashboard.html"

# Honest display words for per-instance example results. Never echoes the raw
# enum; an unknown value falls back to "not tested yet" (unproven, not support).
_EXAMPLE_RESULT_WORDS = {
    "survived": "behaved as claimed on this instance",
    "failed": "failed on this instance",
    "inconclusive": "inconclusive on this instance",
    "not_tested": "not tested yet",
}

# A public classical stream for the focus-music toggle (loads only on click).
_RADIO_STREAM = "https://stream.srg-ssr.ch/m/rsc_de/mp3_128"


def write_run_dashboard(run_dir: Path, *, filename: str = DASHBOARD_FILENAME) -> Path:
    """Write the dashboard into ``run_dir`` and return the path."""

    run_dir = Path(run_dir)
    out = run_dir / filename
    out.write_text(render_run_dashboard(run_dir), encoding="utf-8")
    return out


def render_run_dashboard(run_dir: Path) -> str:
    """Render the read-only workspace HTML from existing run artifacts."""

    run_dir = Path(run_dir)
    state = _load_run_state(run_dir)
    route_data = _route_data(state)
    selected_route = _selected_route_id(state, route_data)
    initial = route_data.get(selected_route) or next(iter(route_data.values()), _fallback_route())
    command = _command_for(run_dir, selected_route, initial["effort"], completed=state["completed"])
    review = _review_result(state)

    # Display-layer safety: redact any global proof-complete overclaim an echoed
    # free-text artifact field might carry into the rendered HTML. Never
    # upgrades status.
    return scrub_proof_language(
        _template(
            state=state,
            run_dir=run_dir,
            routes_preliminary=_evidence_incomplete(run_dir),
            route_data=route_data,
            initial=initial,
            command=command,
            review=review,
        )
    )


# --------------------------------------------------------------------------- #
# state loading
# --------------------------------------------------------------------------- #


def _load_run_state(run_dir: Path) -> dict[str, Any]:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest.get("artifacts", {})
    optional = manifest.get("optional_artifacts", {})

    def artifact(name: str) -> dict[str, Any]:
        path = artifacts.get(name)
        return read_yaml(run_dir / path) if path and (run_dir / path).exists() else {}

    def optional_artifact(name: str) -> dict[str, Any]:
        path = optional.get(name)
        return read_yaml(run_dir / path) if path and (run_dir / path).exists() else {}

    claim = artifact("locked_claim")
    grade = artifact("idea_grade")
    brief = artifact("strategy_brief")
    human_choice = artifact("human_choice")
    source_grounding = artifact("source_grounding")
    examples = artifact("examples")
    counterexamples = artifact("counterexamples")
    computations = artifact("computation_checks")
    literature = artifact("literature_review")
    loop = optional_artifact("prover_loop")
    graph = optional_artifact("proof_goal_graph")
    judgment = optional_artifact("mathematical_judgment")
    proof_verification = optional_artifact("proof_verification")
    failure_audit = optional_artifact("failure_pattern_audit")
    memory_context = optional_artifact("memory_context")
    llm_agent_run = optional_artifact("llm_agent_run")

    current_state = str(manifest.get("current_state", ""))
    latest_attempt = _latest_attempt(loop, human_choice.get("selected_strategy"))
    completed = current_state == "COMPLETED_MEMO"
    repair_needed = completed and (
        latest_attempt.get("status") == "needs_repair" or bool(latest_attempt.get("blockers"))
    )
    cited_steps = sum(
        1 for s in source_grounding.get("steps", []) if s.get("status") == "source_backed"
    )
    machine_checks = sum(
        1 for c in computations.get("checks", []) if c.get("status") == "checked"
    ) + sum(
        1
        for e in proof_verification.get("entries", [])
        if e.get("earned_status") == "instance_checked"
    )

    return {
        "run_dir": run_dir,
        "manifest": manifest,
        "statement": claim.get("normalized_statement") or claim.get("statement") or "Untitled claim",
        "claim": claim,
        "grade": grade,
        "brief": brief,
        "human_choice": human_choice,
        "source_grounding": source_grounding,
        "examples": examples,
        "counterexamples": counterexamples,
        "computations": computations,
        "literature": literature,
        "proof_verification": proof_verification,
        "loop": loop,
        "graph": graph,
        "latest_attempt": latest_attempt,
        "judgment": judgment,
        "failure_audit": failure_audit,
        "memory_context": memory_context,
        "llm_agent_run": llm_agent_run,
        "current_state": current_state,
        "completed": completed,
        "repair_needed": repair_needed,
        "cited_steps": cited_steps,
        "machine_checks": machine_checks,
        "state_label": _state_label(current_state, repair_needed),
        "proof_label": _proof_label(grade, repair_needed),
    }


def _route_data(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    routes = state["brief"].get("top_strategies") or []
    if not routes:
        return {"none": _fallback_route()}
    route_data: dict[str, dict[str, Any]] = {}
    for index, route in enumerate(routes, start=1):
        route_id = str(route.get("id") or f"route_{index}")
        title = str(route.get("title") or f"Route {index}")
        effort = str(route.get("estimated_effort") or "quick_test")
        why = str(route.get("why_this_route") or "")
        risk = str(route.get("main_risk") or "")
        next_plan = str(route.get("next_loop_plan") or "")
        details = [
            item
            for item in (
                f"Original route: {title}",
                why,
                risk and f"Risk: {risk}",
                next_plan and f"Next: {next_plan}",
            )
            if item
        ]
        route_data[route_id] = {
            "id": route_id,
            "label": _short_label(title, index),
            "title": _display_title(title, index),
            "effort": effort,
            "score": route.get("score"),
            "risk": _one_sentence(risk),
            "summary": _one_sentence(why) or _one_sentence(title) or title,
            "details": details or ["No detailed route notes recorded."],
        }
    return route_data


def _selected_route_id(state: dict[str, Any], routes: dict[str, dict[str, Any]]) -> str:
    selected = state["human_choice"].get("selected_strategy")
    if selected in routes:
        return str(selected)
    loop_selected = state["loop"].get("selected_strategy_id")
    if loop_selected in routes:
        return str(loop_selected)
    return next(iter(routes))


def _latest_attempt(loop: dict[str, Any], selected: str | None) -> dict[str, Any]:
    attempts = loop.get("attempts") or []
    if not attempts:
        return {}
    matching = [a for a in attempts if not selected or a.get("strategy_id") == selected]
    candidates = matching or attempts
    return sorted(candidates, key=lambda a: int(a.get("round_index") or 0))[-1]


def _review_result(state: dict[str, Any]) -> dict[str, Any]:
    latest = state["latest_attempt"]
    blockers = [str(item) for item in latest.get("blockers") or []]
    findings = state["judgment"].get("findings") or []
    failures = state["failure_audit"].get("matches") or []
    chips = []
    if findings:
        chips.append("needs source locator")
        chips.append("hypotheses not matched")
    for match in failures[:5]:
        label = str(match.get("pattern_id") or match.get("family") or "failure pattern")
        chips.append(label.replace("_", " "))
    seen: set[str] = set()
    chips = [chip for chip in chips if not (chip in seen or seen.add(chip))]
    return {
        "show": state["completed"] or bool(findings) or bool(blockers),
        "title": "Repair needed" if state["repair_needed"] else "Review result",
        "main": _one_sentence(blockers[0]) if blockers else "No blocking reviewer repair recorded.",
        "side": "The objections attack proof support. They do not disprove the claim.",
        "chips": chips,
    }


def _counterexample_pressure(state: dict[str, Any], agent_rows: list[dict]) -> str:
    """One honest line about counterexample pressure for the route cards."""

    tests = state["counterexamples"].get("tests") or []
    refuting = any(
        t.get("refutes_claim") and t.get("satisfies_all_claim_hypotheses") is True
        for t in tests
    )
    if refuting:
        return "A checked counterexample refutes the claim"
    status = next((r["status"] for r in agent_rows if r["agent"] == "counterexamples"), None)
    if status == "done":
        return "No admissible counterexample recorded so far (absence is not proof)"
    if status in ("not_run", "queued", "running", "failed", "skipped"):
        return "Counterexample search not finished"
    # legacy run without a board
    return (
        "No admissible counterexample recorded so far (absence is not proof)"
        if tests
        else "No counterexample tests recorded yet"
    )


# --------------------------------------------------------------------------- #
# labels
# --------------------------------------------------------------------------- #


def _state_label(current_state: str, repair_needed: bool) -> str:
    if current_state == "WAITING_LLM_AGENT_HUMAN_GATE":
        return "Waiting at your decision"
    if current_state == "COMPLETED_MEMO" and repair_needed:
        return "Reviewed"
    if current_state == "COMPLETED_MEMO":
        return "Memo ready"
    return current_state.replace("_", " ").title() or "Unknown"


def _proof_label(grade: dict[str, Any], repair_needed: bool) -> str:
    if repair_needed:
        return "Needs repair"
    proof_status = str(grade.get("proof_language_status") or "")
    if proof_status in {"computation_checked", "lean_checked", "human_confirmed"}:
        return "Checked"
    if "sketch" in proof_status:
        return "Sketched"
    return "Unknown"


def _one_sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    for marker in (". ", "; "):
        if marker in text:
            return text.split(marker, 1)[0].strip() + "."
    return text


def _short_label(title: str, index: int) -> str:
    lowered = title.lower()
    if "center" in lowered:
        return "Center"
    if "conjugation" in lowered:
        return "Conjugation"
    if "classification" in lowered or "generator" in lowered:
        return "Generators"
    return f"Route {index}"


def _display_title(title: str, index: int) -> str:
    label = _short_label(title, index)
    if label == "Center":
        return "Center route"
    if label == "Conjugation":
        return "Conjugation action"
    if label == "Generators":
        return "Generators"
    # No keyword match: keep the route's real title (truncated) instead of an
    # anonymous "Route N", so non-demo domains keep meaningful card titles.
    title = " ".join(str(title or "").split())
    if title:
        return title if len(title) <= 48 else title[:45].rstrip() + "…"
    return label


def _fallback_route() -> dict[str, Any]:
    return {
        "id": "none",
        "label": "None",
        "title": "No route selected",
        "effort": "quick_test",
        "score": None,
        "risk": "",
        "summary": "Ask for a sharper claim before proof search.",
        "details": ["No strategy route is currently available."],
    }


def _command_for(run_dir: Path, route_id: str, effort: str, *, completed: bool) -> str:
    scout = " \\\n  --literature-scout" if completed else ""
    return (
        "PYTHONPATH=$PWD .venv/bin/espresso orchestrate \\\n"
        f"  --resume {run_dir} \\\n"
        f"  --select-strategy {route_id} \\\n"
        f"  --effort {effort}"
        f"{scout}"
    )


def _h(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _js(obj: object) -> str:
    """JSON-serialize for embedding inside an inline ``<script>`` block.

    ``json.dumps`` alone does not escape ``<``/``>``/``&`` or the JS line
    separators, so artifact free text containing ``</script>`` would terminate
    the script element and inject live HTML (stored XSS). Escaping to ``\\uXXXX``
    keeps the JSON semantically identical while making tag breakout impossible.
    """

    return (
        json.dumps(obj, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _locator_chip(locator: dict[str, Any]) -> str:
    """A locator chip; clickable only for safe http(s)/arXiv targets."""

    kind = str(locator.get("kind") or "")
    value = str(locator.get("value") or "").strip()
    if not value or kind == "unknown":
        return '<span class="loc">needs a locator</span>'
    if kind == "arxiv_id":
        href = f"https://arxiv.org/abs/{value}"
        return f'<a class="loc" href="{_h(href)}" target="_blank" rel="noopener">&#8599; arXiv:{_h(value)}</a>'
    if kind == "url" and value.startswith(("http://", "https://")):
        return f'<a class="loc" href="{_h(value)}" target="_blank" rel="noopener">&#8599; open source</a>'
    return f'<span class="loc">&#128214; {_h(kind)}: {_h(value)}</span>'


# --------------------------------------------------------------------------- #
# sections
# --------------------------------------------------------------------------- #


def _claim_section(state: dict[str, Any]) -> str:
    claim = state["claim"]
    locked = "locked" if claim.get("locked") else "unlocked"
    chips: list[str] = []
    for i, hyp in enumerate((claim.get("hypotheses") or [])[:6], start=1):
        name = hyp.get("name") if isinstance(hyp, dict) else str(hyp)
        chips.append(f'<span class="hyp"><b>H{i}</b>&nbsp; {_h(name)}</span>')
    extra = len(claim.get("hypotheses") or []) - 6
    if extra > 0:
        chips.append(f'<span class="hyp">+{extra} more</span>')
    if claim.get("conclusion"):
        chips.append(f'<span class="hyp concl"><b>&#10233;</b>&nbsp; {_h(claim["conclusion"])}</span>')
    chip_html = f'<div class="hyp-row">{"".join(chips)}</div>' if chips else ""
    return f"""<section class="card claim"><div class="pad">
  <h2>Claim &middot; {_h(locked)}</h2>
  <h1>{_h(state["statement"])}</h1>
  {chip_html}
</div></section>"""


def _routes_section(
    state: dict[str, Any],
    route_data: dict[str, dict[str, Any]],
    initial: dict[str, Any],
    *,
    routes_preliminary: bool,
    cx_pressure: str,
) -> str:
    prelim = (
        '<span class="prelim-tag">preliminary &mdash; evidence agents incomplete</span>'
        if routes_preliminary
        else ""
    )
    cards: list[str] = []
    for route in route_data.values():
        selected = route["id"] == initial["id"]
        score = route.get("score")
        score_html = ""
        if isinstance(score, (int, float)):
            width = max(0, min(100, int(score)))
            score_html = (
                f'<i class="score">score {int(score)} / 100 '
                f'<span class="bar"><i style="width:{width}%"></i></span></i>'
            )
        risk_html = f"<i>Risk: {_h(route['risk'])}</i>" if route.get("risk") else ""
        cards.append(
            f"""<button class="route" data-route="{_h(route['id'])}" data-effort="{_h(route['effort'])}" aria-pressed="{str(selected).lower()}">
  <small>{"Selected" if selected else "Alternative"}</small>
  <b>{_h(route['title'])}</b>
  <span class="idea">{_h(route['summary'])}</span>
  <span class="meta">{score_html}{risk_html}<i>{_h(cx_pressure)}</i></span>
</button>"""
        )
    return f"""<section class="card"><div class="pad">
  <h2>Your Call &mdash; Choose Route{prelim}</h2>
  <p class="sub">Your choice steers ranking and time allocation. It never upgrades evidence or proof status.</p>
  <div class="routes">{"".join(cards)}</div>
</div></section>"""


def _plan_section(
    state: dict[str, Any],
    initial: dict[str, Any],
    command: str,
    *,
    routes_preliminary: bool,
) -> str:
    completed = state["completed"]
    selected_items = "".join(f"<li>{_h(item)}</li>" for item in initial["details"])
    cited = state["cited_steps"]
    checks = state["machine_checks"]
    if cited or checks:
        evidence_line = (
            f"Run evidence so far: {cited} reference-supported step(s) &middot; "
            f"{checks} machine check(s) &mdash; local evidence, not a proof of the claim."
        )
    else:
        evidence_line = "Run evidence so far: none earned yet."
    gaps = state["grade"].get("source_gaps") or []
    gaps_html = ""
    if gaps:
        # Display-layer translation: artifact free text may carry internal enum
        # tokens; the product surface never shows them raw.
        shown = [str(g).replace("source_backed", "reference-supported") for g in gaps[:5]]
        gap_items = "".join(f"<li>{_h(g)}</li>" for g in shown)
        gaps_html = f'<div class="gaps"><b>Source gaps</b><ul class="detail-list">{gap_items}</ul></div>'
    continue_note = (
        '<p class="your-call">You can continue this route now, before the remaining '
        "evidence agents finish &mdash; the memo will clearly mark what is still missing.</p>"
        if routes_preliminary
        else ""
    )
    note = (
        "The review found repair work, not a counterexample. No proof status was upgraded."
        if completed
        else "This continues the search. It still does not claim the theorem is checked."
    )
    heading_extra = "Repair Next" if completed else "Continue"
    lead = (
        "Next useful work: pin exact sources or replace cited lemmas with a local derivation."
        if completed
        else "Run this to continue with the selected route:"
    )
    return f"""<section class="card plan"><div class="pad">
  <h2>Selected Plan &middot; {_h(heading_extra)}</h2>
  <div class="head">
    <div>
      <strong id="selected-title">{_h(initial['title'])}</strong>
      <p class="sub" id="selected-summary" style="margin-bottom:0">{_h(initial['summary'])}</p>
    </div>
    <div class="effort" id="selected-effort">{_h(initial['effort'])}</div>
  </div>
  <ul class="detail-list" id="selected-detail">{selected_items}</ul>
  <p class="sub" style="margin:12px 0 0">{evidence_line}</p>
  {gaps_html}
  <p class="sub" style="margin:12px 0 0">{_h(lead)}</p>
  <pre class="cmd" id="command">{_h(command)}</pre>
  <div class="actions">
    <button class="copy" id="copy">Copy command</button>
    <span class="copy-status" id="copy-status"></span>
  </div>
  {continue_note}
  <div class="note">{_h(note)}</div>
</div></section>"""


def _route_actually_selected(state: dict[str, Any]) -> bool:
    """True only when the human (or the loop) actually selected a strategy — not
    when the picker merely defaults to highlighting the first route. Used to drop
    the graph's stale 'choose a route first' note without hiding it at the gate."""

    # Only the human gate counts as a real selection. The prover loop records a
    # ``selected_strategy_id`` even at the gate (before any human choice), so it
    # must NOT be treated as the human having chosen a route.
    selected = state["human_choice"].get("selected_strategy")
    return bool(selected and selected != "none")


def _proof_state_section(state: dict[str, Any], *, route_shown: bool = False) -> str:
    graph = state.get("graph") or {}
    nodes = graph.get("nodes") or []
    if not nodes:
        return ""
    grouped = _group_subgoals(graph)
    rows: list[str] = []

    def add_row(label: str, nodes_for_row: list[dict[str, Any]]) -> None:
        if not nodes_for_row:
            return
        items = "".join(
            f"<li>{_h(node.get('statement', ''))}"
            f"<span class=\"sg-state\">{_h(_state_words(node.get('state', '')))}</span></li>"
            for node in nodes_for_row
        )
        rows.append(f"<div class=\"sg-group\"><b>{_h(label)}</b><ul class=\"sg-list\">{items}</ul></div>")

    add_row("Open subgoals", grouped["open"])
    add_row("Only sketched", grouped["sketched"])
    add_row("Blocked subgoals", grouped["blocked"])
    add_row("Supported by reference (not a proof of the claim)", grouped["reference"])
    add_row("Checked only in a finite/computational scope", grouped["scoped"])

    main = grouped["main"]
    main_html = ""
    if main is not None:
        main_html = (
            f"<p class=\"sg-main\"><span class=\"muted\">Main goal</span> "
            f"{_h(main.get('statement', ''))} "
            f"<span class=\"sg-state\">{_h(_state_words(main.get('state', '')))}</span></p>"
        )
    next_node = grouped["next"]
    next_html = ""
    if next_node is not None:
        next_html = (
            f"<div class=\"sg-next\"><span class=\"muted\">Next subgoal to work on "
            f"[{_h(next_node.get('id', 'subgoal'))}]</span> "
            f"{_h(next_node.get('statement', ''))} "
            f"<span class=\"sg-state\">{_h(_state_words(next_node.get('state', '')))}</span></div>"
        )
    groups = "".join(rows) or "<p class=\"muted\">No open subgoals recorded.</p>"
    notes_html = ""
    notes = _graph_notes(graph)
    if route_shown:
        # The graph artifact may predate the route selection shown on this page;
        # drop its stale "choose a route first" guidance so the two surfaces do
        # not contradict each other (display filtering only — never a status).
        notes = [n for n in notes if "No strategy route is selected yet" not in str(n)]
    if notes:
        items = "".join(f"<li>{_h(n)}</li>" for n in notes)
        notes_html = (
            f"<div class=\"sg-notes\"><b>About this proof-search view</b>"
            f"<ul class=\"sg-list\">{items}</ul></div>"
        )
    return f"""<section class="card"><div class="pad">
  <h2>Proof State</h2>
  {main_html}
  <div class="sg-groups">{groups}</div>
  {next_html}
  {notes_html}
  <div class="note">{_h(_PROOF_SEARCH_NOTE)}</div>
</div></section>"""


def _route_table_section(state: dict[str, Any]) -> str:
    rows = build_route_step_rows(state.get("source_grounding"), state.get("proof_verification"))
    if not rows:
        return ""
    header = "".join(f"<th>{_h(col)}</th>" for col in ROUTE_TABLE_COLUMNS)
    body_rows = "".join(
        "<tr>"
        + "".join(
            f"<td>{_h(cell)}</td>"
            for cell in (
                row["step"],
                row["local_claim"],
                row["intended_support"],
                row["evidence_status"],
                row["locator_scope"],
                row["gaps"],
                row["next_check"],
            )
        )
        + "</tr>"
        for row in rows
    )
    note = (
        "Each row is LOCAL step evidence. A reference-supported or scoped-checked "
        "step is not a proof of the whole claim or route."
    )
    return f"""<section class="card"><div class="pad">
  <h2>Proof-Route Steps &mdash; evidence &amp; provenance</h2>
  <table class="route-table"><thead><tr>{header}</tr></thead><tbody>{body_rows}</tbody></table>
  <div class="note">{_h(note)}</div>
</div></section>"""


def _review_section(review: dict[str, Any]) -> str:
    chips = "".join(f'<span class="chip warn">{_h(chip)}</span>' for chip in review.get("chips", []))
    return f"""<section class="card"><div class="pad">
  <h2>Review Result</h2>
  <div class="review-grid">
    <div class="review-card"><b>Main blocker</b><p class="muted">{_h(review.get("main"))}</p></div>
    <div class="review-card"><b>Not a refutation</b><p class="muted">{_h(review.get("side"))}</p></div>
  </div>
  <div class="chips">{chips}</div>
</div></section>"""


# --------------------------------------------------------------------------- #
# evidence panel (right column)
# --------------------------------------------------------------------------- #

_AGENT_CLI_NAME = {
    "claim_understanding": "claim-understanding",
    "literature": "literature",
    "examples": "examples",
    "counterexamples": "counterexamples",
    "failure_patterns": "failure-patterns",
}


def _agent_commands(run_dir: Path, agent: str, status: str) -> str:
    if agent not in ("literature", "examples", "counterexamples", "failure_patterns"):
        return ""
    cli = _AGENT_CLI_NAME[agent]
    run_cmd = f"espresso run-agent --run {run_dir} --agent {cli}"
    skip_cmd = f"espresso run-agent --run {run_dir} --agent {cli} --skip"
    if status in ("running", "queued"):
        # Don't invite a duplicate launch while the agent is in flight.
        return f"""<div class="acmds">
  <span class="lab">Already {_h(status)} &mdash; Skip instead?</span>
  <code title="Click to copy">{_h(skip_cmd)}</code>
</div>"""
    verb = "Retry" if status in ("failed", "done") else "Run"
    return f"""<div class="acmds">
  <span class="lab">{_h(verb)}</span><code title="Click to copy">{_h(run_cmd)}</code>
  <span class="lab">Skip</span><code title="Click to copy">{_h(skip_cmd)}</code>
</div>"""


def _agent_status_note(row: dict) -> str:
    note = {
        "done": "Finished; results recorded (the kernel validated the artifact — not a proof).",
        "running": "In progress; not yet evidence.",
        "queued": "Queued to run.",
        "not_run": "Has not run; not counted as evidence.",
        "skipped": "Skipped by request; contributes no evidence.",
        "failed": "Failed and rolled back; previous valid run preserved. Needs check.",
    }.get(row["status"], "")
    if row["status"] == "failed" and row.get("error_summary"):
        note = f"Failed: {str(row['error_summary'])[:120]}"
    return note


def _literature_body(state: dict[str, Any], status: str, run_dir: Path) -> str:
    items = state["literature"].get("candidate_references") or []
    if not items:
        if status == "done":
            return '<p class="empty">No candidate references recorded.</p>' + _agent_commands(
                run_dir, "literature", status
            )
        return (
            '<p class="empty">No literature search has finished yet. Nothing here supports a step.</p>'
            + _agent_commands(run_dir, "literature", status)
        )
    head = ""
    if status != "done":
        head = (
            '<p class="side-note" style="padding:0">In progress &mdash; items below are '
            "<b>proposed, not checked</b>; none of them supports a step yet.</p>"
        )
    cards = []
    for item in items[:8]:
        authors = ", ".join(item.get("authors") or []) or "unknown author"
        phrase = evidence_phrase(item.get("status"))
        why = _h(item.get("why_relevant") or "")
        why_html = f'<div class="why">Why relevant: {why}</div>' if why else ""
        cards.append(
            f"""<div class="art-item">
  <div class="ti">{_h(item.get('title', 'untitled'))}</div>
  <div class="by">{_h(authors)} &middot; {_h(phrase)}</div>
  {why_html}
  {_locator_chip(item.get('locator') or {})}
</div>"""
        )
    more = len(items) - 8
    more_html = f'<p class="side-note">+{more} more in literature_review.yaml</p>' if more > 0 else ""
    return head + f'<div class="artifact">{"".join(cards)}{more_html}</div>' + _agent_commands(
        run_dir, "literature", status
    )


def _examples_body(state: dict[str, Any], status: str, run_dir: Path) -> str:
    items = state["examples"].get("examples") or []
    show_results = status in ("done", "no_board")
    if not items:
        empty = (
            "Search finished with no examples recorded. Not evidence either way."
            if status == "done"
            else "No examples have been tested yet. Not counted as evidence."
        )
        return f'<p class="empty">{empty}</p>' + _agent_commands(run_dir, "examples", status)
    checks_by_example: dict[str, list[dict]] = {}
    for check in state["computations"].get("checks") or []:
        checks_by_example.setdefault(str(check.get("example_id")), []).append(check)
    cards = []
    for ex in items[:8]:
        sat = ex.get("satisfies_all_claim_hypotheses")
        if sat is True:
            sat_note = "satisfies all hypotheses"
        elif sat is False:
            sat_note = "drops a hypothesis — <b>not</b> admissible against this claim"
        else:
            sat_note = "hypothesis match unclear"
        result_word = (
            _EXAMPLE_RESULT_WORDS.get(str(ex.get("result_for_current_idea")), "not tested yet")
            if show_results
            else "result pending — agent unfinished"
        )
        check_html = ""
        for check in checks_by_example.get(str(ex.get("id")), []):
            if check.get("status") != "checked":
                continue
            mc = check.get("machine_check") or {}
            expr = _h(f"{mc.get('expression', '')} = {mc.get('expected', '')}")
            check_html += (
                f'<div class="why"><span class="ev checked">{_h(evidence_phrase("computation_checked"))}'
                f"</span> &nbsp;{expr} (this instance only)</div>"
            )
        cards.append(
            f"""<div class="art-item">
  <div class="ti">{_h(ex.get('name') or ex.get('id', 'example'))}</div>
  <div class="by">{sat_note} &middot; {_h(result_word)}</div>
  {check_html}
</div>"""
        )
    footer = (
        '<p class="side-note" style="padding:0">Tested on examples &mdash; examples do not '
        "prove the universal claim.</p>"
    )
    return footer + f'<div class="artifact">{"".join(cards)}</div>' + _agent_commands(
        run_dir, "examples", status
    )


def _counterexamples_body(state: dict[str, Any], status: str, run_dir: Path) -> str:
    items = state["counterexamples"].get("tests") or []
    if status not in ("done", "no_board"):
        return (
            '<p class="empty">No counterexample search has happened yet. The absence of '
            "counterexamples here is <b>not</b> evidence for the claim.</p>"
            + _agent_commands(run_dir, "counterexamples", status)
        )
    if not items:
        return (
            '<p class="empty">Search finished with no recorded counterexample tests. '
            "Absence of counterexamples is not a proof.</p>"
            + _agent_commands(run_dir, "counterexamples", status)
        )
    cards = []
    for test in items[:8]:
        # Mirror the kernel's admissibility gate (validators._has_admissible_
        # counterexample): a refutation may be DISPLAYED only when the test
        # refutes the claim AND satisfies every hypothesis (`is True`). An
        # "unclear" hypothesis match must never read as an admissible refutation
        # — the display can never be looser than the kernel.
        sat = test.get("satisfies_all_claim_hypotheses")
        scope_bits = []
        if test.get("refutes_claim") and sat is True:
            scope_bits.append("refutes the claim (admissible checked counterexample)")
        else:
            if sat is False:
                scope_bits.append("drops a hypothesis — cannot refute the original claim")
            elif sat is not True:
                scope_bits.append("hypothesis match unclear — cannot refute the original claim")
            if test.get("refutes_step_only"):
                scope_bits.append("attacks a proof step — not a claim refutation")
            if not scope_bits:
                scope_bits.append("does not refute the claim")
        missing = test.get("missing_hypothesis")
        missing_html = f'<div class="why">Missing hypothesis: {_h(missing)}</div>' if missing else ""
        cards.append(
            f"""<div class="art-item">
  <div class="ti">{_h(_one_sentence(test.get('explanation') or test.get('id', 'test')))}</div>
  <div class="by">{_h("; ".join(scope_bits))}</div>
  {missing_html}
</div>"""
        )
    footer = (
        '<p class="side-note" style="padding:0">A counterexample refutes the claim only when '
        "it satisfies every hypothesis and was checked to fail.</p>"
    )
    return footer + f'<div class="artifact">{"".join(cards)}</div>' + _agent_commands(
        run_dir, "counterexamples", status
    )


def _failure_patterns_body(state: dict[str, Any], status: str, run_dir: Path) -> str:
    matches = state["failure_audit"].get("matches") or []
    if not matches:
        empty = (
            "Audit finished; no catalogued failure pattern matched."
            if status == "done"
            else "The failure-pattern audit has not run. Not counted as evidence."
        )
        return f'<p class="empty">{empty}</p>' + _agent_commands(run_dir, "failure_patterns", status)
    cards = []
    for m in matches[:6]:
        cards.append(
            f"""<div class="art-item">
  <div class="ti">{_h(str(m.get('pattern_id') or 'pattern').replace('_', ' '))}</div>
  <div class="by">{_h(objection_phrase(m.get('tier')))}</div>
  <div class="why">{_h(_one_sentence(m.get('explanation') or ''))}</div>
</div>"""
        )
    footer = (
        '<p class="side-note" style="padding:0">Advisory only &mdash; a pattern match is a '
        "warning, never a refutation or a proof.</p>"
    )
    return footer + f'<div class="artifact">{"".join(cards)}</div>' + _agent_commands(
        run_dir, "failure_patterns", status
    )


def _claim_understanding_body(state: dict[str, Any]) -> str:
    claim = state["claim"]
    n_hyp = len(claim.get("hypotheses") or [])
    locked = "locked" if claim.get("locked") else "unlocked"
    ambiguous = claim.get("ambiguous_terms") or []
    amb = (
        f' &middot; ambiguous terms: {_h(", ".join(map(str, ambiguous[:4])))}'
        if ambiguous
        else " &middot; no ambiguous terms"
    )
    return f"""<div class="artifact"><div class="art-item">
  <div class="ti">Normalized: {_h(state['statement'])}</div>
  <div class="by">{_h(locked)} &middot; {n_hyp} hypothesis(es){amb}</div>
</div></div>"""


def _agent_summary_line(agent: str, state: dict[str, Any]) -> str:
    if agent == "claim_understanding":
        return "locked claim &middot; hypothesis ledger"
    if agent == "literature":
        n = len(state["literature"].get("candidate_references") or [])
        return f"{n} candidate reference(s)" if n else "no candidates yet"
    if agent == "examples":
        n = len(state["examples"].get("examples") or [])
        return f"{n} example(s)" if n else "no examples yet"
    if agent == "counterexamples":
        n = len(state["counterexamples"].get("tests") or [])
        return f"{n} test(s)" if n else "no tests yet"
    if agent == "failure_patterns":
        n = len(state["failure_audit"].get("matches") or [])
        return f"{n} advisory match(es)" if n else "no matches"
    return ""


def _evidence_panel(run_dir: Path, state: dict[str, Any]) -> str:
    rows = _agent_display_rows(run_dir)
    if not rows:
        # Legacy run without a board: render artifacts read-only with honest
        # labels but no per-agent process status.
        rows = [
            {
                "agent": a,
                "title": _AGENT_TITLES[a],
                "status": "no_board",
                "status_word": "",
                "attempts": 0,
                "error_summary": None,
            }
            for a in (
                "claim_understanding",
                "literature",
                "examples",
                "counterexamples",
                "failure_patterns",
            )
        ]
    body_for = {
        "claim_understanding": lambda st: _claim_understanding_body(state),
        "literature": lambda st: _literature_body(state, st, run_dir),
        "examples": lambda st: _examples_body(state, st, run_dir),
        "counterexamples": lambda st: _counterexamples_body(state, st, run_dir),
        "failure_patterns": lambda st: _failure_patterns_body(state, st, run_dir),
    }
    cards = []
    for row in rows:
        agent = row["agent"]
        status = row["status"]
        dot = {"done": "done", "running": "running", "failed": "failed", "skipped": "skipped"}.get(
            status, "notrun"
        )
        word_html = (
            f'<span class="aword {dot}">{_h(row.get("status_word") or "")}</span>'
            if row.get("status_word")
            else ""
        )
        attempts = row.get("attempts") or 0
        attempts_html = f'<span class="aruns">{int(attempts)} run(s)</span>' if attempts > 1 else ""
        note = _agent_status_note(row) if status != "no_board" else ""
        note_html = f'<p class="side-note" style="padding:0">{_h(note)}</p>' if note else ""
        open_attr = " open" if agent in ("claim_understanding", "literature") else ""
        cards.append(
            f"""<details class="agent"{open_attr}>
  <summary><span class="adot {dot}"></span>
    <span class="t"><b>{_h(row['title'])}</b><span>{_agent_summary_line(agent, state)}</span></span>
    {word_html}{attempts_html}<span class="caret">&#9654;</span></summary>
  <div class="abody">{note_html}{body_for[agent](status)}</div>
</details>"""
        )
    budget = _budget_card(state)
    return f"""<h2 style="padding:0 6px">Evidence Agents</h2>
<p class="side-note" style="margin:-4px 0 2px">Agents run on demand, in any order. Only a <b>Done</b> agent&rsquo;s artifact counts as evidence &mdash; and the kernel still validates it (validation is not a proof). Read anything below while the rest are running.</p>
{"".join(cards)}
{budget}"""


def _budget_card(state: dict[str, Any]) -> str:
    budget = (state["loop"] or {}).get("budget") or {}
    rounds = (
        f"{budget.get('rounds_used', 0)} / {budget.get('max_rounds', '—')}" if budget else "—"
    )
    calls = (
        f"{budget.get('agent_calls_used', 0)} / {budget.get('max_agent_calls', '—')}"
        if budget
        else "—"
    )
    return f"""<div class="card budget-card"><div class="pad" style="padding:15px 18px">
  <h2>Budget</h2>
  <div class="row"><span class="muted">Prover rounds</span><b>{_h(rounds)}</b></div>
  <div class="row"><span class="muted">Agent calls</span><b>{_h(calls)}</b></div>
  <div class="row"><span class="muted">Model tokens</span><b class="muted" style="font-weight:550">not tracked yet</b></div>
  <p class="side-note" style="padding:6px 0 0;margin:0">Budgets bound the search. Spending more never upgrades evidence.</p>
</div></div>"""


# --------------------------------------------------------------------------- #
# template
# --------------------------------------------------------------------------- #


def _template(
    *,
    state: dict[str, Any],
    run_dir: Path,
    routes_preliminary: bool,
    route_data: dict[str, dict[str, Any]],
    initial: dict[str, Any],
    command: str,
    review: dict[str, Any],
) -> str:
    agent_rows = _agent_display_rows(run_dir)
    cx_pressure = _counterexample_pressure(state, agent_rows)
    budget = (state["loop"] or {}).get("budget") or {}
    budget_mini = ""
    if budget:
        used = int(budget.get("agent_calls_used") or 0)
        cap = max(1, int(budget.get("max_agent_calls") or 1))
        pct = max(0, min(100, round(100 * used / cap)))
        budget_mini = f"""<div class="budget">
    <div class="meter"><i style="width:{pct}%"></i></div>
    <small>Agent calls {used} / {cap} &middot; round {budget.get('rounds_used', 0)} / {budget.get('max_rounds', '—')} &middot; token usage not tracked yet</small>
  </div>"""
    review_html = _review_section(review) if review.get("show") else ""
    route_json = _js(route_data)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Espresso Proof</title>
<style>{_STYLE}</style>
</head>
<body>

<header class="topbar">
  <div class="brand"><span class="bean"></span>Espresso Proof</div>
  <div class="claim-mini">{_h(state["statement"])}</div>
  <span class="chip state"><span class="dot"></span>{_h(state["state_label"])}</span>
  <span class="chip">Proof status: {_h(state["proof_label"])}</span>
  {budget_mini}
  <button class="music" id="music" type="button">
    <span class="note">&#9834;</span><span class="bars"><i></i><i></i><i></i></span> Classical
  </button>
  <audio id="radio" preload="none" src="{_h(_RADIO_STREAM)}"></audio>
</header>

<div class="wrap">
<main class="maincol">
  {_claim_section(state)}
  {_routes_section(state, route_data, initial, routes_preliminary=routes_preliminary, cx_pressure=cx_pressure)}
  {_plan_section(state, initial, command, routes_preliminary=routes_preliminary)}
  {_proof_state_section(state, route_shown=_route_actually_selected(state))}
  {_route_table_section(state)}
  {review_html}
</main>
<aside class="sidecol">
  {_evidence_panel(run_dir, state)}
</aside>
</div>

<script>
const runPath = {_js(str(run_dir))};
const completed = {_js(state["completed"])};
const routeData = {route_json};

function commandFor(routeId) {{
  const route = routeData[routeId];
  const scout = completed ? " \\\\\\n  --literature-scout" : "";
  return `PYTHONPATH=$PWD .venv/bin/espresso orchestrate \\
  --resume ${{runPath}} \\
  --select-strategy ${{routeId}} \\
  --effort ${{route.effort}}${{scout}}`;
}}

function selectRoute(routeId) {{
  const route = routeData[routeId];
  document.querySelectorAll(".route").forEach(button => {{
    button.setAttribute("aria-pressed", String(button.dataset.route === routeId));
  }});
  document.getElementById("selected-title").textContent = route.title;
  document.getElementById("selected-summary").textContent = route.summary;
  document.getElementById("selected-detail").replaceChildren(
    ...route.details.map(text => {{
      const item = document.createElement("li");
      item.textContent = text;
      return item;
    }})
  );
  document.getElementById("selected-effort").textContent = route.effort;
  document.getElementById("command").textContent = commandFor(routeId);
  document.getElementById("copy-status").textContent = "";
}}

document.querySelectorAll(".route").forEach(button => {{
  button.addEventListener("click", () => selectRoute(button.dataset.route));
}});

selectRoute({_js(initial["id"])});

document.getElementById("copy").addEventListener("click", async () => {{
  const text = document.getElementById("command").textContent;
  try {{
    await navigator.clipboard.writeText(text);
    document.getElementById("copy-status").textContent = "Copied";
  }} catch {{
    document.getElementById("copy-status").textContent = "Select and copy the command above";
  }}
}});

document.querySelectorAll(".acmds code").forEach(c => c.addEventListener("click", async () => {{
  try {{
    await navigator.clipboard.writeText(c.textContent);
    c.style.outline = "2px solid #b9d8c6";
    setTimeout(() => c.style.outline = "", 900);
  }} catch {{}}
}}));

const music = document.getElementById("music"), radio = document.getElementById("radio");
music.addEventListener("click", async () => {{
  if (radio.paused) {{
    try {{ await radio.play(); music.classList.add("playing"); }} catch {{}}
  }} else {{
    radio.pause(); music.classList.remove("playing");
  }}
}});
</script>
</body>
</html>
"""


_STYLE = r"""
:root{
  color-scheme:light;
  --paper:#faf7f1; --card:#fffdf9; --ink:#211d16; --muted:#7a7367; --line:#e6dfd2;
  --esp:#6b4a2f; --esp-deep:#4d3521; --esp-soft:#f3ebe2; --esp-line:#d9c9b8;
  --green:#2e7d54; --green-soft:#e9f3ec; --green-line:#b9d8c6;
  --amber:#9a6b1d; --amber-soft:#faf0dc; --amber-line:#e9d3a8;
  --gray-soft:#f0ede7;
  --shadow:0 1px 2px rgba(43,33,21,.05),0 10px 30px rgba(43,33,21,.07);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--paper);color:var(--ink);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:var(--esp)}
code,pre,.mono{font-family:"SF Mono",SFMono-Regular,Consolas,monospace}
.topbar{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:16px;
  padding:10px 26px;background:rgba(250,247,241,.92);backdrop-filter:blur(8px);
  border-bottom:1px solid var(--line)}
.brand{display:flex;align-items:center;gap:9px;font-weight:700;white-space:nowrap}
.brand .bean{width:13px;height:13px;border-radius:50% 50% 50% 50%/60% 60% 40% 40%;
  background:radial-gradient(circle at 35% 30%,#9a6f4b,#5a3d24);box-shadow:inset 0 -2px 3px rgba(0,0,0,.25)}
.topbar .claim-mini{flex:1;min-width:0;color:var(--muted);font-family:Georgia,serif;font-style:italic;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip{display:inline-flex;align-items:center;gap:6px;padding:4px 11px;border-radius:999px;
  font-size:12.5px;font-weight:650;border:1px solid var(--line);background:var(--card);white-space:nowrap}
.chip .dot{width:7px;height:7px;border-radius:50%}
.chip.state{border-color:var(--esp-line);color:var(--esp)}
.chip.state .dot{background:var(--esp);animation:breathe 2.4s ease-in-out infinite}
@keyframes breathe{50%{opacity:.35}}
.budget{display:flex;flex-direction:column;gap:2px;min-width:150px}
.budget .meter{height:5px;border-radius:99px;background:var(--gray-soft);overflow:hidden}
.budget .meter i{display:block;height:100%;background:linear-gradient(90deg,#8a5d3b,var(--esp));border-radius:99px}
.budget small{color:var(--muted);font-size:11px;line-height:1.3}
.music{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:6px 13px;
  font:inherit;font-size:13px;cursor:pointer;display:inline-flex;align-items:center;gap:7px;color:var(--ink)}
.music:hover{border-color:var(--esp-line);background:var(--esp-soft)}
.music .bars{display:none;align-items:flex-end;gap:2px;height:11px}
.music.playing .bars{display:inline-flex}
.music.playing .note{display:none}
.music .bars i{width:3px;background:var(--esp);border-radius:1px;animation:eq .9s ease-in-out infinite}
.music .bars i:nth-child(2){animation-delay:.2s}.music .bars i:nth-child(3){animation-delay:.45s}
@keyframes eq{0%,100%{height:4px}50%{height:11px}}
.wrap{max-width:1280px;margin:0 auto;padding:26px 26px 90px;
  display:grid;grid-template-columns:minmax(0,1.65fr) minmax(320px,1fr);gap:22px;align-items:start}
.maincol{display:grid;gap:22px;min-width:0}
.sidecol{display:grid;gap:14px;position:sticky;top:64px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}
.card>.pad{padding:22px 26px}
h2{margin:0 0 4px;font-size:12.5px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);font-weight:700}
.sub{color:var(--muted);font-size:13px;margin:0 0 14px;max-width:680px}
.muted{color:var(--muted)}
p{margin:0}
.note{margin-top:14px;padding:11px 14px;border:1px solid var(--amber-line);border-radius:9px;
  background:var(--amber-soft);color:#5c4310;font-size:13px}
.claim h1{margin:6px 0 14px;font-family:Georgia,"Times New Roman",serif;font-weight:500;
  font-size:clamp(26px,3.2vw,36px);line-height:1.22}
.hyp-row{display:flex;flex-wrap:wrap;gap:8px}
.hyp{padding:5px 12px;border-radius:999px;background:var(--esp-soft);border:1px solid var(--esp-line);
  font-size:13px;color:#543a23;font-family:Georgia,serif}
.hyp b{font-weight:650;color:var(--esp)}
.hyp.concl{background:#fdfaf4;border-color:var(--esp);color:var(--esp-deep);font-weight:600}
.routes{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:14px}
.route{appearance:none;font:inherit;text-align:left;background:var(--card);color:var(--ink);
  border:1px solid var(--line);border-radius:12px;padding:16px 16px 14px;cursor:pointer;min-height:165px;
  display:flex;flex-direction:column;gap:8px;transition:transform .14s,border-color .14s,box-shadow .14s,background .14s}
.route:hover{transform:translateY(-2px);border-color:var(--esp-line);box-shadow:var(--shadow)}
.route[aria-pressed=true]{background:var(--esp-soft);border-color:var(--esp);box-shadow:0 0 0 3px rgba(107,74,47,.12)}
.route small{color:var(--esp);font-weight:750;text-transform:uppercase;letter-spacing:.07em;font-size:10.5px}
.route b{font-family:Georgia,serif;font-weight:600;font-size:16.5px}
.route .idea{font-size:13.5px;line-height:1.5}
.route .meta{margin-top:auto;display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--muted)}
.route .meta i{font-style:normal}
.score{display:inline-flex;align-items:center;gap:6px}
.score .bar{width:54px;height:4px;border-radius:99px;background:var(--gray-soft);overflow:hidden}
.score .bar i{display:block;height:100%;background:var(--esp);border-radius:99px}
.prelim-tag{display:inline-block;margin-left:8px;padding:2px 9px;border-radius:999px;background:var(--amber-soft);
  border:1px solid var(--amber-line);color:var(--amber);font-size:11px;font-weight:700;letter-spacing:.04em;
  vertical-align:2px;text-transform:none}
.plan{background:linear-gradient(180deg,#fbf5ec,var(--esp-soft));border-color:var(--esp-line)}
.plan .head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-top:6px}
.plan .head strong{font-family:Georgia,serif;font-size:21px;font-weight:600;display:block;margin-bottom:2px}
.effort{flex:0 0 auto;padding:6px 12px;border:1px solid var(--esp-line);border-radius:999px;color:var(--esp);
  background:rgba(255,255,255,.6);font-size:12.5px;font-weight:650}
.detail-list{margin:12px 0 0;padding:0;list-style:none;display:grid;gap:7px}
.detail-list li{padding-left:18px;position:relative;color:#4f4639;font-size:13.5px}
.detail-list li:before{content:"";position:absolute;left:2px;top:.65em;width:6px;height:6px;border-radius:50%;background:var(--esp)}
.gaps{margin-top:12px}.gaps b{font-size:13px}
pre.cmd{margin:10px 0 0;padding:14px 16px;background:#221a12;color:#f3ead9;border-radius:10px;
  font-size:12.5px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere;font-family:"SF Mono",SFMono-Regular,Consolas,monospace}
.actions{display:flex;gap:10px;align-items:center;margin-top:10px}
.copy{border:1px solid var(--esp-line);border-radius:8px;background:var(--card);color:var(--ink);
  padding:8px 13px;font:inherit;font-size:13px;cursor:pointer}
.copy:hover{border-color:var(--esp)}
.copy-status{color:var(--muted);font-size:13px}
.your-call{margin-top:12px;font-size:13px;color:#5b4a35;display:flex;gap:8px;align-items:flex-start}
.your-call:before{content:"\261E";font-size:15px;line-height:1.4}
.sg-main{margin:2px 0 14px;font-family:Georgia,serif;font-size:15.5px}
.sg-groups{display:grid;gap:13px}
.sg-group b{display:block;margin-bottom:6px;font-size:13px}
.sg-list{margin:0;padding:0;list-style:none;display:grid;gap:6px}
.sg-list li{padding-left:17px;position:relative;font-size:13.5px}
.sg-list li:before{content:"";position:absolute;left:2px;top:.66em;width:6px;height:6px;border-radius:50%;background:var(--esp)}
.sg-state{display:inline-block;margin-left:8px;padding:2px 9px;border-radius:999px;background:var(--gray-soft);
  color:var(--muted);font-size:11.5px}
.sg-next{margin-top:14px;padding:12px 15px;border:1px solid var(--esp-line);border-radius:10px;background:var(--esp-soft);font-size:13.5px}
.sg-notes{margin-top:14px;padding:12px 14px;border:1px solid var(--line);border-radius:8px;background:var(--gray-soft)}
.sg-notes b{display:block;margin-bottom:6px}
table.route-table{width:100%;border-collapse:collapse;font-size:12.8px;margin-top:10px}
.route-table th,.route-table td{border-bottom:1px solid var(--line);padding:9px 10px;text-align:left;vertical-align:top}
.route-table th{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:700;border-bottom:2px solid var(--line)}
.route-table td:first-child{font-family:Georgia,serif}
.ev{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;font-size:11.5px;font-weight:650;white-space:nowrap}
.ev.checked{background:var(--green-soft);border:1px solid var(--green-line);color:var(--green)}
.agent{border:1px solid var(--line);border-radius:12px;background:var(--card);box-shadow:var(--shadow);overflow:hidden}
.agent summary{list-style:none;cursor:pointer;padding:13px 16px;display:flex;align-items:center;gap:10px}
.agent summary::-webkit-details-marker{display:none}
.agent summary:hover{background:#fbf8f2}
.adot{width:9px;height:9px;border-radius:50%;flex:0 0 auto}
.adot.done{background:var(--green)}
.adot.running{background:var(--amber);animation:breathe 1.4s ease-in-out infinite}
.adot.notrun,.adot.skipped{background:#c9c2b6}
.adot.failed{background:#b3503e}
.agent .t{flex:1;min-width:0}
.agent .t b{display:block;font-size:14px}
.agent .t span{display:block;color:var(--muted);font-size:12px;margin-top:1px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.aword{font-size:11.5px;font-weight:700;padding:3px 10px;border-radius:999px;white-space:nowrap}
.aword.done{background:var(--green-soft);color:var(--green)}
.aword.running{background:var(--amber-soft);color:var(--amber)}
.aword.notrun,.aword.skipped{background:var(--gray-soft);color:var(--muted)}
.aword.failed{background:#f7e4df;color:#9c3f2d}
.aruns{color:var(--muted);font-size:11px;white-space:nowrap}
.caret{color:var(--muted);transition:transform .15s;font-size:11px}
.agent[open] .caret{transform:rotate(90deg)}
.abody{border-top:1px dashed var(--line);padding:13px 16px 15px;display:grid;gap:10px}
.artifact{display:grid;gap:9px}
.art-item{border:1px solid var(--line);border-radius:9px;padding:10px 12px;background:#fffefb}
.art-item .ti{font-family:Georgia,serif;font-size:13.5px;margin-bottom:2px}
.art-item .by{font-size:12px;color:var(--muted)}
.art-item .why{font-size:12.5px;margin-top:5px;color:#564c3d}
.loc{display:inline-flex;align-items:center;gap:5px;margin-top:6px;font-size:12px;
  padding:2px 9px;border-radius:999px;background:var(--gray-soft);color:var(--ink);text-decoration:none;border:1px solid var(--line)}
a.loc:hover{border-color:var(--esp-line);background:var(--esp-soft)}
.empty{font-size:12.8px;color:var(--muted);padding:8px 2px;font-style:italic;margin:0}
.acmds{display:grid;gap:5px;margin-top:4px}
.acmds .lab{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.acmds code{display:block;padding:7px 9px;background:#221a12;color:#f3ead9;border-radius:7px;
  font-size:11px;overflow-wrap:anywhere;cursor:pointer}
.acmds code:hover{outline:2px solid var(--esp-line)}
.side-note{font-size:12px;color:var(--muted);padding:0 6px;line-height:1.55}
.budget-card .row{display:flex;justify-content:space-between;font-size:13px;padding:4px 0}
.budget-card .row b{font-variant-numeric:tabular-nums}
.review-grid{display:grid;grid-template-columns:1fr 1fr;gap:13px;margin-top:12px}
.review-card{padding:14px 16px;border:1px solid var(--line);border-radius:10px;background:#fffefb}
.review-card b{display:block;margin-bottom:6px;font-size:13.5px}
.review-card p{font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:11px}
.chip.warn{background:var(--amber-soft);border-color:var(--amber-line);color:#65430f;font-weight:550}
@media (max-width:1020px){.wrap{grid-template-columns:1fr}.sidecol{position:static}}
@media (max-width:760px){
  .wrap{padding:16px 14px 60px}.card>.pad{padding:17px 16px}
  .routes,.review-grid{grid-template-columns:1fr}
  .topbar{flex-wrap:wrap;gap:9px;padding:9px 14px}.claim-mini{order:5;flex-basis:100%}
}
""".strip()


__all__ = ["DASHBOARD_FILENAME", "render_run_dashboard", "write_run_dashboard"]
