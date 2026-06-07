"""Goal 10: product UI + runbook.

The VS Code bridge snapshot must surface every manifest artifact (including the
V2 advisory artifacts) so the dashboard can show them; it must stay read-only and
inside the workspace; and the runbook must not overclaim (no proof-complete
capability language).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.failure_pattern_bank import audit_failure_patterns
from espresso_proof.kernel import KernelError, require_inside, snapshot
from espresso_proof.mathematical_judgment import judge_run
from espresso_proof.memory_context import build_memory_context
from espresso_proof.mock_pipeline import run_mock_critique

FIXTURES = Path("tests/fixtures")
RUNBOOK = Path("docs/engineering/IVAN_PERSONAL_RUNBOOK.md")


def test_bridge_snapshot_surfaces_v2_advisory_artifacts(tmp_path: Path) -> None:
    # workspace must contain the run for the snapshot path checks.
    run_dir = run_mock_critique("ui", FIXTURES / "vague_claim_unclear" / "input.md", tmp_path)
    # Produce the V2 advisory artifacts.
    judge_run(run_dir)
    audit_failure_patterns(run_dir)
    build_memory_context(run_dir)

    snap = snapshot(tmp_path, run_dir)
    current = snap["current"]
    arts = current["artifacts"]
    # Manifest-driven: every advisory artifact is exposed for the UI to display.
    for key in ("mathematical_judgment", "failure_pattern_audit", "memory_context"):
        assert key in arts, key
        assert arts[key] is not None
    assert current["validation"]["status"] == "valid"


def test_bridge_snapshot_is_read_only(tmp_path: Path) -> None:
    run_dir = run_mock_critique("ui", FIXTURES / "vague_claim_unclear" / "input.md", tmp_path)
    before = sorted(p.name for p in run_dir.iterdir())
    snapshot(tmp_path, run_dir)
    after = sorted(p.name for p in run_dir.iterdir())
    assert before == after  # snapshot writes nothing


def test_require_inside_rejects_paths_outside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    with pytest.raises(KernelError):
        require_inside(root, (tmp_path / "outside" / "x").resolve())


def test_runbook_does_not_overclaim(tmp_path: Path) -> None:
    import re

    text = RUNBOOK.read_text(encoding="utf-8")
    # Normalize markdown emphasis + line wraps so phrase checks are robust.
    norm = re.sub(r"\s+", " ", text.replace("*", "")).lower()
    # The runbook must state the product boundary.
    assert "lean/proof-close theorem prover" in norm
    assert "never claims a theorem is proved" in norm
    # No assertive proof-complete capability claims.
    for phrase in ("proof complete", "we proved the", "this establishes the theorem"):
        assert phrase not in norm, phrase
    # Documents the V2 integrated command + new advisory sections.
    for token in ("orchestrate", "literature-scout", "auto-ground", "memory-context",
                  "verify-matrix-rank-in-run", "mathematical judgment"):
        assert token in norm, token
