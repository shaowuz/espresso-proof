"""Goal 43A: typed, display-safe snapshot view model for the React cockpit.

``build_snapshot`` derives ONE bounded JSON view model per run from the
validated artifacts. This module is the thin-client boundary:

- every trust label is computed HERE, server-side, through the existing safe
  display vocabulary (`descriptions.py`) — the React app renders strings and
  tones, it never recomputes proof/source/evidence status;
- the ``tone`` field drives styling, and the "earned" tone (the only one a
  green visual state may bind to) is granted exclusively for evidence the
  kernel already earned (reference support, checked computation, trusted
  verifier scope) — never for candidate / running / skipped / failed /
  literature-metadata-only states;
- a missing or unreadable artifact yields an explicit ``missing`` panel flag
  plus an empty list — placeholders, never fabricated success (fail closed);
- nothing here writes anything; this is display only and earns no status.
"""

from __future__ import annotations

from pathlib import Path

from .descriptions import describe_grade, effort_phrase, evidence_phrase, objection_phrase
from .io import read_yaml
from .latest_round import load_latest_round
from .proof_language import scrub_proof_language

# The ONLY statuses whose evidence the kernel has actually earned. Everything
# else — candidate, unverified, not_found, running agents, literature
# relevance — must never map to the "earned" (green-capable) tone.
_EARNED_STATUSES = {
    "source_backed",
    "source_checked",
    "checked",
    "computation_checked",
    "instance_checked",
    "lean_checked",
}

# Statuses describing found problems / pressure: visually cautionary.
_WARN_STATUSES = {"misapplied", "wrong", "failed"}


def _tone(status: str | None) -> str:
    """Map an internal status to a display tone, fail-closed to "muted"."""

    s = str(status or "")
    if s in _EARNED_STATUSES:
        return "earned"
    if s in _WARN_STATUSES:
        return "warn"
    if s in {"human_attested", "human_confirmed", "human_needed"}:
        return "human"
    return "muted"


def _artifact(run_dir: Path, name: str) -> dict | None:
    """Read a manifest-declared artifact, or None when absent/unreadable."""

    try:
        manifest = read_yaml(run_dir / "run_manifest.yaml")
    except Exception:  # noqa: BLE001 — missing manifest -> everything missing
        return None
    rel = manifest.get("artifacts", {}).get(name) or manifest.get(
        "optional_artifacts", {}
    ).get(name)
    if not rel or not (run_dir / rel).exists():
        return None
    try:
        data = read_yaml(run_dir / rel)
    except Exception:  # noqa: BLE001 — unreadable artifact -> missing, never invented
        return None
    return data if isinstance(data, dict) else None


def _clip(text: object, limit: int = 280) -> str:
    return str(text or "").strip()[:limit]


def _clip_scrubbed(text: object, limit: int = 280) -> str:
    return scrub_proof_language(_clip(text, limit))


def _clip_status_text(text: object, limit: int = 280) -> str:
    return (
        _clip_scrubbed(text, limit)
        .replace("source_backed", "reference-supported")
        .replace("checked_evidence", "checked evidence")
        .replace("theorem_applicability_status", "theorem applicability status")
    )


def _my_feedback_map(run_dir: Path) -> dict:
    """Latest advisory feedback per strategy (Goal 43C). Display-only; a score
    is never evidence and never changes a tone."""

    from .strategy_feedback import latest_by_strategy

    out: dict[str, dict] = {}
    try:
        for sid, item in latest_by_strategy(run_dir).items():
            out[sid] = {
                "score": item.score,
                "priority": item.priority,
                "note": item.safe_note,
            }
    except Exception:  # noqa: BLE001 — advisory; absence is fine
        pass
    return out


