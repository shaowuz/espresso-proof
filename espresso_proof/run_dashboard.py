"""Minimal interactive run dashboard for Espresso Proof.

This is a read-only product surface for one run. It translates validated run
artifacts into a small HTML page: claim, status icons, route picker, selected
plan, review result, and next command. It never mutates mathematical artifacts
and never upgrades proof/source/evidence status.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

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


def write_run_dashboard(run_dir: Path, *, filename: str = DASHBOARD_FILENAME) -> Path:
    """Write the minimal interactive dashboard into ``run_dir`` and return it."""

    run_dir = Path(run_dir)
    out = run_dir / filename
    out.write_text(render_run_dashboard(run_dir), encoding="utf-8")
    return out


def render_run_dashboard(run_dir: Path) -> str:
    """Render a read-only HTML dashboard from existing run artifacts."""

    run_dir = Path(run_dir)
    state = _load_run_state(run_dir)
    route_data = _route_data(state)
    selected_route = _selected_route_id(state, route_data)
    initial = route_data.get(selected_route) or next(iter(route_data.values()), _fallback_route())
    command = _command_for(run_dir, selected_route, initial["effort"], completed=state["completed"])
    review = _review_result(state)

    # Display-layer safety: redact any global proof-complete overclaim that an
    # echoed free-text artifact field might carry into the rendered HTML. Typed
    # labels/CSS are unaffected (only global "the theorem is proved" language is
    # matched). Never upgrades status.
    return scrub_proof_language(
        _template(
            statement=state["statement"],
            state_label=state["state_label"],
            selected_label=initial["label"],
            proof_label=state["proof_label"],
            judgment_cards=_judgment_cards(state),
            proof_state_html=_proof_state_section(state),
            route_table_html=_route_table_section(state),
            routes=list(route_data.values()),
            initial=initial,
            route_json=json.dumps(route_data, ensure_ascii=False),
            run_path=str(run_dir),
            command=command,
            review=review,
            completed=state["completed"],
        )
    )


# Human wording, next-subgoal priority, and grouping are imported from
# ``subgoal_graph`` so the dashboard and the memo render the proof-search state
# from a single source of truth (no duplicated enum->wording maps).


def _proof_state_section(state: dict[str, Any]) -> str:
    graph = state.get("graph") or {}
    nodes = graph.get("nodes") or []
    if not nodes:
        return ""

    grouped = _group_subgoals(graph)
    main = grouped["main"]
    open_nodes = grouped["open"]
    blocked_nodes = grouped["blocked"]
    sketched_nodes = grouped["sketched"]
    reference_nodes = grouped["reference"]
    scoped_nodes = grouped["scoped"]
    next_node = grouped["next"]

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

    add_row("Open subgoals", open_nodes)
    add_row("Only sketched", sketched_nodes)
    add_row("Blocked subgoals", blocked_nodes)
    add_row("Supported by reference (not a proof of the claim)", reference_nodes)
    add_row("Checked only in a finite/computational scope", scoped_nodes)

    main_html = ""
    if main is not None:
        main_html = (
            f"<p class=\"sg-main\"><span class=\"muted\">Main goal</span> "
            f"{_h(main.get('statement', ''))} "
            f"<span class=\"sg-state\">{_h(_state_words(main.get('state', '')))}</span></p>"
        )
    next_html = ""
    if next_node is not None:
        next_html = (
            f"<p class=\"sg-next\"><span class=\"muted\">Next subgoal to work on "
            f"[{_h(next_node.get('id', 'subgoal'))}]</span> "
            f"{_h(next_node.get('statement', ''))} "
            f"<span class=\"sg-state\">{_h(_state_words(next_node.get('state', '')))}</span></p>"
        )
    groups = "".join(rows) or "<p class=\"muted\">No open subgoals recorded.</p>"
    # Display notes (how to read the list) — shown only when present; HTML-escaped.
    notes_html = ""
    notes = _graph_notes(graph)
    if notes:
        items = "".join(f"<li>{_h(n)}</li>" for n in notes)
        notes_html = (
            f"<div class=\"sg-notes\"><b>About this proof-search view</b>"
            f"<ul class=\"sg-list\">{items}</ul></div>"
        )
    note = _PROOF_SEARCH_NOTE
    return f"""<section>
  <h2>Proof State</h2>
  {main_html}
  <div class="sg-groups">{groups}</div>
  {next_html}
  {notes_html}
  <div class="note">{_h(note)}</div>
