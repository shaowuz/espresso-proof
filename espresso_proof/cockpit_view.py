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

from .descriptions import describe_grade, evidence_phrase
from .io import read_yaml

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


def _strategies(brief: dict | None, selected_id: str | None) -> list[dict]:
    items = []
    for raw in (brief or {}).get("top_strategies", [])[:8]:
        source_status = raw.get("source_status")
        items.append(
            {
                "id": _clip(raw.get("id"), 80),
                "title": _clip(raw.get("title"), 140),
                "one_line": _clip(raw.get("why_this_route")),
                "score": raw.get("score") if isinstance(raw.get("score"), int) else None,
                "status_sentence": describe_grade(status=raw.get("status")),
                "source_phrase": evidence_phrase(source_status),
                "source_tone": _tone(source_status),
                "main_risk": _clip(raw.get("main_risk")),
                "next_step": _clip(raw.get("next_loop_plan")),
                "selected": bool(selected_id) and raw.get("id") == selected_id,
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
            "status_sentence": describe_grade(
                status=(grade or {}).get("status"),
                proof_language_status=(grade or {}).get("proof_language_status"),
            ),
            "human_review_required": bool((grade or {}).get("human_review_required", True)),
        },
        "status": status,
        "strategies": _strategies(brief, selected),
        "examples": _examples(examples),
        "counterexamples": _counterexamples(counterexamples),
        "computations": _computations(computations),
        "literature": _literature(literature),
        "source_gaps": [_clip(g, 160) for g in ((grade or {}).get("source_gaps") or [])[:8]],
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
