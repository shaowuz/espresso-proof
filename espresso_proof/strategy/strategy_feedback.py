"""Human strategy feedback — advisory ranking/time-allocation input.

This is the human-in-the-loop channel: the maintainer can score a route, add a note, and
ask to focus / de-prioritize / repair it. It is **strictly advisory**:

- the feedback lives in a SIDECAR file (``strategy_feedback.yaml``) that is NOT
  part of the kernel run manifest, so ``validate_run`` never reads it as
  evidence and it cannot enter any proof/source/evidence/grade/claim status;
- the Pydantic model is strict (``extra='forbid'``), so a payload that smuggles
  a ``status`` / ``source_backed`` / ``proved`` / ``refuted`` / ``grade`` field
  is rejected outright — feedback can carry a score, a priority, and a note,
  nothing that looks like earned evidence;
- the only downstream use is an advisory free-text SUMMARY handed to the next
  continuation as ranking/time-allocation context (the existing
  ``taste_judgment`` channel), which the kernel re-validates throughout and
  which can never upgrade a label.

A high score keeps a route exactly as candidate as it was; only the backend/
kernel earns stronger labels.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from espresso_proof.kernel.io import read_yaml, write_yaml

FEEDBACK_FILE = "strategy_feedback.yaml"

# Priority is the user's intent for compute/attention, never an evidence label.
Priority = Literal["focus", "normal", "reduce", "repair"]

# Where the user means the feedback to apply. Display/ranking only.
IntendedUse = Literal["ranking", "time_allocation", "prompt_context"]


class StrategyFeedback(BaseModel):
    """One advisory feedback item for a strategy. Strict: unknown fields
    (including any status/evidence field) are rejected."""

    model_config = ConfigDict(extra="forbid")

    feedback_id: str
    run_id: str
    claim_id: str
    strategy_id: str
    score: int = Field(ge=0, le=100)
    priority: Priority = "normal"
    note: str = ""
    intended_use: IntendedUse = "ranking"
    created_at: str
    # Locked true: an advisory item can never claim to be anything else.
    advisory_only: Literal[True] = True

    @property
    def safe_note(self) -> str:
        return self.note.strip()[:500]


class StrategyFeedbackLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    items: list[StrategyFeedback] = Field(default_factory=list)


def load_feedback(run_dir: Path) -> StrategyFeedbackLog:
    """Read the sidecar log, or an empty one. Never raises on a missing file;
    a malformed file fails closed to empty (advisory data is never trusted)."""

    path = Path(run_dir) / FEEDBACK_FILE
    if not path.exists():
        return StrategyFeedbackLog(run_id=Path(run_dir).name)
    try:
        return StrategyFeedbackLog.model_validate(read_yaml(path))
    except Exception:  # noqa: BLE001 — advisory sidecar fails closed to empty
        return StrategyFeedbackLog(run_id=Path(run_dir).name)


def append_feedback(run_dir: Path, item: StrategyFeedback) -> StrategyFeedbackLog:
    """Validate and atomically append one feedback item to the sidecar.

    The item is model-validated by the caller; here we re-load, append, and
    write atomically (``write_yaml`` renames over the target). The kernel run
    is never touched."""

    log = load_feedback(run_dir)
    log.items.append(item)
    # bound the log so a sidecar can never grow without limit (display-only).
    log.items = log.items[-200:]
    write_yaml(Path(run_dir) / FEEDBACK_FILE, log.model_dump(mode="json"))
    return log


def latest_by_strategy(run_dir: Path) -> dict[str, StrategyFeedback]:
    """The most recent feedback item per strategy id (advisory display)."""

    latest: dict[str, StrategyFeedback] = {}
    for item in load_feedback(run_dir).items:
        latest[item.strategy_id] = item
    return latest


_PRIORITY_PHRASES = {
    "focus": "asked to FOCUS more compute here",
    "reduce": "asked to DE-PRIORITIZE this route",
    "repair": "asked the AI to REPAIR this route",
    "normal": "left at normal priority",
}


def feedback_summary(run_dir: Path, selected_strategy: str | None = None) -> str:
    """A concise, advisory free-text summary for the next continuation.

    This is ranking/time-allocation CONTEXT only — it states the user's
    preference and is explicitly marked advisory. It carries no evidence and
    the kernel re-validates everything downstream."""

    latest = latest_by_strategy(run_dir)
    if not latest:
        return ""
    lines = ["Human strategy feedback (ADVISORY — ranking/time allocation only,"
             " never evidence; routes stay candidate until the kernel earns a"
             " stronger label):"]
    # surface the selected route first when given, then the rest by score.
    order = sorted(
        latest.values(),
        key=lambda f: (f.strategy_id != selected_strategy, -f.score),
    )
    for f in order[:6]:
        phrase = _PRIORITY_PHRASES.get(f.priority, "left at normal priority")
        bit = f"- route {f.strategy_id}: user score {f.score}/100, {phrase}"
        if f.safe_note:
            bit += f'; note: "{f.safe_note}"'
        lines.append(bit)
    return "\n".join(lines)