</section>"""


def _route_table_section(state: dict[str, Any]) -> str:
    """Goal 24: per-step route table. Local step evidence only -- a row never
    asserts the whole claim/route is proved. Built from validated artifacts."""
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
    return f"""<section>
  <h2>Proof-Route Steps</h2>
  <table class="route-table"><thead><tr>{header}</tr></thead><tbody>{body_rows}</tbody></table>
  <div class="note">{_h(note)}</div>
</section>"""


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
    source_backed = _count_status(source_grounding, "source_backed")

    return {
        "run_dir": run_dir,
        "manifest": manifest,
        "statement": claim.get("normalized_statement") or claim.get("statement") or "Untitled claim",
        "claim": claim,
        "grade": grade,
        "brief": brief,
        "human_choice": human_choice,
        "source_grounding": source_grounding,
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
        "source_backed": source_backed,
        "state_label": _state_label(current_state, repair_needed),
        "proof_label": _proof_label(grade, repair_needed),
    }


def _route_data(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    routes = state["brief"].get("top_strategies") or []
    if not routes:
        return {
            "none": {
                "id": "none",
                "label": "None",
                "title": "No route selected",
                "effort": "quick_test",
                "summary": "Ask for a sharper claim before proof search.",
                "details": ["No strategy route is currently available."],
            }
        }
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
    # Stable de-duplication.
    seen: set[str] = set()
    chips = [chip for chip in chips if not (chip in seen or seen.add(chip))]
    return {
        "show": state["completed"] or bool(findings) or bool(blockers),
        "title": "Repair needed" if state["repair_needed"] else "Review result",
        "main": _one_sentence(blockers[0]) if blockers else "No blocking reviewer repair recorded.",
        "side": "The objections attack proof support. They do not disprove the claim.",
        "chips": chips,
    }


def _judgment_cards(state: dict[str, Any]) -> list[dict[str, str]]:
    claim_clear = state["grade"].get("status") != "unclear" and bool(state["claim"].get("locked"))
    proof_language = str(state["grade"].get("proof_language_status") or "")
    source_backed = state["source_backed"]
    cards = [
        {
            "kind": "ok" if claim_clear else "unknown",
            "icon": "✓" if claim_clear else "?",
            "title": "Claim clear" if claim_clear else "Claim unclear",
            "subtitle": "Ready to study" if claim_clear else "Needs sharpening",
        },
        {
            "kind": "sketch",
            "icon": "∴",
            "title": "Proof sketched" if "sketch" in proof_language else "Candidate argument",
            "subtitle": "Not checked",
        },
        {
            "kind": "ok" if source_backed else "unknown",
            "icon": "✓" if source_backed else "?",
            "title": "Reference support" if source_backed else "References unknown",
            "subtitle": f"{source_backed} supported step(s)" if source_backed else "No supported step yet",
        },
    ]
    if state["completed"] or state["repair_needed"]:
        cards.append(
            {
                "kind": "repair" if state["repair_needed"] else "ok",
                "icon": "!" if state["repair_needed"] else "✓",
                "title": "Repair needed" if state["repair_needed"] else "Reviewed",
                "subtitle": "Reviewers objected" if state["repair_needed"] else "No blocker recorded",
            }
        )
    return cards


def _state_label(current_state: str, repair_needed: bool) -> str:
    if current_state == "WAITING_LLM_AGENT_HUMAN_GATE":
        return "Waiting"
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


def _count_status(blob: Any, status: str) -> int:
    if isinstance(blob, dict):
        return int(blob.get("status") == status) + sum(_count_status(v, status) for v in blob.values())
    if isinstance(blob, list):
        return sum(_count_status(v, status) for v in blob)
    return 0


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
    return label


def _fallback_route() -> dict[str, Any]:
    return {
        "id": "none",
        "label": "None",
        "title": "No route selected",
        "effort": "quick_test",
        "summary": "Ask for a sharper claim before proof search.",
        "details": ["No strategy route is currently available."],
    }


def _command_for(run_dir: Path, route_id: str, effort: str, *, completed: bool) -> str:
    if completed:
        return (
            "PYTHONPATH=$PWD .venv/bin/espresso orchestrate \\\n"
            f"  --resume {run_dir} \\\n"
            f"  --select-strategy {route_id} \\\n"
            f"  --effort {effort} \\\n"
            "  --literature-scout"
        )
    return (
        "PYTHONPATH=$PWD .venv/bin/espresso orchestrate \\\n"
        f"  --resume {run_dir} \\\n"
        f"  --select-strategy {route_id} \\\n"
        f"  --effort {effort}"
    )


def _template(
    *,
    statement: str,
    state_label: str,
    selected_label: str,
    proof_label: str,
    judgment_cards: list[dict[str, str]],
    proof_state_html: str,
    route_table_html: str,
    routes: list[dict[str, Any]],
    initial: dict[str, Any],
    route_json: str,
    run_path: str,
    command: str,
    review: dict[str, Any],
    completed: bool,
) -> str:
    route_buttons = "\n".join(_route_button(route, selected=route["id"] == initial["id"]) for route in routes)
    cards = "\n".join(_judgment_card(card) for card in judgment_cards)
    review_html = _review_section(review) if review.get("show") else ""
    selected_items = "".join(f"<li>{_h(item)}</li>" for item in initial["details"])
    note = (
        "The review found repair work, not a counterexample. No proof status was upgraded."
        if completed
        else "This continues the search. It still does not claim the theorem is checked."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Espresso Proof</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
  <div class="page">
    <header>
      <div class="label">Espresso Proof</div>
      <h1>{_h(statement)}</h1>
    </header>

    <section class="status">
      <div><span class="muted">State</span><strong>{_h(state_label)}</strong></div>
      <div><span class="muted">Selected route</span><strong id="status-route">{_h(selected_label)}</strong></div>
      <div><span class="muted">Proof status</span><strong>{_h(proof_label)}</strong></div>
    </section>

    <section>
      <h2>Current Judgment</h2>
      <div class="judgment-icons">{cards}</div>
    </section>

    {proof_state_html}

    {route_table_html}

    {review_html}

    <section>
      <h2>Choose Route</h2>
      <div class="route-grid">{route_buttons}</div>
    </section>

    <section class="selection">
      <h2>Selected Plan</h2>
      <div class="selection-row">
        <div>
          <strong id="selected-title">{_h(initial["title"])}</strong>
          <p id="selected-summary">{_h(initial["summary"])}</p>
          <ul class="detail-list" id="selected-detail">{selected_items}</ul>
        </div>
        <div class="effort" id="selected-effort">{_h(initial["effort"])}</div>
      </div>
    </section>

    <section>
      <h2>{"Repair Next" if completed else "Continue"}</h2>
      <p>{"Next useful work: pin exact sources or replace cited lemmas with a local derivation." if completed else "Run this to continue with the selected route:"}</p>
      <pre id="command">{_h(command)}</pre>
      <div class="actions">
        <button class="copy" id="copy">Copy command</button>
        <span class="copy-status" id="copy-status"></span>
      </div>
      <div class="note">{_h(note)}</div>
    </section>
  </div>
</main>

<script>
const runPath = {json.dumps(run_path)};
const completed = {json.dumps(completed)};
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
  document.getElementById("status-route").textContent = route.label;
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

selectRoute({json.dumps(initial["id"])});

document.getElementById("copy").addEventListener("click", async () => {{
  const text = document.getElementById("command").textContent;
  try {{
    await navigator.clipboard.writeText(text);
    document.getElementById("copy-status").textContent = "Copied";
  }} catch {{
    document.getElementById("copy-status").textContent = "Select and copy the command above";
  }}
}});
</script>
</body>
</html>
"""


