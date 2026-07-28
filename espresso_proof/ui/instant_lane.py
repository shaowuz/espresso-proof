"""Instant Mathematical Conversation Lane — a fast DRAFT, never evidence.

The instant lane answers a freshly-submitted claim like a good mathematical
assistant FIRST (intuition, a likely direction, one to three candidate routes,
and the obvious caveats), so the user sees useful text in seconds while the
slower artifact/kernel pipeline runs in the background.

It is bounded hard, exactly like the chat preview but stricter:

- the draft is conversation, NOT proof. It earns no source-backed / checked /
  refuted / human-confirmed status and never says proved / proof-close / proof complete;
- every text field is run through ``scrub_proof_language`` before it is stored
  or streamed, and the strict model REJECTS proof-complete language and any
  unknown (status-smuggling) field outright;
- if a fast model is configured but slow or unavailable, the lane falls back to
  a friendly deterministic draft within a small latency budget — it never blocks
  on the model, and the normal pipeline keeps running regardless;
- if it is persisted it is a SIDECAR (``instant_conversation.yaml``) that is NOT
  in the kernel run manifest, so ``validate_run`` never reads it as evidence;
- when the real artifacts later disagree with the draft, the artifacts win and
  the draft is marked ``superseded_by_artifacts``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.observability import now_iso
from espresso_proof.kernel.proof_language import (
    contains_proof_language,
    scrub_draft_status_claims,
    scrub_source_support_language,
)

INSTANT_FILE = "instant_conversation.yaml"
VISIBLE_LABEL = "Draft only — not checked"
DEFAULT_SAFETY_NOTE = (
    "This draft is conversation, not proof. It earns no source-backed, checked, "
    "refuted, or human-confirmed status; the artifact pipeline above is "
    "authoritative and supersedes this draft whenever they disagree."
)
# The lane must show a draft OR a friendly fallback fast; it never waits on a
# slow external model past this budget (the real pipeline keeps running anyway).
DRAFT_BUDGET_SECONDS = 2.5

InstantStatus = Literal["draft", "fallback", "failed", "unavailable"]
InstantEffort = Literal["minimal", "low"]


def _scrub_text(text: object, limit: int = 600) -> str:
    """Scrub proof-language, source-support vocabulary, and earned-status verdicts,
    then bound length. Every stored/streamed field passes here, so neither a
    proof-complete overclaim nor a 'source-backed'/'checked'/'refuted' verdict
 Can reach the user from an advisory draft."""

    cleaned = scrub_source_support_language(str(text or ""))  # also runs scrub_proof_language
    cleaned = scrub_draft_status_claims(cleaned)
    return cleaned.strip()[:limit]


def _scrub_list(items: object, *, max_items: int, limit: int = 240) -> list[str]:
    out: list[str] = []
    for item in (items or []) if isinstance(items, (list, tuple)) else []:
        cleaned = _scrub_text(item, limit)
        if cleaned:
            out.append(cleaned)
        if len(out) >= max_items:
            break
    return out


class InstantConversation(BaseModel):
    """One advisory instant-lane draft. Strict: any unknown field (including a
    smuggled ``status``/``source_backed``/``proved``/``grade`` field) is rejected,
    and proof-complete language in the answer/routes/caveats is rejected."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = ""
    claim_text: str = ""
    created_at: str = ""
    model: str = ""
    effort: InstantEffort = "minimal"
    status: InstantStatus = "draft"
    # Locked: the label the UI must show. It can never read as earned evidence.
    visible_label: Literal["Draft only — not checked"] = VISIBLE_LABEL
    answer: str = ""
    routes_preview: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    safety_note: str = DEFAULT_SAFETY_NOTE
    # Locked false/true: an advisory draft can never claim to be anything else.
    writes_kernel_status: Literal[False] = False
    advisory_only: Literal[True] = True
    superseded_by_artifacts: bool = False
    superseded_reason: str = ""

    @field_validator("answer", "safety_note", "superseded_reason")
    @classmethod
    def _no_proof_language_text(cls, value: str) -> str:
        phrase = contains_proof_language(value)
        if phrase is not None:
            raise ValueError(f"instant draft text contains proof-complete language: {phrase!r}")
        return value

    @field_validator("routes_preview", "caveats")
    @classmethod
    def _no_proof_language_list(cls, value: list[str]) -> list[str]:
        for item in value:
            phrase = contains_proof_language(item)
            if phrase is not None:
                raise ValueError(f"instant draft item contains proof-complete language: {phrase!r}")
        return value