def _ledger_context(run_dir: Path, grade: dict | None) -> dict:
    """Shared, honest evidence/time-allocation context for the strategy ledgers.

    Every count is pressure or gap, NEVER earned support: a surviving example
    is not counted as evidence, and reviewer objections are advisory. Target
    subgoal and effort are search/scheduling state, not status."""

    examples = _artifact(run_dir, "examples") or {}
    counter = _artifact(run_dir, "counterexamples") or {}
    audit = _artifact(run_dir, "failure_pattern_audit") or {}

    example_pressure = sum(
        1 for e in examples.get("examples", [])
        if str(e.get("result_for_current_idea")) == "failed"
    )
    counterexample_pressure = sum(
        1 for c in counter.get("tests", [])
        if c.get("refutes_claim") or c.get("exposes_missing_hypothesis")
        or str(c.get("result")) == "failed"
    )
    objections = []
    for entry in (audit.get("findings") or audit.get("patterns") or [])[:6]:
        tier = entry.get("tier") if isinstance(entry, dict) else None
        objections.append(objection_phrase(tier))

    target = {}
    try:
        from .subgoal_graph import assemble_subgoal_graph, select_next_subgoal

        target = select_next_subgoal(assemble_subgoal_graph(run_dir)) or {}
    except Exception:  # noqa: BLE001
        target = {}

    effort = ""
    loop = _artifact(run_dir, "prover_loop") or {}
    if loop:
        effort = effort_phrase((loop.get("budget") or {}).get("approved_effort"))

    return {
        "source_gaps": len([g for g in ((grade or {}).get("source_gaps") or []) if str(g).strip()]),
        "example_pressure": example_pressure,
        "counterexample_pressure": counterexample_pressure,
        "reviewer_objections": objections,
        "target_subgoal_id": target.get("id"),
        "target_local_claim": _clip(target.get("statement"), 200) if target.get("statement") else "",
        "effort": effort,
        "next_cheapest_test": _clip((grade or {}).get("next_cheapest_test"), 200),
    }


def _strategies(
    brief: dict | None,
    selected_id: str | None,
    feedback: dict | None = None,
) -> list[dict]:
    feedback = feedback or {}
    allowed_efforts = {
        str(c.get("strategy_id")): [str(e) for e in (c.get("effort_options") or [])]
        for c in (brief or {}).get("allowed_human_choices", [])
        if c.get("strategy_id")
    }
    items = []
    for raw in (brief or {}).get("top_strategies", [])[:8]:
        source_status = raw.get("source_status")
        sid = _clip(raw.get("id"), 80)
        items.append(
            {
                "id": sid,
                "title": _clip_scrubbed(raw.get("title"), 140),
                "one_line": _clip(raw.get("why_this_route")),
                "score": raw.get("score") if isinstance(raw.get("score"), int) else None,
                "status_sentence": describe_grade(status=raw.get("status")),
                "source_phrase": evidence_phrase(source_status),
                "source_tone": _tone(source_status),
                "main_risk": _clip(raw.get("main_risk")),
                "next_step": _clip(raw.get("next_loop_plan")),
                "selected": bool(selected_id) and raw.get("id") == selected_id,
                # advisory: the user's latest feedback for this route, or null.
                "my_feedback": feedback.get(sid),
                # efforts the kernel allows for this route (for the continue action).
                "allowed_efforts": allowed_efforts.get(sid, []),
            }
        )
    return items


_ADMISSIBILITY_PHRASES = {
    True: "Satisfies all stated hypotheses",
    False: "Does not satisfy all stated hypotheses",
    "unclear": "Hypothesis match unclear",
}

_EXAMPLE_RESULT_PHRASES = {
    "survived": "Survived this test",
    "failed": "Failed this test",
    "inconclusive": "Inconclusive",
    "not_tested": "Not tested yet",
}


def _examples(selection: dict | None) -> list[dict]:
    items = []
    for raw in (selection or {}).get("examples", [])[:24]:
        result = str(raw.get("result_for_current_idea") or "not_tested")
        satisfies = raw.get("satisfies_all_claim_hypotheses")
        items.append(
            {
                "id": _clip(raw.get("id"), 80),
                "name": _clip(raw.get("name"), 140),
                "tests": [_clip(t, 120) for t in (raw.get("what_it_tests") or [])[:4]],
                "behavior": _clip(raw.get("observed_or_inferred_behavior")),
                "admissibility": _ADMISSIBILITY_PHRASES.get(
                    satisfies, _ADMISSIBILITY_PHRASES["unclear"]
                ),
                "result_phrase": _EXAMPLE_RESULT_PHRASES.get(result, "Not tested yet"),
                # an example never EARNS evidence by itself: survived stays
                # neutral, failed warns, everything else is muted.
                "tone": "warn" if result == "failed" else (
                    "neutral" if result == "survived" else "muted"
                ),
            }
        )
    return items