def _route_button(route: dict[str, Any], *, selected: bool) -> str:
    return f"""<button class="route" data-route="{_h(route['id'])}" data-effort="{_h(route['effort'])}" aria-pressed="{str(selected).lower()}">
  <small>{"Recommended" if selected else "Alternative"}</small>
  <b>{_h(route["title"])}</b>
  <span class="one-line">{_h(route["summary"])}</span>
</button>"""


def _judgment_card(card: dict[str, str]) -> str:
    return f"""<div class="judgment {_h(card['kind'])}">
  <div class="judgment-icon">{_h(card['icon'])}</div>
  <b>{_h(card['title'])}</b>
  <span>{_h(card['subtitle'])}</span>
</div>"""


def _review_section(review: dict[str, Any]) -> str:
    chips = "".join(f'<span class="chip warn">{_h(chip)}</span>' for chip in review.get("chips", []))
    return f"""<section>
  <h2>Review Result</h2>
  <div class="review-grid">
    <div class="review-card"><b>Main blocker</b><p class="muted">{_h(review.get("main"))}</p></div>
    <div class="review-card"><b>Not a refutation</b><p class="muted">{_h(review.get("side"))}</p></div>
  </div>
  <div class="chips">{chips}</div>
</section>"""


def _h(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


_STYLE = r"""
:root{color-scheme:light;--bg:#f6f4ef;--paper:#fffdf8;--ink:#1d1d1b;--muted:#6f6b62;--line:#ded8cb;--soft:#f1ece2;--green:#1f6f55;--green-soft:#e7f0eb;--green-line:#a9cabb;--amber-soft:#fbefd9}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(180deg,#fbfaf6 0,var(--bg) 260px);color:var(--ink);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:860px;margin:0 auto;padding:58px 24px 84px}
.page{background:var(--paper);border:1px solid var(--line);border-radius:12px;box-shadow:0 18px 45px rgba(49,44,34,.08);overflow:hidden}
header{padding:34px 36px 30px;border-bottom:1px solid var(--line)}
.label{display:inline-flex;gap:8px;align-items:center;color:var(--green);font-size:13px;font-weight:650;letter-spacing:.04em;text-transform:uppercase}.label:before{content:"";width:8px;height:8px;border-radius:50%;background:var(--green)}
h1{margin:12px 0 0;font-family:Georgia,"Times New Roman",serif;font-size:clamp(32px,5vw,48px);line-height:1.08;font-weight:500;letter-spacing:0}
section{padding:28px 36px;border-bottom:1px solid var(--line)}section:last-child{border-bottom:0}
h2{margin:0 0 10px;font-size:14px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
p{margin:0;max-width:690px}p+p{margin-top:10px}.muted{color:var(--muted)}
.status{display:grid;grid-template-columns:repeat(3,1fr);padding:0}.status div{padding:20px 24px;border-right:1px solid var(--line)}.status div:last-child{border-right:0}.status strong{display:block;margin-top:3px;font-size:18px}
.judgment-icons{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.judgment{padding:16px;border:1px solid var(--line);border-radius:10px;background:white}.judgment-icon{display:inline-grid;place-items:center;width:34px;height:34px;margin-bottom:10px;border-radius:50%;font-weight:800}.judgment.ok .judgment-icon{background:var(--green-soft);color:var(--green)}.judgment.sketch .judgment-icon,.judgment.repair .judgment-icon{background:var(--amber-soft);color:#8b5a19}.judgment.unknown .judgment-icon{background:var(--soft);color:var(--muted)}.judgment b{display:block;margin-bottom:2px}.judgment span{color:var(--muted);font-size:14px}
.review-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.review-card{padding:16px;border:1px solid var(--line);border-radius:10px;background:white}.review-card b{display:block;margin-bottom:8px}.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}.chip{padding:6px 9px;border-radius:999px;background:var(--soft);color:var(--muted);font-size:13px}.chip.warn{background:var(--amber-soft);color:#65430f}
.route-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:14px}.route{appearance:none;width:100%;min-height:174px;text-align:left;padding:16px;background:white;color:var(--ink);border:1px solid var(--line);border-radius:10px;cursor:pointer;transition:transform .12s ease,border-color .12s ease,background .12s ease,box-shadow .12s ease}.route:hover{transform:translateY(-1px);border-color:var(--green-line);box-shadow:0 8px 20px rgba(49,44,34,.08)}.route[aria-pressed=true]{background:var(--green-soft);border-color:var(--green);box-shadow:0 0 0 3px rgba(31,111,85,.12)}.route small{display:inline-block;margin-bottom:10px;color:var(--green);font-weight:700;text-transform:uppercase;letter-spacing:.06em}.route b{display:block;margin-bottom:8px;font-size:17px}.route span{display:block;color:var(--muted);font-size:14px;line-height:1.45}.route .one-line{color:var(--ink);font-size:15px}
.selection{background:var(--green-soft)}.selection-row{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}.selection-row strong{display:block;font-size:21px;margin-bottom:4px}.effort{flex:0 0 auto;padding:7px 10px;border:1px solid var(--green-line);border-radius:999px;color:var(--green);background:rgba(255,255,255,.55);font-size:13px;font-weight:650}
.detail-list{margin:14px 0 0;padding:0;list-style:none;display:grid;gap:8px}.detail-list li{padding-left:18px;position:relative;color:var(--muted)}.detail-list li:before{content:"";position:absolute;left:2px;top:.72em;width:6px;height:6px;border-radius:50%;background:var(--green)}
pre{margin:14px 0 0;padding:16px;background:#171917;color:#eef5ec;border-radius:8px;overflow-wrap:anywhere;white-space:pre-wrap;font:13px/1.55 "SFMono-Regular",Consolas,monospace}.actions{display:flex;gap:10px;align-items:center;margin-top:12px}.copy{border:1px solid var(--line);border-radius:8px;background:white;color:var(--ink);padding:9px 12px;font:inherit;cursor:pointer}.copy:hover{border-color:var(--green)}.copy-status{color:var(--muted);font-size:14px}.note{margin-top:12px;padding:12px 14px;border:1px solid #ead7b7;border-radius:8px;background:var(--amber-soft);color:#593b12;font-size:14px}
.sg-main{margin-bottom:14px}.sg-groups{display:grid;gap:14px}.sg-group b{display:block;margin-bottom:6px}.sg-list{margin:0;padding:0;list-style:none;display:grid;gap:6px}.sg-list li{padding-left:18px;position:relative;color:var(--ink)}.sg-list li:before{content:"";position:absolute;left:2px;top:.72em;width:6px;height:6px;border-radius:50%;background:var(--green)}.sg-state{display:inline-block;margin-left:8px;padding:2px 8px;border-radius:999px;background:var(--soft);color:var(--muted);font-size:12px}.sg-next{margin-top:14px;padding:12px 14px;border:1px solid var(--green-line);border-radius:8px;background:var(--green-soft)}.sg-notes{margin-top:14px;padding:12px 14px;border:1px solid var(--line);border-radius:8px;background:var(--soft)}.sg-notes b{display:block;margin-bottom:6px}.route-table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}.route-table th,.route-table td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}.route-table th{background:var(--soft);color:var(--muted);font-weight:600}
@media (max-width:780px){main{padding:18px}header,section{padding:24px 20px}.status,.route-grid,.review-grid{grid-template-columns:1fr}.judgment-icons{grid-template-columns:1fr}.status div{border-right:0;border-bottom:1px solid var(--line)}.status div:last-child{border-bottom:0}.selection-row{display:block}.effort{display:inline-block;margin-top:12px}}
""".strip()


__all__ = ["DASHBOARD_FILENAME", "render_run_dashboard", "write_run_dashboard"]
