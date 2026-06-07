"""Goal 13: product completion acceptance report.

The acceptance report must exist, render an explicit verdict, enumerate the
blocker criteria as NOT triggered, stay honest (no proof-complete capability
claim; product boundary stated), and recommend a concrete next stage.
"""

from __future__ import annotations

import re
from pathlib import Path

REPORT = Path("docs/product/PRODUCT_COMPLETION_ACCEPTANCE_REPORT.md")


def _norm() -> str:
    return re.sub(r"\s+", " ", REPORT.read_text(encoding="utf-8").replace("*", "")).lower()


def test_acceptance_report_exists_and_is_honest() -> None:
    assert REPORT.exists()
    t = _norm()
    # explicit verdict + recommended next stage
    assert "acceptance verdict" in t
    assert "private beta" in t
    assert "recommended next stage" in t
    # product boundary preserved
    assert "not a theorem prover" in t or "is intentionally not a theorem prover" in t
    # no proof-complete capability claim
    for phrase in ("proof complete", "we proved the", "this establishes the theorem"):
        assert phrase not in t, phrase
    # blocker criteria addressed + the core safety properties named
    assert "blocker criteria" in t and "not triggered" in t
    for token in ("source_backed", "instance_checked", "calibration", "fail-closed"):
        assert token in t, token
    # evidence sections present
    for token in ("demo run", "dogfood", "benchmark", "gate"):
        assert token in t, token