def _counterexamples(report: dict | None) -> list[dict]:
    items = []
    for raw in (report or {}).get("tests", [])[:24]:
        result = str(raw.get("result") or "not_tested")
        checked = result == "failed"
        if bool(raw.get("refutes_claim")):
            phrase = "Checked counterexample against the stated claim"
        elif bool(raw.get("exposes_missing_hypothesis")):
            phrase = "Exposes a missing hypothesis (not a refutation)"
        elif bool(raw.get("refutes_step_only")):
            phrase = "Challenges one proof step only (claim may still hold)"
        elif checked:
            phrase = "Checked pressure test"
        else:
            phrase = _EXAMPLE_RESULT_PHRASES.get(result, "Not tested yet")
        items.append(
            {
                "id": _clip(raw.get("id"), 80),
                "example": _clip(raw.get("example_id"), 100),
                "explanation": _clip(raw.get("explanation")),
                "phrase": phrase,
                "admissibility": _ADMISSIBILITY_PHRASES.get(
                    raw.get("satisfies_all_claim_hypotheses"),
                    _ADMISSIBILITY_PHRASES["unclear"],
                ),
                "repair": _clip(raw.get("repaired_claim_suggestion")),
                "tone": "warn" if checked else "muted",
            }
        )
    return items


def _computations(checks: dict | None) -> list[dict]:
    items = []
    for raw in (checks or {}).get("checks", [])[:24]:
        status = raw.get("status")
        items.append(
            {
                "id": _clip(raw.get("id"), 80),
                "quantity": _clip(raw.get("computed_quantity"), 140),
                "method": _clip(raw.get("method"), 40),
                "result": _clip(raw.get("result")),
                "phrase": evidence_phrase(status),
                "tone": _tone(status),
            }
        )
    return items


def _literature(review: dict | None) -> list[dict]:
    items = []
    for raw in (review or {}).get("candidate_references", [])[:24]:
        status = raw.get("status")
        locator = raw.get("locator") or {}
        items.append(
            {
                "id": _clip(raw.get("id"), 80),
                "title": _clip(raw.get("title"), 160),
                "authors": [_clip(a, 60) for a in (raw.get("authors") or [])[:6]],
                "locator": {
                    "kind": _clip(locator.get("kind"), 24),
                    "value": _clip(locator.get("value"), 160),
                },
                "relevance": _clip(raw.get("relevance"), 40),
                "why": _clip(raw.get("why_relevant")),
                # a literature hit is metadata: its phrase/tone comes from the
                # SOURCE status the kernel recorded, never from mere relevance.
                "phrase": evidence_phrase(status),
                "tone": _tone(status),
            }
        )
    return items


def build_snapshot(run_dir: Path, run_id: str, status: dict) -> dict:
    """The cockpit snapshot view model. ``status`` is the existing
    ``status_summary(run_dir)`` dict (reused so agent/budget wording stays in
    one place)."""

    claim = _artifact(run_dir, "locked_claim")
    grade = _artifact(run_dir, "idea_grade")
    brief = _artifact(run_dir, "strategy_brief")
    examples = _artifact(run_dir, "examples")
    counterexamples = _artifact(run_dir, "counterexamples")
    computations = _artifact(run_dir, "computation_checks")
    literature = _artifact(run_dir, "literature_review")

    selected = (status.get("selected_route") or {}).get("id")
    snapshot = {
        "run_id": run_id,
        "claim": {
            "text": _clip((claim or {}).get("normalized_statement"), 600),
            "raw_text": _clip((claim or {}).get("raw_text"), 1200),
            "status_sentence": describe_grade(
                status=(grade or {}).get("status"),
                proof_language_status=(grade or {}).get("proof_language_status"),
            ),
            "human_review_required": bool((grade or {}).get("human_review_required", True)),
        },
        "status": status,
        "strategies": _strategies(brief, selected, _my_feedback_map(run_dir)),
        "ledger_context": _ledger_context(run_dir, grade),
        "latest_round": load_latest_round(run_dir),
        "examples": _examples(examples),
        "counterexamples": _counterexamples(counterexamples),
        "computations": _computations(computations),
        "literature": _literature(literature),
        "open_warnings": status.get("open_warnings") or [],
        "source_gaps": [
            _clip_status_text(g, 160) for g in ((grade or {}).get("source_gaps") or [])[:8]
        ],
        "missing": {
            "claim": claim is None,
            "grade": grade is None,
            "strategies": brief is None,
            "examples": examples is None,
            "counterexamples": counterexamples is None,
            "computations": computations is None,
            "literature": literature is None,
        },
    }
    return snapshot


