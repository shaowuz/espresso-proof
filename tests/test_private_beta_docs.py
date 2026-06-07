"""Goal 12: private beta release assets.

The beta docs must exist, be honest (no proof-complete capability claim, product
boundary stated), document privacy + known limitations + offline fallback, and
reference real demo/runbook/commands.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path("docs")
BETA = DOCS / "release" / "PRIVATE_BETA.md"
NOTES = DOCS / "release" / "RELEASE_NOTES.md"
FEEDBACK = DOCS / "release" / "FEEDBACK_TEMPLATE.md"


def _norm(p: Path) -> str:
    return re.sub(r"\s+", " ", p.read_text(encoding="utf-8").replace("*", "")).lower()


def test_beta_assets_exist() -> None:
    for p in (BETA, NOTES, FEEDBACK):
        assert p.exists(), p
    # references real, existing assets
    assert (DOCS / "engineering" / "IVAN_PERSONAL_RUNBOOK.md").exists()
    assert (DOCS / "demos" / "p2_group_demo" / "README.md").exists()


def test_beta_doc_is_honest_and_complete() -> None:
    t = _norm(BETA)
    # product boundary
    assert "lean/proof-close theorem prover" in t
    assert "never" in t and "claims a theorem is proved" in t
    # no proof-complete capability claim
    for phrase in ("proof complete", "we proved the", "this establishes the theorem"):
        assert phrase not in t, phrase
    # required release sections (Goal 12)
    for token in (
        "install", "provider", "demo", "dashboard", "known limitations",
        "privacy", "troubleshooting", "feedback", "offline",
    ):
        assert token in t, token
    # documents the real commands
    for cmd in ("orchestrate", "calibration", "verify-finite-groups", "bank-dashboard",
                "espresso_vscode_bridge.py"):
        assert cmd in t, cmd


def test_release_notes_and_feedback_honest() -> None:
    notes = _norm(NOTES)
    assert "private beta" in notes
    assert "not a lean/proof-close theorem prover" in notes or "not a lean" in notes
    for phrase in ("proof complete", "we proved the", "this establishes the theorem"):
        assert phrase not in notes, phrase
    fb = _norm(FEEDBACK)
    assert "safety" in fb and "validate_run" in fb
