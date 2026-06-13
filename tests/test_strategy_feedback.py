"""Goal 43C: human strategy feedback is advisory — model + sidecar discipline.

Feedback can carry a score, a priority, and a note; it can NEVER carry a
status/evidence field, can never claim to be anything but advisory, and lives
in a sidecar the kernel does not read as evidence. A high score keeps a route
exactly as candidate as it was.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.strategy_feedback import (
    FEEDBACK_FILE,
    StrategyFeedback,
    append_feedback,
    feedback_summary,
    latest_by_strategy,
    load_feedback,
)


def _item(**over) -> StrategyFeedback:
    base = dict(
        feedback_id="fb_1",
        run_id="r",
        claim_id="claim_001",
        strategy_id="s1",
        score=70,
        priority="focus",
        note="looks promising via the center argument",
        intended_use="ranking",
        created_at="2026-06-13T00:00:00Z",
    )
    base.update(over)
    return StrategyFeedback.model_validate(base)


def test_valid_feedback_roundtrips() -> None:
    item = _item()
    assert item.advisory_only is True
    assert item.score == 70 and item.priority == "focus"
    # round-trips through json dump/load unchanged.
    again = StrategyFeedback.model_validate(item.model_dump(mode="json"))
    assert again == item


@pytest.mark.parametrize(
    "field",
    [
        "status",
        "source_backed",
        "proved",
        "refuted",
        "grade",
        "proof_status",
        "evidence",
        "source_status",
        "human_confirmed",
        "checked",
    ],
)
def test_status_upgrade_fields_are_rejected(field: str) -> None:
    # the strict model forbids ANY field that smells like earned evidence.
    with pytest.raises(ValidationError):
        _item(**{field: "proved"})


def test_advisory_only_cannot_be_falsified() -> None:
    with pytest.raises(ValidationError):
        _item(advisory_only=False)


def test_score_bounds_and_priority_enum() -> None:
    with pytest.raises(ValidationError):
        _item(score=101)
    with pytest.raises(ValidationError):
        _item(score=-1)
    with pytest.raises(ValidationError):
        _item(priority="promote_to_proved")


def test_append_is_atomic_and_bounded(tmp_path: Path) -> None:
    for i in range(205):
        append_feedback(tmp_path, _item(feedback_id=f"fb_{i}", score=i % 101))
    log = load_feedback(tmp_path)
    assert len(log.items) == 200  # bounded
    assert log.items[-1].feedback_id == "fb_204"
    # the sidecar is a plain advisory file, never part of the kernel manifest.
    assert (tmp_path / FEEDBACK_FILE).exists()


def test_latest_by_strategy_takes_the_most_recent(tmp_path: Path) -> None:
    append_feedback(tmp_path, _item(strategy_id="s1", score=30))
    append_feedback(tmp_path, _item(strategy_id="s1", score=90, priority="reduce"))
    append_feedback(tmp_path, _item(strategy_id="s2", score=55))
    latest = latest_by_strategy(tmp_path)
    assert latest["s1"].score == 90 and latest["s1"].priority == "reduce"
    assert latest["s2"].score == 55


def test_summary_is_advisory_and_never_evidence(tmp_path: Path) -> None:
    append_feedback(tmp_path, _item(strategy_id="s1", score=88, priority="focus"))
    append_feedback(tmp_path, _item(strategy_id="s2", score=20, priority="reduce", note="dead end"))
    summary = feedback_summary(tmp_path, selected_strategy="s1")
    assert "ADVISORY" in summary
    assert "never evidence" in summary
    assert "candidate until the kernel" in summary
    # selected route surfaces first; both scores present; no evidence vocabulary.
    assert summary.index("s1") < summary.index("s2")
    assert "88/100" in summary and "dead end" in summary
    for banned in ("source_backed", "proved", "refuted", "checked by"):
        assert banned not in summary.lower()
    # empty when there is no feedback.
    assert feedback_summary(tmp_path / "nope") == ""


def test_malformed_sidecar_fails_closed_to_empty(tmp_path: Path) -> None:
    write_yaml(tmp_path / FEEDBACK_FILE, {"run_id": "r", "items": [{"bogus": 1}]})
    # a corrupt advisory sidecar is never trusted — it reads as empty.
    assert load_feedback(tmp_path).items == []
    assert read_yaml(tmp_path / FEEDBACK_FILE) is not None  # file untouched on read