# Goal 43B: each cockpit panel reads its own validated slice so panels refresh
# independently. The panel agent_state is honest process state, never evidence:
# it is read from the matching agent's process row (done/running/failed/
# skipped), falls back to "not_run" when no artifact and no row exist, and is
# "ready" once the artifact is present. A cache/poll can never turn a missing
# panel into earned evidence — the tones inside each slice still come only from
# the validated artifact status.
PANELS = ("claim", "strategies", "examples", "counterexamples", "computations", "literature")

# Which on-demand agent backs each panel (for honest per-panel process state).
_PANEL_AGENT = {
    "examples": "examples",
    "counterexamples": "counterexamples",
    "literature": "literature",
}

_AGENT_PROCESS_WORDS = {"done", "running", "failed", "skipped", "queued"}


def _panel_agent_state(panel: str, present: bool, status: dict) -> str:
    """Honest process state for a panel — never an evidence signal."""

    agent = _PANEL_AGENT.get(panel)
    if agent is not None:
        for row in status.get("agents", []) or []:
            if row.get("agent") == agent:
                word = str(row.get("status") or "")
                if word in _AGENT_PROCESS_WORDS:
                    # "done" only means the process finished; whether evidence
                    # was earned is decided by the slice's own status tones.
                    return word
    return "ready" if present else "not_run"


def build_panel(run_dir: Path, run_id: str, status: dict, panel: str) -> dict:
    """One bounded, display-safe panel slice. ``panel`` must be in ``PANELS``;
    the caller validates the run first (same contract as the snapshot)."""

    if panel not in PANELS:
        raise KeyError(panel)
    out: dict = {"run_id": run_id, "panel": panel}

    if panel == "claim":
        claim = _artifact(run_dir, "locked_claim")
        grade = _artifact(run_dir, "idea_grade")
        out["claim"] = {
            "text": _clip((claim or {}).get("normalized_statement"), 600),
            "raw_text": _clip((claim or {}).get("raw_text"), 1200),
            "status_sentence": describe_grade(
                status=(grade or {}).get("status"),
                proof_language_status=(grade or {}).get("proof_language_status"),
            ),
            "human_review_required": bool((grade or {}).get("human_review_required", True)),
        }
        out["missing"] = claim is None
        out["agent_state"] = _panel_agent_state(panel, claim is not None, status)
        return out

    if panel == "strategies":
        brief = _artifact(run_dir, "strategy_brief")
        grade = _artifact(run_dir, "idea_grade")
        selected = (status.get("selected_route") or {}).get("id")
        out["strategies"] = _strategies(brief, selected, _my_feedback_map(run_dir))
        out["ledger_context"] = _ledger_context(run_dir, grade)
        out["latest_round"] = load_latest_round(run_dir)
        out["missing"] = brief is None
        out["agent_state"] = _panel_agent_state(panel, brief is not None, status)
        return out

    artifact_name = {
        "examples": "examples",
        "counterexamples": "counterexamples",
        "computations": "computation_checks",
        "literature": "literature_review",
    }[panel]
    builder = {
        "examples": _examples,
        "counterexamples": _counterexamples,
        "computations": _computations,
        "literature": _literature,
    }[panel]
    data = _artifact(run_dir, artifact_name)
    out["items"] = builder(data)
    out["missing"] = data is None
    out["agent_state"] = _panel_agent_state(panel, data is not None, status)
    if panel == "literature":
        grade = _artifact(run_dir, "idea_grade")
        out["source_gaps"] = [
            _clip_status_text(g, 160)
            for g in ((grade or {}).get("source_gaps") or [])[:8]
        ]
    return out