class InstantDraftClient(Protocol):
    """A bounded FAST model adapter for the instant lane. It must NOT be the deep
    xhigh adapter: low/minimal effort, short output, no tools, no reviewer loop."""

    provider: str
    model: str

    def complete_json(self, *, system: str, prompt: str) -> dict:
        ...


INSTANT_SYSTEM = (
    "You are a fast mathematical research assistant producing a DRAFT only. "
    "You CANNOT and MUST NOT claim a proof, checked status, source-backed status, "
    "or a refutation. Do not say proved, proof-close, or proof complete. Give a short "
    "honest draft: direct intuition, the likely answer direction if clear, one to "
    "three candidate proof routes, and obvious caveats or missing hypotheses. "
    'Return JSON: {"answer": str, "routes": [str], "caveats": [str]}.'
)


def _fallback_draft(claim_text: str) -> tuple[str, list[str], list[str]]:
    """A friendly, always-fast, model-free draft. Honest and generic: it offers a
    way to start without asserting anything about the specific claim."""

    answer = (
        "Quick read: I can start on this right away while the evidence pipeline "
        "runs. Nothing below is checked — treat it as a sketch to react to."
    )
    routes = [
        "Restate the claim precisely and pin every hypothesis before arguing.",
        "Test small or extremal cases to build intuition and look for pressure.",
        "Look for a standard theorem that applies — then it still needs an exact "
        "locator and a hypothesis match before any source support is earned.",
    ]
    caveats = [
        "Missing or implicit hypotheses can change the answer entirely.",
        "Examples passing is not a proof; a counterexample check is still needed.",
    ]
    return answer, routes, caveats


def _parse_client_draft(payload: dict) -> tuple[str, list[str], list[str]]:
    answer = _scrub_text(payload.get("answer"), 600)
    routes = _scrub_list(payload.get("routes"), max_items=3, limit=240)
    caveats = _scrub_list(payload.get("caveats"), max_items=4, limit=200)
    return answer, routes, caveats


def build_instant_draft(
    claim_text: str,
    *,
    client: InstantDraftClient | None = None,
    run_id: str = "",
    budget_seconds: float = DRAFT_BUDGET_SECONDS,
    created_at: str | None = None,
    effort: InstantEffort = "minimal",
) -> InstantConversation:
    """Build a labeled draft within ``budget_seconds``.

    With no client (or a slow/failing/empty one) it returns the deterministic
    fallback — it NEVER blocks past the budget and never raises. The real
    pipeline is the caller's responsibility and keeps running regardless.
    """

    claim = _scrub_text(claim_text, 600)
    status: InstantStatus = "fallback"
    model = ""
    answer, routes, caveats = _fallback_draft(claim)

    if client is not None:
        prompt = f"Claim: {claim}\nProduce the draft JSON now."
        # Run the model on a DAEMON thread and wait only up to the budget. On a
        # timeout we abandon the thread (it cannot block process exit) and fall
        # back immediately — the lane never waits on a slow external model.
        box: dict = {}

        def _call() -> None:
            try:
                box["payload"] = client.complete_json(system=INSTANT_SYSTEM, prompt=prompt)
            except Exception as exc:  # noqa: BLE001 — recorded, never raised into the lane
                box["error"] = exc

        worker = threading.Thread(target=_call, daemon=True)
        worker.start()
        worker.join(timeout=max(0.05, budget_seconds))

        if worker.is_alive():
            status = "unavailable"  # slow model — fall back fast, pipeline runs on
        elif "error" in box:
            status = "failed"
        else:
            payload = box.get("payload")
            c_answer, c_routes, c_caveats = _parse_client_draft(
                payload if isinstance(payload, dict) else {}
            )
            if c_answer or c_routes:
                answer = c_answer or answer
                routes = c_routes or routes
                caveats = c_caveats or caveats
                status = "draft"
                model = getattr(client, "model", "") or ""
            else:
                status = "fallback"  # model returned nothing usable

    return InstantConversation(
        run_id=run_id,
        claim_text=claim,
        created_at=created_at if created_at is not None else now_iso(),
        model=model,
        effort=effort,
        status=status,
        answer=answer,
        routes_preview=routes,
        caveats=caveats,
    )


