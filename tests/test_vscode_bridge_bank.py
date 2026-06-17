"""VS Code bridge bank compatibility.

The VS Code bridge (snapshot + detail) reads bankable artifacts (examples,
counterexamples) through manifest + bank-aware loading, so a run converted by
`materialize-banks` renders exactly like a single-YAML run. Read-only; shape
stays compatible; no status change; missing artifact fails closed.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

from espresso_proof.kernel import KernelError, snapshot
from espresso_proof.research_bank import load_bankable_dict, materialize_run_banks
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.validators import validate_run

FIXTURES = Path("tests/fixtures")
_FORBIDDEN = ("proof-close", "proof complete", "we proved", "proved the claim")

# Import the VS Code bridge script (it lives in scripts/, not the package).
_BRIDGE_PATH = Path("scripts/espresso_vscode_bridge.py").resolve()
_spec = importlib.util.spec_from_file_location("espresso_vscode_bridge", _BRIDGE_PATH)
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)


def test_vscode_surface_is_marked_dev_only() -> None:
    bridge_text = _BRIDGE_PATH.read_text(encoding="utf-8")
    readme_text = Path("vscode-extension/README.md").read_text(encoding="utf-8")
    package_text = Path("vscode-extension/package.json").read_text(encoding="utf-8")
    main_js = Path("vscode-extension/media/main.js").read_text(encoding="utf-8")

    assert "Dev-only VS Code prototype bridge" in bridge_text
    assert "canonical product UI is the React cockpit" in bridge_text
    assert "Dev-Only" in readme_text
    assert "canonical product UI" in readme_text
    assert "Espresso Proof Dev Prototype" in package_text
    assert "dev-only local prototype" in main_js


def _single_run(tmp_path: Path, name: str = "single") -> tuple[Path, Path]:
    workspace = (tmp_path / name).resolve()
    run_dir = run_mock_critique(
        "hodge_atoms", FIXTURES / "hodge_all_cubic_fourfolds" / "input.md", workspace
    )
    return workspace, run_dir


def _bank_run(tmp_path: Path, name: str = "bank") -> tuple[Path, Path]:
    workspace, run_dir = _single_run(tmp_path, name)
    materialize_run_banks(run_dir)
    validate_run(run_dir)
    return workspace, run_dir


def _digest(run_dir: Path) -> dict[str, str]:
    return {
        str(p.relative_to(run_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(run_dir.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------------------
# 1-3) Snapshot works on both layouts; output shape stays compatible.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", ["single", "bank"])
def test_bridge_snapshot_works_on_both_layouts(tmp_path: Path, layout: str) -> None:
    workspace, run_dir = _single_run(tmp_path) if layout == "single" else _bank_run(tmp_path)
    payload = snapshot(workspace, run_dir)
    current = payload["current"]
    assert current["validation"]["status"] == "valid"
    assert current["missing_artifacts"] == []
    artifacts = current["artifacts"]
    # shape compatibility: same dict shape as the single-file artifacts.
    assert isinstance(artifacts["examples"]["examples"], list)
    assert artifacts["examples"]["examples"], "examples should be non-empty"
    assert isinstance(artifacts["counterexamples"]["tests"], list)
    # previews remain strings (what the extension renders).
    previews = current["artifact_previews"]
    assert isinstance(previews["examples"], str) and previews["examples"]
    assert isinstance(previews["counterexamples"], str) and previews["counterexamples"]
    for phrase in _FORBIDDEN:
        assert phrase not in previews["examples"].lower()


@pytest.mark.parametrize("layout", ["single", "bank"])
def test_bridge_detail_target_works_on_both_layouts(tmp_path: Path, layout: str) -> None:
    _, run_dir = _single_run(tmp_path) if layout == "single" else _bank_run(tmp_path)
    ex_id = load_bankable_dict(run_dir, "examples")["examples"][0]["id"]
    ce_id = load_bankable_dict(run_dir, "counterexamples")["tests"][0]["id"]
    ex_item = bridge.load_detail_target(run_dir, "example", ex_id)
    ce_item = bridge.load_detail_target(run_dir, "counterexample", ce_id)
    assert ex_item["id"] == ex_id
    assert ce_item["id"] == ce_id
    # detail markdown renders without proof-complete language.
    md = bridge.render_detail_markdown("example", ex_item)
    for phrase in _FORBIDDEN:
        assert phrase not in md.lower()


def test_bridge_detail_markdown_scrubs_raw_status_vocabulary() -> None:
    strategy_md = bridge.render_detail_markdown(
        "strategy",
        {
            "id": "route_001",
            "title": "A source-backed route",
            "status": "plausible_direction",
            "source_status": "source_backed",
            "why_this_route": "Could use a source_backed theorem.",
            "main_risk": "The source backed label is only advisory.",
        },
    )
    literature_md = bridge.render_detail_markdown(
        "literature",
        {
            "id": "lit_001",
            "title": "Candidate",
            "status": "candidate",
            "why_relevant": "Mentions a source-backed theorem.",
        },
    )
    rendered = (strategy_md + literature_md).lower()

    assert "## artifact excerpt" not in rendered
    assert "source-backed" not in rendered
    assert "source_backed" not in rendered
    assert "source backed" not in rendered
    assert "route source label; verify step grounding" in rendered
    assert "reference-supported" in rendered


# ---------------------------------------------------------------------------
# 4) Bridge read does not mutate artifacts.
# ---------------------------------------------------------------------------


def test_bridge_read_does_not_mutate_artifacts(tmp_path: Path) -> None:
    workspace, run_dir = _bank_run(tmp_path)
    before = _digest(run_dir)
    snapshot(workspace, run_dir)
    ex_id = load_bankable_dict(run_dir, "examples")["examples"][0]["id"]
    bridge.load_detail_target(run_dir, "example", ex_id)
    assert _digest(run_dir) == before


# ---------------------------------------------------------------------------
# 5) Switching layouts causes no status change in what the bridge surfaces.
# ---------------------------------------------------------------------------


def test_bridge_no_status_change_across_layouts(tmp_path: Path) -> None:
    # same run before vs after materialization (same run_id).
    workspace, run_dir = _single_run(tmp_path)
    before_examples = snapshot(workspace, run_dir)["current"]["artifacts"]["examples"]
    grade_before = snapshot(workspace, run_dir)["current"]["artifacts"]["idea_grade"]
    materialize_run_banks(run_dir)
    validate_run(run_dir)
    after = snapshot(workspace, run_dir)["current"]["artifacts"]
    assert after["examples"] == before_examples
    assert after["idea_grade"] == grade_before


# ---------------------------------------------------------------------------
# 6) Missing required artifact still fails closed.
# ---------------------------------------------------------------------------


def test_bridge_detail_missing_artifact_fails_closed(tmp_path: Path) -> None:
    _, run_dir = _single_run(tmp_path)
    (run_dir / "examples.yaml").unlink()  # manifest still points at it
    with pytest.raises((KernelError, bridge.BridgeError, FileNotFoundError, OSError)):
        bridge.load_detail_target(run_dir, "example", "anything")


def test_bridge_snapshot_marks_missing_required_artifact(tmp_path: Path) -> None:
    workspace, run_dir = _single_run(tmp_path)
    (run_dir / "counterexamples.yaml").unlink()
    payload = snapshot(workspace, run_dir)
    # fail-closed: the snapshot reports the missing required artifact rather than
    # fabricating an empty one.
    assert "counterexamples" in payload["current"]["missing_artifacts"]
