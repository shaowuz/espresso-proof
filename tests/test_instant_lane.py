"""Goal 55: Instant Mathematical Conversation Lane — fast DRAFT, never evidence.

These tests pin the safety/latency contract of the instant lane:

- a draft is produced fast, labeled "Draft only — not checked", with intuition,
  routes, and caveats;
- a deliberately SLOW fake model never blocks past the budget — the lane falls
  back and the (caller's) pipeline keeps running;
- proof-complete language never reaches the user (scrubbed on the way in) and the
  strict model REJECTS a hand-written draft that carries proof language or any
  status-upgrade field;
- the persisted sidecar is advisory: it is not in the kernel manifest and
  validate_run still passes with it sitting in the run dir;
- the earned artifacts supersede the draft on disagreement.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.instant_lane import (
    INSTANT_FILE,
    VISIBLE_LABEL,
    InstantConversation,
    build_instant_draft,
    draft_lines,
    instant_lane_metrics,
    load_instant_conversation,
    mark_superseded,
    stream_instant_draft,
    write_instant_conversation,
)
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.orchestrator import orchestrate_run
from espresso_proof.validators import validate_run

FIXTURES = Path("tests/fixtures")


class _FastClient:
    provider = "stub"
    model = "fast-test-model"

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def complete_json(self, *, system: str, prompt: str) -> dict:
        return self._payload


class _SlowClient:
    provider = "external"
    model = "slow-test-model"

    def complete_json(self, *, system: str, prompt: str) -> dict:
        time.sleep(5.0)  # far past any sane budget
        return {"answer": "too late", "routes": [], "caveats": []}


class _BrokenClient:
    provider = "external"
    model = "broken-test-model"

    def complete_json(self, *, system: str, prompt: str) -> dict:
        raise RuntimeError("model exploded")


# -- draft shape + labeling ---------------------------------------------------


def test_fallback_draft_is_labeled_and_structured() -> None:
    draft = build_instant_draft("Show that |G| = p^2 implies G abelian.", created_at="t0")
    assert draft.status == "fallback"  # no client → friendly fallback
    assert draft.visible_label == VISIBLE_LABEL
    assert draft.answer
    assert 1 <= len(draft.routes_preview) <= 3
    assert draft.caveats
    assert draft.advisory_only is True
    assert draft.writes_kernel_status is False
    lines = draft_lines(draft)
    assert lines[0].startswith("Draft only — not checked")


def test_fast_client_draft_is_used_and_scrubbed() -> None:
    draft = build_instant_draft(
        "Claim X.",
        client=_FastClient(
            {
                "answer": "Likely true; we have proved it and proof-close.",  # proof language
                "routes": ["Reduce to the abelian case."],
                "caveats": ["Needs the finiteness hypothesis."],
            }
        ),
        created_at="t0",
    )
    assert draft.status == "draft"
    assert draft.model == "fast-test-model"
    # proof-complete language is scrubbed out before it is ever stored.
    assert "proof-close" not in draft.answer.lower()
    assert "proved" not in draft.answer.lower() or "[withheld" in draft.answer.lower()
    assert draft.routes_preview == ["Reduce to the abelian case."]


# -- latency contract: slow / broken model never blocks -----------------------


def test_slow_model_falls_back_within_budget() -> None:
    start = time.monotonic()
    draft = build_instant_draft(
        "Claim Y.", client=_SlowClient(), budget_seconds=0.5, created_at="t0"
    )
    elapsed = time.monotonic() - start
    assert elapsed < 2.5  # did NOT wait for the 5s model
    assert draft.status == "unavailable"
    assert draft.answer  # a friendly fallback is still shown
    assert draft.visible_label == VISIBLE_LABEL


def test_broken_model_falls_back_and_never_raises() -> None:
    draft = build_instant_draft("Claim Z.", client=_BrokenClient(), created_at="t0")
    assert draft.status == "failed"
    assert draft.answer


# -- strict model: rejects proof language + status-upgrade fields -------------


def test_model_rejects_proof_complete_language() -> None:
    with pytest.raises(ValidationError):
        InstantConversation(claim_text="c", answer="This completes the proof. proof-close.")


def test_model_rejects_proof_language_in_routes() -> None:
    with pytest.raises(ValidationError):
        InstantConversation(claim_text="c", routes_preview=["We proved the theorem."])


def test_model_rejects_status_upgrade_fields() -> None:
    with pytest.raises(ValidationError):
        InstantConversation.model_validate(
            {"claim_text": "c", "status": "draft", "source_backed": True}
        )
    with pytest.raises(ValidationError):
        InstantConversation.model_validate({"claim_text": "c", "writes_kernel_status": True})
    with pytest.raises(ValidationError):
        InstantConversation.model_validate({"claim_text": "c", "human_confirmed": True})


def test_streamed_lines_are_all_scrubbed() -> None:
    draft = build_instant_draft(
        "We have proved this theorem already; proof-close.",  # hostile claim text
        created_at="t0",
    )
    for line in stream_instant_draft(draft):
        assert "proof-close" not in line.lower()


# -- persistence is advisory: sidecar, kernel never reads it ------------------


def test_sidecar_is_advisory_and_validate_run_still_passes(tmp_path: Path) -> None:
    result = orchestrate_run(
        project="g55",
        input_path=FIXTURES / "promising_but_unproved" / "input.md",
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])

    draft = build_instant_draft("Show the claim.", run_id=run_dir.name, created_at="t0")
    path = write_instant_conversation(run_dir, draft)
    assert path.name == INSTANT_FILE

    # the sidecar is NOT registered as a kernel artifact (manifest never names it).
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    names = set((manifest.get("artifacts") or {}).values()) | set(
        (manifest.get("optional_artifacts") or {}).values()
    )
    assert INSTANT_FILE not in names

    # the kernel run still validates with the draft sitting in the run dir.
    validate_run(run_dir)

    # round-trips through the strict model.
    loaded = load_instant_conversation(run_dir)
    assert loaded is not None and loaded.visible_label == VISIBLE_LABEL


def test_malformed_sidecar_fails_closed_to_none(tmp_path: Path) -> None:
    write_yaml(tmp_path / INSTANT_FILE, {"claim_text": "c", "status": "proved_secretly"})
    assert load_instant_conversation(tmp_path) is None


def test_supersede_marks_draft_when_artifacts_disagree(tmp_path: Path) -> None:
    draft = build_instant_draft("Claim.", run_id="r", created_at="t0")
    write_instant_conversation(tmp_path, draft)
    updated = mark_superseded(tmp_path, "Counterexample check refuted the draft direction.")
    assert updated is not None
    assert updated.superseded_by_artifacts is True
    assert "Counterexample" in updated.superseded_reason
    assert load_instant_conversation(tmp_path).superseded_by_artifacts is True


def test_metrics_record_shape() -> None:
    rec = instant_lane_metrics(
        request_received_at="t0",
        first_visible_ack_ms=42,
        first_draft_visible_ms=1200,
        status="draft",
        background_run_started=True,
    )
    m = rec["instant_lane_metrics"]
    assert m["instant_lane_status"] == "draft"
    assert m["background_run_started"] is True
    assert m["first_draft_visible_ms"] == 1200
