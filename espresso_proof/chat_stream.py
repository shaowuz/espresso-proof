"""Goal 43F: conversational chat stream — a PREVIEW, never evidence.

The chat stream narrates what Espresso is doing from the run's CURRENT validated
artifact state, in calm conversational language. It is a spike on top of the
existing artifact/kernel model, and it is bounded hard:

- it WRITES NOTHING — no artifact, no status, no sidecar; it only reads;
- every emitted line is scrubbed through ``scrub_source_support_language`` so a
  proof-complete overclaim can never reach the user, and no line asserts
  proved / source-backed / refuted / checked status;
- the evidence panels still come from artifacts; if the stream and the
  artifacts disagree, the artifacts win (the stream is display-only preview);
- it fails closed: any error yields a short, safe fallback line.

The streamed text is explicitly labelled advisory/preview in the UI.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .descriptions import describe_grade, route_source_phrase
from .io import read_yaml
from .proof_language import scrub_source_support_language
from .validators import validate_run


def _artifact(run_dir: Path, name: str) -> dict | None:
    try:
        manifest = read_yaml(run_dir / "run_manifest.yaml")
    except Exception:  # noqa: BLE001
        return None
    rel = manifest.get("artifacts", {}).get(name) or manifest.get(
        "optional_artifacts", {}
    ).get(name)
    if not rel or not (run_dir / rel).exists():
        return None
    try:
        data = read_yaml(run_dir / rel)
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _safe(line: str) -> str:
    """Scrub proof-language and bound length. Every emitted line passes here."""

    return scrub_source_support_language(line).strip()[:240]


def chat_progress_lines(
    run_dir: Path,
    user_message: str = "",
    active_object: dict | None = None,
) -> list[str]:
    """Build the ordered, already-scrubbed conversational lines for a run.

    Each line describes current state in advisory terms ("...remains
    preliminary", "...still needs an exact locator") — never a status claim.
    """

    lines: list[str] = []
    msg = scrub_source_support_language(str(user_message)).strip()[:200]
    if msg:
        lines.append(_safe(f'You asked: "{msg}"'))

    if (run_dir / "run_manifest.yaml").exists():
        try:
            validate_run(run_dir)
        except Exception:  # noqa: BLE001 - preview must fail closed on invalid artifacts
            lines.append(_safe(
                "I could not validate this run's current artifacts, so I will not summarize status. "
                "Use the panels or kernel validation output as the source of truth."
            ))
            return [ln for ln in lines if ln]

    claim = _artifact(run_dir, "locked_claim")
    grade = _artifact(run_dir, "idea_grade")
    if claim:
        lines.append(_safe("Reading the locked claim and what it is precise enough to test."))
        sentence = describe_grade(
            status=(grade or {}).get("status"),
            proof_language_status=(grade or {}).get("proof_language_status"),
        )
        if sentence:
            lines.append(_safe(f"Where it stands: {sentence}"))

    brief = _artifact(run_dir, "strategy_brief")
    routes = (brief or {}).get("top_strategies") or []
    if routes:
        lines.append(_safe(f"I have {len(routes)} candidate route(s); all remain preliminary until evidence is earned."))
        route = routes[0]
        if isinstance(active_object, dict) and active_object.get("kind") == "strategy":
            active_id = str(active_object.get("id") or "")
            route = next((r for r in routes if str(r.get("id") or "") == active_id), route)
        src = route_source_phrase(route.get("source_status"))
        label = "Requested route" if route is not routes[0] else "Top route"
        lines.append(_safe(f'{label} — "{str(route.get("title",""))[:80]}". Source standing: {src}.'))

    examples = _artifact(run_dir, "examples")
    n_ex = len((examples or {}).get("examples") or [])
    counter = _artifact(run_dir, "counterexamples")
    n_ce = len((counter or {}).get("tests") or [])
    if examples is None and counter is None:
        lines.append(_safe("Examples and counterexamples have not been run yet — run those agents to add pressure."))
    else:
        lines.append(_safe(f"Recorded so far: {n_ex} example(s), {n_ce} counterexample test(s). Absence is not safety."))

    lit = _artifact(run_dir, "literature_review")
    refs = (lit or {}).get("candidate_references") or []
    if refs:
        lines.append(_safe(f"Found {len(refs)} candidate reference(s); grounding still needs an exact locator and hypothesis match before any source support is earned."))
    else:
        lines.append(_safe("No candidate references yet; a literature pass would look for relevant theorems."))

    lines.append(_safe("This is a conversational preview of the current artifacts — the panels above are the source of truth."))
    return [ln for ln in lines if ln]


def stream_chat(
    run_dir: Path,
    user_message: str = "",
    active_object: dict | None = None,
) -> Iterator[str]:
    """Yield already-scrubbed conversational lines one at a time. Fails closed
    to a single safe line — never raises into the response."""

    try:
        lines = chat_progress_lines(run_dir, user_message, active_object)
    except Exception:  # noqa: BLE001 — the stream is a preview; never break the page
        lines = []
    if not lines:
        lines = [_safe("I could not read this run's current state; the panels above remain the source of truth.")]
    yield from lines