def draft_lines(draft: InstantConversation) -> list[str]:
    """The ordered, already-scrubbed lines the lane shows. The label leads so the
    'draft, not checked' framing is visible before any mathematical text."""

    lines = [f"{draft.visible_label}.", draft.answer]
    for i, route in enumerate(draft.routes_preview, start=1):
        lines.append(f"Route {i}: {route}")
    for caveat in draft.caveats:
        lines.append(f"Caveat: {caveat}")
    lines.append(draft.safety_note)
    return [_scrub_text(ln, 400) for ln in lines if str(ln).strip()]


def stream_instant_draft(draft: InstantConversation) -> Iterator[str]:
    """Yield the already-scrubbed draft lines one at a time. Never raises."""

    try:
        lines = draft_lines(draft)
    except Exception:  # noqa: BLE001 — the lane is a preview; never break the page
        lines = []
    if not lines:
        lines = [_scrub_text("Draft only — not checked. I could not draft this yet; the pipeline is still running.")]
    yield from lines


def write_instant_conversation(run_dir: Path, draft: InstantConversation) -> Path:
    """Persist the draft as an advisory SIDECAR (not in the kernel manifest). The
    model is dumped in JSON mode; the kernel never reads this file as evidence."""

    path = Path(run_dir) / INSTANT_FILE
    write_yaml(path, draft.model_dump(mode="json"))
    return path


def load_instant_conversation(run_dir: Path) -> InstantConversation | None:
    """Read the sidecar, or None. A malformed/unsafe file fails closed to None
    (advisory data is never trusted, and an invalid draft is simply not shown)."""

    path = Path(run_dir) / INSTANT_FILE
    if not path.exists():
        return None
    try:
        return InstantConversation.model_validate(read_yaml(path))
    except Exception:  # noqa: BLE001 — advisory sidecar fails closed to None
        return None


def mark_superseded(run_dir: Path, reason: str) -> InstantConversation | None:
    """Mark a persisted draft as superseded by the earned artifacts (the artifacts
    win on any disagreement). No-op if there is no draft to supersede."""

    draft = load_instant_conversation(run_dir)
    if draft is None:
        return None
    draft.superseded_by_artifacts = True
    draft.superseded_reason = _scrub_text(reason, 240)
    write_instant_conversation(run_dir, draft)
    return draft


def instant_lane_metrics(
    *,
    request_received_at: str,
    first_visible_ack_ms: int,
    first_draft_visible_ms: int,
    status: InstantStatus,
    background_run_started: bool,
    first_artifact_update_ms: int | None = None,
) -> dict:
    """The latency contract record. Display/telemetry only — it carries
    no evidence and is never read by the kernel."""

    return {
        "instant_lane_metrics": {
            "request_received_at": request_received_at,
            "first_visible_ack_ms": first_visible_ack_ms,
            "first_draft_visible_ms": first_draft_visible_ms,
            "first_artifact_update_ms": first_artifact_update_ms,
            "instant_lane_status": status,
            "background_run_started": background_run_started,
        }
    }
