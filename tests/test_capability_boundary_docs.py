"""Phase 07 — capability boundary docs consistency.

Small, stable guards for the canonical capability table and the de-staled
engineering docs. Positive assertions only (label vocabulary present, safety
statements present, foundational modules referenced as implemented actually
exist), so the test does not churn as the codebase grows.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TABLE = REPO / "docs" / "status" / "ESPRESSO_CAPABILITY_BOUNDARY.md"
KERNEL = REPO / "docs" / "engineering" / "ENGINEERING_KERNEL.md"

_REQUIRED_LABELS = (
    "implemented",
    "live-capable",
    "mock-backed",
    "docs-only",
    "planned",
    "not implemented",
)


def test_capability_table_exists_with_label_vocabulary() -> None:
    assert TABLE.is_file()
    text = TABLE.read_text(encoding="utf-8")
    for label in _REQUIRED_LABELS:
        assert label in text, f"capability table missing label {label!r}"


def test_capability_table_states_safety_boundary() -> None:
    text = TABLE.read_text(encoding="utf-8").lower()
    # orchestrate canonical vs run-flow compatibility
    assert "canonical runtime path" in text
    assert "compatibility wrapper" in text
    # memory/source/reviewer boundary
    assert "advisory" in text
    assert "source support" in text
    assert "reviewer approval is verification" in text  # listed as a "no"
    assert "espresso never claims a theorem proved" in text


def test_kernel_doc_destaled_mock_only() -> None:
    text = KERNEL.read_text(encoding="utf-8")
    assert "intentionally mock-only" not in text
    assert "only accepted execution mode" not in text
    assert "live-capable" in text


def test_implemented_modules_referenced_exist() -> None:
    for rel in (
        "espresso_proof/stage_runner.py",
        "espresso_proof/source_grounding_stage.py",
        "espresso_proof/front_half/_agents.py",
        "espresso_proof/validation/front_half.py",
        "espresso_proof/memory_context.py",
    ):
        assert (REPO / rel).is_file(), f"capability table implies {rel} exists"
