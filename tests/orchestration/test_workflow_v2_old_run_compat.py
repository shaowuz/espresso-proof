from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.memory.memory import write_memory_entry
from espresso_proof.memory.memory_context import build_memory_context
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.validators import validate_run


FIXTURES = Path("tests/fixtures")


def _legacy_run(tmp_path: Path) -> Path:
    run_dir = run_mock_critique(
        "workflow_v2_old_run_compat",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    validate_run(run_dir)
    return run_dir


def test_legacy_waiting_human_major_decision_run_still_validates(
    tmp_path: Path,
) -> None:
    run_dir = _legacy_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")

    assert manifest["current_state"] == "WAITING_HUMAN_MAJOR_DECISION"
    assert "update_inbox" not in manifest.get("optional_artifacts", {})
    assert "time_allocation" not in manifest.get("optional_artifacts", {})
    validate_run(run_dir)


def test_existing_old_state_names_remain_valid(tmp_path: Path) -> None:
    for state in (
        "SCAFFOLDED",
        "FRONT_HALF_DONE",
        "LITERATURE_DONE",
        "WAITING_LLM_AGENT_HUMAN_GATE",
        "COMPLETED_MEMO",
    ):
        run_dir = _legacy_run(tmp_path / state.lower())
        manifest = read_yaml(run_dir / "run_manifest.yaml")
        manifest["current_state"] = state
        write_yaml(run_dir / "run_manifest.yaml", manifest)

        validate_run(run_dir)


def test_legacy_run_without_v2_optional_artifacts_validates(tmp_path: Path) -> None:
    run_dir = _legacy_run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    optional = manifest.setdefault("optional_artifacts", {})
    for key in (
        "update_inbox",
        "time_allocation",
        "pressure_status",
        "front_half_run",
        "agent_run_status",
        "memory_context",
    ):
        rel = optional.pop(key, None)
        if rel:
            (run_dir / rel).unlink(missing_ok=True)
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    validate_run(run_dir)


def test_legacy_memory_context_without_v2_revalidation_fields_is_pending_only(
    tmp_path: Path,
) -> None:
    run_dir = _legacy_run(tmp_path)
    project_dir = run_dir.parents[1]
    claim = read_yaml(run_dir / "locked_claim.yaml")
    write_memory_entry(
        project_dir,
        {
            "id": "mem_legacy_old_run",
            "timestamp": "2026-06-15T00:00:00+00:00",
            "project": project_dir.name,
            "type": "source_gap",
            "claim_id": claim["id"],
            "summary": "A prior run requested an exact source locator.",
            "provenance": [
                f"projects/{project_dir.name}/runs/{run_dir.name}/idea_grade.yaml"
            ],
            "reusable_lesson": "request exact source support before ranking this route",
        },
    )
    build_memory_context(run_dir)
    raw = read_yaml(run_dir / "memory_context.yaml")
    raw["schema_version"] = 1
    for entry in raw["matched_entries"]:
        entry.pop("memory_claim_id", None)
        entry.pop("provenance", None)
        entry.pop("revalidation_required", None)
        entry.pop("revalidated_against_locked_claim", None)
    write_yaml(run_dir / "memory_context.yaml", raw)

    validate_run(run_dir)
    memo = build_concise_memo(run_dir)
    assert "[pending revalidation; no action]" in memo
    assert "[request source]" not in memo
