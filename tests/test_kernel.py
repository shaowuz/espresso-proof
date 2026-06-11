from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from espresso_proof.cli import _assert_fixture_expectations, app
from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.kernel import KernelError, require_inside, safe_run_path, snapshot
from espresso_proof.mock_pipeline import run_mock_critique
from espresso_proof.memory import (
    load_memory_index,
    summarize_memory,
    write_memory_entry,
    write_run_memory,
)
from espresso_proof.models import (
    AdmissionCheck,
    AgentContracts,
    ComputationChecks,
    CounterexampleTest,
    ExampleBank,
    ExampleFeedbackSummary,
    HumanChoice,
    HumanExampleFeedback,
    MemoryEntry,
    MemoryIndex,
    Review,
    SourceGroundingReport,
    WorkflowSpec,
)
from espresso_proof.validators import KernelValidationError, validate_run
from espresso_proof.workflow_generator import classify_domain, generate_workflow_spec


FIXTURES = Path("tests/fixtures")


@pytest.mark.parametrize("fixture_dir", sorted(p for p in FIXTURES.iterdir() if p.is_dir()))
def test_fixture_shape_is_complete(fixture_dir: Path) -> None:
    assert (fixture_dir / "input.md").exists()
    assert (fixture_dir / "expected.yaml").exists()
    assert (fixture_dir / "rubric.md").exists()


@pytest.mark.parametrize("fixture_dir", sorted(p for p in FIXTURES.iterdir() if p.is_dir()))
def test_mock_fixtures_validate(fixture_dir: Path, tmp_path: Path) -> None:
    run_dir = run_mock_critique("hodge_atoms", fixture_dir / "input.md", tmp_path)
    validate_run(run_dir)

    expected = read_yaml(fixture_dir / "expected.yaml")
    _assert_fixture_expectations(run_dir, expected)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "tests" in manifest["artifacts"]
    assert "agent_contracts" in manifest["artifacts"]
    assert "human_choice" in manifest["artifacts"]
    assert "strategy_synthesis" in manifest["artifacts"]
    assert "strategy_brief" in manifest["artifacts"]
    assert (run_dir / manifest["artifacts"]["tests"]).exists()
    assert (run_dir / manifest["artifacts"]["agent_contracts"]).exists()
    assert (run_dir / manifest["artifacts"]["human_choice"]).exists()
    assert (run_dir / manifest["artifacts"]["strategy_synthesis"]).exists()
    assert (run_dir / manifest["artifacts"]["strategy_brief"]).exists()
    AgentContracts.model_validate(read_yaml(run_dir / manifest["artifacts"]["agent_contracts"]))
    HumanChoice.model_validate(read_yaml(run_dir / manifest["artifacts"]["human_choice"]))

    memory_dir = run_dir.parents[1] / "memory"
    assert any(memory_dir.glob("*.yaml"))
    MemoryIndex.model_validate(read_yaml(memory_dir / "index.yaml"))


def test_kernel_snapshot_loads_projects_runs_and_current_artifacts(tmp_path: Path) -> None:
    input_path = FIXTURES / "promising_but_unproved" / "input.md"
    run_dir = run_mock_critique("hodge_atoms", input_path, tmp_path)

    payload = snapshot(tmp_path, run_dir)

    assert payload["workspace"] == str(tmp_path.resolve())
    assert payload["selected_run"] == str(run_dir.resolve())
    assert payload["current"]["state"] in {"completed", "stopped"}
    assert payload["current"]["validation"]["status"] == "valid"
    assert "locked_claim" in payload["current"]["artifact_paths"]
    assert payload["current"]["artifacts"]["locked_claim"]["id"] == "claim_001"


def test_kernel_safe_run_path_rejects_escape(tmp_path: Path) -> None:
    run_dir = tmp_path / "projects" / "hodge_atoms" / "runs" / "run_001"
    run_dir.mkdir(parents=True)

    with pytest.raises(KernelError, match="relative"):
        safe_run_path(run_dir, str(tmp_path / "outside.yaml"))
    with pytest.raises(KernelError, match="cannot contain"):
        safe_run_path(run_dir, "../outside.yaml")


def test_kernel_safe_run_path_rejects_symlink(tmp_path: Path) -> None:
    # The VS Code bridge resolves artifact paths through safe_run_path; a symlink
    # pointing outside the run must be rejected (VS_CODE_ARTIFACT_DASHBOARD:
    # artifact paths must not escape workspace).
    run_dir = tmp_path / "projects" / "hodge_atoms" / "runs" / "run_001"
    run_dir.mkdir(parents=True)
    outside = tmp_path / "outside.yaml"
    outside.write_text("not: trusted\n", encoding="utf-8")
    (run_dir / "linked.yaml").symlink_to(outside)

    with pytest.raises(KernelError, match="symlink"):
        safe_run_path(run_dir, "linked.yaml")


def test_kernel_require_inside_confines_to_workspace(tmp_path: Path) -> None:
    # require_inside is the bridge's workspace-confinement guard; verify it
    # directly (it is otherwise only exercised through snapshot).
    root = (tmp_path / "workspace").resolve()
    inside = root / "projects" / "p" / "runs" / "r"
    inside.mkdir(parents=True)

    assert require_inside(root, inside.resolve()) == inside.resolve()
    with pytest.raises(KernelError, match="outside workspace"):
        require_inside(root, (root / ".." / "evil").resolve())


def test_kernel_snapshot_rejects_requested_run_outside_workspace(tmp_path: Path) -> None:
    outside_run = tmp_path.parent / "outside-run"
    outside_run.mkdir(exist_ok=True)

    with pytest.raises(KernelError, match="outside workspace"):
        snapshot(tmp_path, outside_run)


def test_kernel_snapshot_rejects_manifest_artifact_escape(tmp_path: Path) -> None:
    input_path = FIXTURES / "promising_but_unproved" / "input.md"
    run_dir = run_mock_critique("hodge_atoms", input_path, tmp_path)
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest["artifacts"]["examples"] = "../outside.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelError, match="cannot contain"):
        snapshot(tmp_path, run_dir)


def test_kernel_snapshot_rejects_symlink_artifact(tmp_path: Path) -> None:
    input_path = FIXTURES / "promising_but_unproved" / "input.md"
    run_dir = run_mock_critique("hodge_atoms", input_path, tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text("not: trusted\n", encoding="utf-8")
    examples_path = run_dir / "examples.yaml"
    examples_path.unlink()
    examples_path.symlink_to(outside)

    with pytest.raises(KernelError, match="symlink"):
        snapshot(tmp_path, run_dir)


def test_bridge_open_detail_rejects_invalid_manifest_path(tmp_path: Path) -> None:
    input_path = FIXTURES / "promising_but_unproved" / "input.md"
    run_dir = run_mock_critique("hodge_atoms", input_path, tmp_path)
    example_id = read_yaml(run_dir / "examples.yaml")["examples"][0]["id"]
    manifest_path = run_dir / "run_manifest.yaml"
    manifest = read_yaml(manifest_path)
    manifest["artifacts"]["examples"] = "../outside.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/espresso_vscode_bridge.py",
            "open-detail",
            "--workspace",
            str(tmp_path),
            "--run",
            str(run_dir),
            "--kind",
            "example",
            "--target-id",
            example_id,
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert completed.returncode == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert "artifact path" in payload["error"] or "manifest" in payload["error"]


def test_seed_files_validate() -> None:
    WorkflowSpec.model_validate(read_yaml(Path("projects/hodge_atoms/workflow_spec.yaml")))
    ExampleBank.model_validate(read_yaml(Path("projects/hodge_atoms/example_bank.yaml")))
    AgentContracts.model_validate(read_yaml(Path("projects/hodge_atoms/agent_contracts.yaml")))
    WorkflowSpec.model_validate(read_yaml(Path("projects/coherent_base_change/workflow_spec.yaml")))
    ExampleBank.model_validate(read_yaml(Path("projects/coherent_base_change/example_bank.yaml")))
    AgentContracts.model_validate(read_yaml(Path("projects/coherent_base_change/agent_contracts.yaml")))


def test_cross_artifact_rejects_unchecked_evidence(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "unchecked_example_not_evidence" / "input.md",
        tmp_path,
    )
    grade_path = run_dir / "idea_grade.yaml"
    data = read_yaml(grade_path)
    data["survived_tests"] = ["test_special_vs_very_general"]

    grade_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    counterexample_path = run_dir / "counterexamples.yaml"
    counterexample_data = read_yaml(counterexample_path)
    counterexample_data["tests"][0]["result"] = "survived"
    counterexample_path.write_text(yaml.safe_dump(counterexample_data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="unchecked example"):
        validate_run(run_dir)


def test_cross_artifact_rejects_dropped_hypothesis_refutation(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "dropped_hypothesis_not_refutation" / "input.md",
        tmp_path,
    )
    path = run_dir / "counterexamples.yaml"
    data = read_yaml(path)
    data["tests"][0]["refutes_claim"] = True

    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="dropped-hypothesis"):
        validate_run(run_dir)


def test_cross_artifact_rejects_bad_final_report_language(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    (run_dir / "final_report.md").write_text("proof complete and verified", encoding="utf-8")

    with pytest.raises(KernelValidationError, match="forbidden proof language"):
        validate_run(run_dir)


def test_failed_parallel_reviewer_blocks_promising_grade(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    grade_path = run_dir / "idea_grade.yaml"
    data = read_yaml(grade_path)
    data["status"] = "promising"
    data["score"] = 80

    grade_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="failed reviewers cannot produce promising grade"):
        validate_run(run_dir)


def test_literature_review_cannot_be_source_backed(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "literature_review.yaml"
    data = read_yaml(path)
    data["candidate_references"][0]["status"] = "source_backed"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="Literature Review"):
        validate_run(run_dir)


def test_loop_control_stops_after_two_no_progress_cycles(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "loop_control.yaml"
    data = read_yaml(path)
    data["no_new_counterexamples_cycles"] = 2
    data["no_new_references_cycles"] = 2
    data["grade_delta_last_two"] = 0
    data["should_stop"] = False
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="no-progress"):
        validate_run(run_dir)


def test_manual_review_cannot_be_checked_evidence(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "unchecked_example_not_evidence" / "input.md",
        tmp_path,
    )
    path = run_dir / "computation_checks.yaml"
    data = read_yaml(path)
    data["checks"][1]["status"] = "checked"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="manual_review_needed"):
        validate_run(run_dir)


def test_line_proof_sketch_only_does_not_force_failed_attempt() -> None:
    review = Review.model_validate(
        {
            "claim_id": "claim_001",
            "attempt_status": "unclear",
            "verdict": "unclear",
            "reviewers": [
                {
                    "reviewer_id": "strategy_reviewer",
                    "scope": "proof_strategy",
                    "verdict": "pass",
                    "applicability": "applicable",
                    "summary": "Strategy is plausible.",
                    "objections": [],
                    "checked_artifacts": ["tests.yaml"],
                    "required_reruns": [],
                    "severity": "medium",
                },
                {
                    "reviewer_id": "evidence_reviewer",
                    "scope": "examples_counterexamples",
                    "verdict": "pass",
                    "applicability": "applicable",
                    "summary": "Evidence is not misused.",
                    "objections": [],
                    "checked_artifacts": ["computation_checks.yaml"],
                    "required_reruns": [],
                    "severity": "medium",
                },
                {
                    "reviewer_id": "line_proof_reviewer",
                    "scope": "line_by_line_proof",
                    "verdict": "unclear",
                    "applicability": "sketch_only",
                    "summary": "No full line-by-line proof was supplied.",
                    "objections": ["Only a proof sketch is available."],
                    "checked_artifacts": ["source_grounding.yaml"],
                    "required_reruns": [],
                    "severity": "medium",
                },
            ],
            "strongest_objection": "No full proof is available, but this is not a failed attempt.",
            "theorem_drift": [],
            "missing_hypotheses": [],
            "source_risks": [],
            "example_risks": [],
            "downgrade_reasons": ["Line proof review is sketch_only."],
        }
    )
    assert review.attempt_status.value == "unclear"


def test_sketch_only_applicability_cannot_be_used_by_strategy_reviewer() -> None:
    with pytest.raises(ValueError, match="only line_proof_reviewer"):
        Review.model_validate(
            {
                "claim_id": "claim_001",
                "attempt_status": "unclear",
                "verdict": "unclear",
                "reviewers": [
                    {
                        "reviewer_id": "strategy_reviewer",
                        "scope": "proof_strategy",
                        "verdict": "unclear",
                        "applicability": "sketch_only",
                        "summary": "Invalid applicability.",
                        "objections": [],
                        "checked_artifacts": [],
                        "required_reruns": [],
                        "severity": "medium",
                    },
                    {
                        "reviewer_id": "evidence_reviewer",
                        "scope": "examples_counterexamples",
                        "verdict": "pass",
                        "applicability": "applicable",
                        "summary": "Evidence is okay.",
                        "objections": [],
                        "checked_artifacts": [],
                        "required_reruns": [],
                        "severity": "medium",
                    },
                    {
                        "reviewer_id": "line_proof_reviewer",
                        "scope": "line_by_line_proof",
                        "verdict": "unclear",
                        "applicability": "sketch_only",
                        "summary": "Sketch only.",
                        "objections": [],
                        "checked_artifacts": [],
                        "required_reruns": [],
                        "severity": "medium",
                    },
                ],
                "strongest_objection": "Invalid reviewer applicability.",
                "theorem_drift": [],
                "missing_hypotheses": [],
                "source_risks": [],
                "example_risks": [],
                "downgrade_reasons": [],
            }
        )


def test_human_choice_rejects_unknown_selected_strategy(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "human_choice.yaml"
    data = read_yaml(path)
    data.update(
        {
            "status": "selected",
            "selected_strategy": "not_a_strategy",
            "effort": "quick_test",
            "taste_judgment": "Try this anyway.",
            "continue_decision": "continue",
            "human_confirmed": True,
        }
    )
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="unknown strategy"):
        validate_run(run_dir)


def test_choose_strategy_cli_writes_human_choice_and_memory(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "choose-strategy",
            "--run",
            str(run_dir),
            "--strategy",
            "strategy_pin_reference",
            "--effort",
            "quick_test",
            "--decision",
            "continue",
            "--reason",
            "Pinning the exact theorem is the cheapest useful next step.",
        ],
    )

    assert result.exit_code == 0, result.output
    validate_run(run_dir)
    human_choice = read_yaml(run_dir / "human_choice.yaml")
    assert human_choice["status"] == "selected"
    assert human_choice["selected_strategy"] == "strategy_pin_reference"
    assert human_choice["effort"] == "quick_test"
    assert human_choice["continue_decision"] == "continue"
    assert human_choice["human_confirmed"] is True

    project_dir = run_dir.parents[1]
    memory = summarize_memory(project_dir, claim_text="pin exact Hodge theorem")
    assert "User decisions" in memory
    assert "strategy_pin_reference" in memory
    index = read_yaml(project_dir / "memory" / "index.yaml")
    assert any(item["type"] == "user_decision" for item in index["entries"])


def test_choose_strategy_cli_rejects_invalid_effort_without_writing(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    before = read_yaml(run_dir / "human_choice.yaml")
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "choose-strategy",
            "--run",
            str(run_dir),
            "--strategy",
            "none",
            "--effort",
            "normal_pass",
            "--decision",
            "stop",
            "--reason",
            "No route is worth continuing.",
        ],
    )

    assert result.exit_code != 0
    assert read_yaml(run_dir / "human_choice.yaml") == before
    validate_run(run_dir)


def test_agent_contracts_must_cover_required_outputs(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "agent_contracts.yaml"
    data = read_yaml(path)
    data["contracts"] = [
        contract
        for contract in data["contracts"]
        if contract["output_artifact"] != "strategy_brief.yaml"
    ]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="agent_contracts missing outputs"):
        validate_run(run_dir)


def test_memory_loading_missing_index_returns_empty(tmp_path: Path) -> None:
    index = load_memory_index(tmp_path / "projects" / "empty_project")
    assert index.project_id == "empty_project"
    assert index.entries == []
    assert summarize_memory(tmp_path / "projects" / "empty_project") == ""


def test_memory_summary_loads_written_memory(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    summary = summarize_memory(run_dir.parents[1], claim_text="cubic fourfold hodge atom")

    assert "Source gaps" in summary
    assert "top strategy" in summary
    assert "provenance:" in summary


def _memory_entry(**overrides) -> dict:
    entry = {
        "id": "mem_entry_1",
        "timestamp": "2026-06-03T00:00:00+00:00",
        "project": "mem_proj",
        "type": "failed_attempt",
        "claim_id": "claim_1",
        "summary": "specialization route stalled at step 3",
        "provenance": ["run:demo"],
        "reusable_lesson": "specialization route needs a flatness check first",
    }
    entry.update(overrides)
    return entry


def test_memory_entry_schema_rejects_status_fields() -> None:
    # Memory is advisory, not evidential: it cannot set source_backed, mark
    # examples/computations checked, or upgrade evidence/proof status
    # (KERNEL_SAFETY_RULES; memory_reuse slice). The schema has no status slot,
    # so any such field is rejected.
    forbidden_fields = (
        "source_backed",
        "examples_checked",
        "proof_status",
        "evidence_status",
        "computation_checked",
        "computations_checked",
    )
    for forbidden in forbidden_fields:
        with pytest.raises(ValueError, match="Extra inputs"):
            MemoryEntry.model_validate(_memory_entry(**{forbidden: True}))


def test_run_memory_produces_reviewer_and_counterexample_categories(tmp_path: Path) -> None:
    # A real mock run must PRODUCE (not just be able to render) reviewer-objection
    # and counterexample memory categories, via the unified write_run_memory.
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    summary = summarize_memory(run_dir.parents[1], claim_text="cubic fourfold hodge atom")
    assert "Source gaps" in summary  # existing grade-summary entry preserved
    assert "Reviewer objections" in summary
    assert "Counterexamples" in summary


def test_write_run_memory_emits_optional_categories(tmp_path: Path) -> None:
    # When the optional theorem_applicability / human_example_feedback artifacts
    # exist, write_run_memory produces those advisory memory categories too.
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    (run_dir / "theorem_applicability.yaml").write_text(
        yaml.safe_dump({"usages": [{"blocks_source_backed": True, "applicability_status": "blocked"}]}),
        encoding="utf-8",
    )
    (run_dir / "human_example_feedback.yaml").write_text(
        yaml.safe_dump({"feedback": [{"feedback_id": "f1"}]}),
        encoding="utf-8",
    )
    write_run_memory(
        run_dir.parents[1], run_dir, project_id=run_dir.parents[1].name, run_id=run_dir.name
    )
    summary = summarize_memory(run_dir.parents[1], claim_text="cubic fourfold")
    assert "Theorem applicability failures" in summary
    assert "Human example feedback" in summary


def test_memory_summary_includes_required_categories(tmp_path: Path) -> None:
    # The memory summary must surface every required category (MEMORY_REUSE
    # Required Memory Categories), including theorem applicability failures,
    # reviewer objections, and human example feedback.
    project_dir = tmp_path / "projects" / "mem_categories"
    sections = {
        "theorem_applicability_failure": "Theorem applicability failures",
        "reviewer_objection": "Reviewer objections",
        "human_example_feedback": "Human example feedback",
    }
    for entry_type, header in sections.items():
        write_memory_entry(
            project_dir,
            _memory_entry(
                id=f"mem_{entry_type}",
                type=entry_type,
                summary=f"{entry_type} remembered for reuse",
            ),
        )

    summary = summarize_memory(project_dir, claim_text="remembered for reuse")
    for header in sections.values():
        assert header in summary


def test_memory_summary_includes_failed_route(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "mem_proj"
    write_memory_entry(project_dir, _memory_entry(id="mem_failed_1"))

    summary = summarize_memory(project_dir, claim_text="specialization route flatness")
    assert "Previous failed routes:" in summary
    assert "mem_failed_1" in summary


def test_memory_summary_is_deterministic(tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "mem_det"
    write_memory_entry(project_dir, _memory_entry(id="mem_a", type="failed_attempt"))
    write_memory_entry(
        project_dir,
        _memory_entry(id="mem_b", type="source_gap", summary="missing exact locator for Theorem 1.2"),
    )

    first = summarize_memory(project_dir, claim_text="cubic fourfold locator")
    second = summarize_memory(project_dir, claim_text="cubic fourfold locator")
    assert first == second
    assert first  # non-empty


def test_inspect_shows_claim_grade_and_evidence_sections(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    result = CliRunner().invoke(app, ["inspect", "--run", str(run_dir)])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Claim" in out
    assert "status:" in out  # claim locked / human_confirmed status line
    assert "Grade" in out
    assert "Source Gaps" in out
    assert "Examples / Counterexamples" in out
    assert "Top Strategies" in out
    assert "Artifacts" in out
    assert "locked_claim" in out  # artifact path listing


def test_inspect_does_not_emit_proof_complete_language(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    result = CliRunner().invoke(app, ["inspect", "--run", str(run_dir)])
    assert result.exit_code == 0, result.output
    lowered = result.output.lower()
    assert "proof complete" not in lowered
    assert "proved the theorem" not in lowered


def test_memo_renders_compact_graded_sections(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    result = CliRunner().invoke(app, ["memo", "--run", str(run_dir)])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "ESPRESSO MEMO" in out
    assert "Verdict:" in out
    assert "Source gaps:" in out
    assert "Top routes:" in out
    assert "Next cheapest test:" in out
    # proof state is rendered in concrete English, not the raw enum.
    assert "Proof state:" in out
    assert "idea sketch" in out.lower()
    assert "idea_sketch" not in out  # no raw enum in user-visible text


def test_memo_unclear_claim_recommends_locking(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "vague_claim_unclear" / "input.md",
        tmp_path,
    )
    result = CliRunner().invoke(app, ["memo", "--run", str(run_dir)])
    assert result.exit_code == 0, result.output
    lowered = result.output.lower()
    # concrete English, not the abstract label "unclear".
    assert "not precise enough" in lowered
    assert "unclear" not in lowered
    assert "lock" in lowered  # "First action: lock..." / "Lock a precise claim first"


def test_memo_no_proof_complete_language_for_weak_artifacts(tmp_path: Path) -> None:
    # The memo must not overstate: for a proof_candidate it shows the real status,
    # and never emits memo-generated proof-complete language (Concise Answer
    # safety; AGENT_AUTOMATION_ENGINEERING_PLAN Step 1).
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    result = CliRunner().invoke(app, ["memo", "--run", str(run_dir)])
    assert result.exit_code == 0, result.output
    out = result.output
    # proof state in concrete English, never the word "proved" or the raw enum.
    assert "candidate argument" in out.lower()
    assert "proof_candidate" not in out
    # Check only the memo-GENERATED verdict section (everything from "Verdict:"),
    # so the assertion does not depend on what the echoed claim text contains.
    verdict_section = out.split("Verdict:", 1)[1].lower()
    for forbidden in ("proof complete", "verified", "proof-close", "proved"):
        assert forbidden not in verdict_section


def test_inspect_does_not_mutate_run(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    before = {path.name: path.read_bytes() for path in sorted(run_dir.glob("*.yaml"))}
    result = CliRunner().invoke(app, ["inspect", "--run", str(run_dir)])
    assert result.exit_code == 0, result.output
    after = {path.name: path.read_bytes() for path in sorted(run_dir.glob("*.yaml"))}
    assert before == after


def test_workflow_generator_classifies_domains() -> None:
    hodge_spec = generate_workflow_spec(
        "KKPY Hodge atoms and mirror symmetry for cubic fourfolds",
        project_name="generated_hodge",
    )
    coherent_spec = generate_workflow_spec(
        "coherent sheaf cohomology and base change for a flat proper morphism",
        project_name="generated_base_change",
    )
    stacks_spec = generate_workflow_spec(
        "fpqc descent and representability for an algebraic stack",
        project_name="generated_stacks",
    )
    generic_spec = generate_workflow_spec(
        "study a smooth projective morphism and divisor intersection problem",
        project_name="generated_generic",
    )

    assert classify_domain("Hodge atom mirror symmetry") == "hodge_atoms"
    assert hodge_spec["domain_key"] == "hodge_atoms"
    assert coherent_spec["domain_key"] == "coherent_base_change"
    assert stacks_spec["domain_key"] == "stacks_descent"
    assert generic_spec["domain_key"] == "generic_ag"
    assert hodge_spec["canonical_example_templates"] != coherent_spec["canonical_example_templates"]
    WorkflowSpec.model_validate(hodge_spec)
    WorkflowSpec.model_validate(coherent_spec)
    WorkflowSpec.model_validate(stacks_spec)
    WorkflowSpec.model_validate(generic_spec)


def test_workflow_spec_rejects_missing_lean_non_goal() -> None:
    # Espresso is not Lean-heavy by default (KERNEL_SAFETY_RULES Mathematical
    # Status Rules): a workflow spec must keep Lean formalization an explicit
    # non-goal. Dropping that non-goal must be rejected.
    spec = read_yaml(Path("projects/hodge_atoms/workflow_spec.yaml"))
    spec["non_goals"] = [
        goal for goal in spec["non_goals"] if "lean formalization" not in goal.lower()
    ]
    with pytest.raises(ValueError, match="reject Lean formalization"):
        WorkflowSpec.model_validate(spec)


def test_strategy_rules_reject_standard_score_overreach(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "strategy_synthesis.yaml"
    data = read_yaml(path)
    data["routes"][0]["id"] = "strategy_speculate_big_jump"
    data["routes"][0]["score"] = 70
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="max lift"):
        validate_run(run_dir)


def test_strategy_rules_reject_repair_score_overreach(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "strategy_synthesis.yaml"
    data = read_yaml(path)
    data["routes"][0]["id"] = "strategy_repair_overclaim"
    data["routes"][0]["score"] = 80
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="repair-like strategy score"):
        validate_run(run_dir)


def test_strategy_rules_reject_high_score_with_unverified_source(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    path = run_dir / "strategy_synthesis.yaml"
    data = read_yaml(path)
    data["routes"][0]["score"] = 75
    data["routes"][0]["source_status"] = "unverified"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="source_status cannot have score"):
        validate_run(run_dir)


def test_strategy_rules_reject_unchecked_deep_exploration(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "strategy_synthesis.yaml"
    data = read_yaml(path)
    data["routes"][2]["failed_tests"] = ["test_special_vs_very_general"]
    data["routes"][2]["estimated_effort"] = "deep_exploration"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="unchecked example evidence"):
        validate_run(run_dir)


def test_strategy_rules_reject_unclear_claim_non_lock_route(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "vague_claim_unclear" / "input.md",
        tmp_path,
    )
    path = run_dir / "strategy_synthesis.yaml"
    data = read_yaml(path)
    data["routes"][0]["id"] = "strategy_test_anyway"
    data["routes"][0]["estimated_effort"] = "deep_exploration"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="unclear claim"):
        validate_run(run_dir)


def test_strategy_rules_reject_failed_reviewer_promising_strategy(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "strategy_synthesis.yaml"
    data = read_yaml(path)
    data["routes"][0]["status"] = "promising"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="failed reviewers cannot lead to promising strategy"):
        validate_run(run_dir)


def test_strategy_rules_reject_failed_reviewer_proof_like_strategy(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "strategy_synthesis.yaml"
    data = read_yaml(path)
    data["routes"][0]["next_loop_plan"] = "Write the proof and present it as complete."
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="proof-like strategy"):
        validate_run(run_dir)


def test_referencing_misapplied_requires_hypothesis_mismatch(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "source_misapplication_very_general_to_all" / "input.md",
        tmp_path,
    )
    path = run_dir / "source_grounding.yaml"
    data = read_yaml(path)
    data["steps"][0]["hypothesis_match"] = {"matches": True, "mismatches": []}
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="misapplied step"):
        validate_run(run_dir)


def test_promising_fixture_has_non_toy_checked_support(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    grade = read_yaml(run_dir / "idea_grade.yaml")
    computations = read_yaml(run_dir / "computation_checks.yaml")
    assert "test_very_general_package_support" in grade["survived_tests"]
    support = {
        check["id"]: check
        for check in computations["checks"]
        if check["status"] in {"checked", "source_checked"}
    }
    assert "check_very_general_hodge_package_source" in support
    assert support["check_very_general_hodge_package_source"]["status"] == "source_checked"


def test_eval_rejects_incomplete_fixture_shape(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "bad_fixture"
    fixture_dir.mkdir()
    (fixture_dir / "input.md").write_text("bad fixture", encoding="utf-8")

    result = CliRunner().invoke(app, ["eval", "--mode", "mock", "--fixture", str(fixture_dir)])

    assert result.exit_code != 0
    assert isinstance(result.exception, KernelValidationError)
    assert "missing required files" in str(result.exception)


def test_manifest_rejects_run_id_mismatch(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "run_manifest.yaml"
    data = read_yaml(path)
    data["run_id"] = "wrong-run-id"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="does not match run dir"):
        validate_run(run_dir)


def test_manifest_rejects_absolute_artifact_path(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "run_manifest.yaml"
    data = read_yaml(path)
    data["artifacts"]["locked_claim"] = str((run_dir / "locked_claim.yaml").resolve())
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="must be relative"):
        validate_run(run_dir)


def test_manifest_rejects_parent_traversal_and_missing_artifact(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "run_manifest.yaml"
    data = read_yaml(path)
    data["artifacts"]["locked_claim"] = "../locked_claim.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="cannot contain"):
        validate_run(run_dir)

    data["artifacts"]["locked_claim"] = "missing_locked_claim.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(KernelValidationError, match="is missing"):
        validate_run(run_dir)


def test_computation_checks_reject_nested_claim_mismatch(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "computation_checks.yaml"
    data = read_yaml(path)
    data["checks"][0]["claim_id"] = "other_claim"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="claim_id mismatch"):
        validate_run(run_dir)


def test_test_report_requires_checked_evidence_for_survived_or_failed(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "tests.yaml"
    data = read_yaml(path)
    data["tests"][0]["result"] = "failed"
    data["tests"][0]["evidence_status"] = "human_needed"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="checked/source_checked evidence"):
        validate_run(run_dir)


def test_test_report_counterexample_evidence_must_be_audited(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "tests.yaml"
    data = read_yaml(path)
    data["tests"][0]["uses_examples"] = []
    data["tests"][0]["uses_counterexamples"] = ["test_special_vs_very_general"]
    data["tests"][0]["result"] = "failed"
    data["tests"][0]["evidence_status"] = "checked"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="unchecked counterexample example"):
        validate_run(run_dir)


def test_literature_review_cannot_mark_misapplied(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "literature_review.yaml"
    data = read_yaml(path)
    data["candidate_references"][0]["status"] = "misapplied"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="Literature Review"):
        validate_run(run_dir)


def test_source_backed_requires_exact_locator_and_hypothesis_match() -> None:
    base = {
        "claim_id": "claim_001",
        "source_refs": [
            {
                "id": "src_bad",
                "type": "paper",
                "title": "Bad source",
                "authors": [],
                "locator": {"kind": "unknown", "value": "to_be_filled"},
                "statement_or_summary": "Too vague.",
                "hypothesis_match": {"matches": True, "mismatches": []},
                "confidence": "high",
                "status": "source_backed",
            }
        ],
        "steps": [],
    }
    with pytest.raises(ValueError, match="exact locator"):
        SourceGroundingReport.model_validate(base)

    base["source_refs"][0]["locator"] = {"kind": "theorem", "value": "Theorem 1.1"}
    base["source_refs"][0]["hypothesis_match"] = {"matches": "unclear", "mismatches": []}
    with pytest.raises(ValueError, match="matching hypotheses"):
        SourceGroundingReport.model_validate(base)


def test_final_report_allows_unverified_but_rejects_proof_language(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    (run_dir / "final_report.md").write_text(
        "This is an unverified candidate and not a completed argument.",
        encoding="utf-8",
    )
    validate_run(run_dir)

    (run_dir / "final_report.md").write_text(
        "This proof establishes the theorem.",
        encoding="utf-8",
    )
    with pytest.raises(KernelValidationError, match="forbidden proof language"):
        validate_run(run_dir)


def test_loop_control_stop_requires_should_stop(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    path = run_dir / "loop_control.yaml"
    data = read_yaml(path)
    data["next_action"] = "stop"
    data["should_stop"] = False
    data["stop_reasons"] = []
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="next_action=stop"):
        validate_run(run_dir)


def test_strategy_rules_reject_high_score_for_likely_false_or_refuted_idea(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "likely_false_multiple_pressures" / "input.md",
        tmp_path,
    )
    grade_path = run_dir / "idea_grade.yaml"
    grade = read_yaml(grade_path)
    grade["score"] = 70
    grade_path.write_text(yaml.safe_dump(grade, sort_keys=False), encoding="utf-8")

    synthesis_path = run_dir / "strategy_synthesis.yaml"
    synthesis = read_yaml(synthesis_path)
    synthesis["routes"][0]["score"] = 75
    synthesis["routes"][0]["source_status"] = "candidate"
    synthesis_path.write_text(yaml.safe_dump(synthesis, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="promising or fragile"):
        validate_run(run_dir)


def test_strategy_rules_reject_high_score_for_refuted_idea(tmp_path: Path) -> None:
    # A refuted idea cannot carry a high-confidence (>=75) strategy route
    # (KERNEL_SAFETY_RULES Strategy Rules; strategy_scoring_gate slice). The
    # sibling test exercises likely_false; this isolates the refuted status.
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "refuted_by_all_hypotheses_counterexample" / "input.md",
        tmp_path,
    )
    grade_path = run_dir / "idea_grade.yaml"
    grade = read_yaml(grade_path)
    assert grade["status"] == "refuted"
    grade["score"] = 60
    grade_path.write_text(yaml.safe_dump(grade, sort_keys=False), encoding="utf-8")

    synthesis_path = run_dir / "strategy_synthesis.yaml"
    synthesis = read_yaml(synthesis_path)
    synthesis["routes"][0]["score"] = 75
    synthesis["routes"][0]["source_status"] = "candidate"
    synthesis_path.write_text(yaml.safe_dump(synthesis, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="promising or fragile"):
        validate_run(run_dir)


def test_strategy_rules_reject_high_score_with_unchecked_evidence(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    grade_path = run_dir / "idea_grade.yaml"
    grade = read_yaml(grade_path)
    grade["score"] = 70
    grade_path.write_text(yaml.safe_dump(grade, sort_keys=False), encoding="utf-8")

    synthesis_path = run_dir / "strategy_synthesis.yaml"
    synthesis = read_yaml(synthesis_path)
    synthesis["routes"][2]["score"] = 75
    synthesis["routes"][2]["failed_tests"] = ["test_special_vs_very_general"]
    synthesis["routes"][2]["estimated_effort"] = "normal_pass"
    synthesis_path.write_text(yaml.safe_dump(synthesis, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="unchecked example evidence"):
        validate_run(run_dir)


def test_strategy_rules_reject_marker_based_unchecked_deep_exploration(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    path = run_dir / "strategy_synthesis.yaml"
    data = read_yaml(path)
    data["routes"][1]["estimated_effort"] = "deep_exploration"
    data["routes"][1]["next_loop_plan"] = "Use human_needed support as the reason to go deep."
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="explicit unchecked or human_needed support"):
        validate_run(run_dir)


def test_strategy_brief_score_is_canonical_for_human_grading(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    path = run_dir / "strategy_brief.yaml"
    data = read_yaml(path)
    data["top_strategies"][0]["score"] = data["top_strategies"][0]["score"] - 1
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    validate_run(run_dir)


def test_strategy_brief_non_score_fields_must_match_strategy_synthesis(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    path = run_dir / "strategy_brief.yaml"
    data = read_yaml(path)
    data["top_strategies"][0]["main_risk"] = "Changed outside the synthesizer."
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="differs from Strategy Synthesis"):
        validate_run(run_dir)


def test_vscode_bridge_grade_strategy_updates_strategy_brief_only(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    brief_before = read_yaml(run_dir / "strategy_brief.yaml")
    synthesis_before = read_yaml(run_dir / "strategy_synthesis.yaml")
    strategy = brief_before["top_strategies"][0]
    new_score = max(0, strategy["score"] - 1)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/espresso_vscode_bridge.py",
            "grade-strategy",
            "--workspace",
            str(tmp_path),
            "--run",
            str(run_dir),
            "--target-id",
            strategy["id"],
            "--score",
            str(new_score),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["payload"]["score"] == new_score
    brief_after = read_yaml(run_dir / "strategy_brief.yaml")
    synthesis_after = read_yaml(run_dir / "strategy_synthesis.yaml")
    assert brief_after["top_strategies"][0]["score"] == new_score
    assert synthesis_after == synthesis_before
    validate_run(run_dir)


def test_vscode_bridge_choose_strategy_uses_validated_cli(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    strategy_id = read_yaml(run_dir / "strategy_brief.yaml")["top_strategies"][1]["id"]

    result = subprocess.run(
        [
            sys.executable,
            "scripts/espresso_vscode_bridge.py",
            "choose-strategy",
            "--workspace",
            str(tmp_path),
            "--run",
            str(run_dir),
            "--strategy-id",
            strategy_id,
            "--effort",
            "quick_test",
            "--decision",
            "continue",
            "--reason",
            "Use the validated bridge path for the human choice.",
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["payload"]["strategy_id"] == strategy_id
    assert read_yaml(run_dir / "human_choice.yaml")["selected_strategy"] == strategy_id
    assert (run_dir.parents[1] / "memory" / f"{run_dir.name}_human_choice.yaml").exists()
    validate_run(run_dir)


def test_strategy_brief_requires_human_choice_options_and_top_three_limit(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    path = run_dir / "strategy_brief.yaml"
    data = read_yaml(path)
    data["allowed_human_choices"] = [
        choice
        for choice in data["allowed_human_choices"]
        if choice["strategy_id"] != data["top_strategies"][0]["id"]
    ]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="missing human choice options"):
        validate_run(run_dir)

    data = read_yaml(path)
    data["allowed_human_choices"].append(
        {
            "strategy_id": data["top_strategies"][0]["id"],
            "effort_options": ["quick_test"],
        }
    )
    data["top_strategies"].append(data["top_strategies"][0])
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(KernelValidationError, match="at most three"):
        validate_run(run_dir)


def test_human_choice_effort_must_be_allowed_and_none_cannot_continue(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )
    brief_path = run_dir / "strategy_brief.yaml"
    brief = read_yaml(brief_path)
    selected = brief["top_strategies"][1]["id"]
    for choice in brief["allowed_human_choices"]:
        if choice["strategy_id"] == selected:
            choice["effort_options"] = ["quick_test"]
    brief_path.write_text(yaml.safe_dump(brief, sort_keys=False), encoding="utf-8")

    choice_path = run_dir / "human_choice.yaml"
    choice = read_yaml(choice_path)
    choice.update(
        {
            "status": "selected",
            "selected_strategy": selected,
            "effort": "long_watch",
            "taste_judgment": "Try a longer run anyway.",
            "continue_decision": "continue",
            "human_confirmed": True,
        }
    )
    choice_path.write_text(yaml.safe_dump(choice, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="is not allowed"):
        validate_run(run_dir)

    for choice_option in brief["allowed_human_choices"]:
        if choice_option["strategy_id"] == selected:
            choice_option["effort_options"] = ["quick_test", "normal_pass"]
    brief_path.write_text(yaml.safe_dump(brief, sort_keys=False), encoding="utf-8")
    choice.update(
        {
            "selected_strategy": "none",
            "effort": "quick_test",
            "continue_decision": "continue",
        }
    )
    choice_path.write_text(yaml.safe_dump(choice, sort_keys=False), encoding="utf-8")
    with pytest.raises(KernelValidationError, match="cannot continue"):
        validate_run(run_dir)


def test_human_feedback_unknown_target_is_rejected(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "human_marks_example_interesting" / "input.md",
        tmp_path,
    )
    path = run_dir / "human_example_feedback.yaml"
    data = read_yaml(path)
    data["feedback"][0]["target_id"] = "ex_not_real"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="unknown example target"):
        validate_run(run_dir)


def test_human_feedback_unconfirmed_item_is_rejected(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "human_marks_example_interesting" / "input.md",
        tmp_path,
    )
    path = run_dir / "human_example_feedback.yaml"
    data = read_yaml(path)
    data["feedback"][0]["human_confirmed"] = False
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="unconfirmed feedback"):
        validate_run(run_dir)


def test_human_feedback_summary_rejects_unknown_feedback_id(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "human_marks_example_interesting" / "input.md",
        tmp_path,
    )
    path = run_dir / "example_feedback_summary.yaml"
    data = read_yaml(path)
    data["summarized_from"] = ["feedback_not_real"]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    with pytest.raises(KernelValidationError, match="unknown feedback ids"):
        validate_run(run_dir)


def test_human_feedback_schema_rejects_evidence_status_fields() -> None:
    data = {
        "schema_version": 1,
        "project_id": "hodge_atoms",
        "claim_id": "claim_001",
        "run_id": "run_001",
        "created_at": "2026-05-28T12:00:00Z",
        "feedback": [
            {
                "feedback_id": "feedback_001",
                "target_type": "source_ref",
                "target_artifact": "source_grounding.yaml",
                "target_id": "src_candidate_001",
                "labels": ["needs_source"],
                "reason": "Needs an exact theorem locator.",
                "scope": "project",
                "requested_actions": ["run_referencing"],
                "human_confirmed": True,
                "source_backed": True,
            }
        ],
    }

    with pytest.raises(ValueError, match="Extra inputs"):
        HumanExampleFeedback.model_validate(data)


def _human_feedback_with_extra(extra: dict) -> dict:
    """A valid human feedback payload plus one forbidden extra field."""
    item = {
        "feedback_id": "feedback_001",
        "target_type": "source_ref",
        "target_artifact": "source_grounding.yaml",
        "target_id": "src_candidate_001",
        "labels": ["needs_source"],
        "reason": "Needs an exact theorem locator.",
        "scope": "project",
        "requested_actions": ["run_referencing"],
        "human_confirmed": True,
        **extra,
    }
    return {
        "schema_version": 1,
        "project_id": "hodge_atoms",
        "claim_id": "claim_001",
        "run_id": "run_001",
        "created_at": "2026-05-28T12:00:00Z",
        "feedback": [item],
    }


def test_human_feedback_schema_rejects_checked_status_fields() -> None:
    # Human taste cannot mark examples/computations checked (KERNEL_SAFETY_RULES
    # Human Feedback Rules): the feedback schema has no evidence-status slot.
    data = _human_feedback_with_extra({"evidence_status": "checked"})
    with pytest.raises(ValueError, match="Extra inputs"):
        HumanExampleFeedback.model_validate(data)


def test_human_feedback_schema_rejects_proof_status_field() -> None:
    # Human taste cannot upgrade proof status (KERNEL_SAFETY_RULES Human Feedback
    # Rules): the feedback schema has no proof-status slot.
    data = _human_feedback_with_extra({"proof_status": "proof_complete"})
    with pytest.raises(ValueError, match="Extra inputs"):
        HumanExampleFeedback.model_validate(data)


def _counterexample_test(**overrides) -> dict:
    """A valid CounterexampleTest payload; override fields to break it."""
    data = {
        "id": "cx_1",
        "operator_id": "op_specialize",
        "example_id": "ex_1",
        "satisfies_all_claim_hypotheses": True,
        "target": "theorem",
        "result": "survived",
        "refutes_claim": False,
        "refutes_step_only": False,
        "exposes_missing_hypothesis": False,
        "explanation": "A canonical test of the claim.",
    }
    data.update(overrides)
    return data


def test_counterexample_proof_step_attack_cannot_refute_claim() -> None:
    # A proof-step failure is not automatically a theorem refutation
    # (KERNEL_SAFETY_RULES Example And Counterexample Rules).
    data = _counterexample_test(
        target="proof_step",
        result="failed",
        refutes_step_only=True,
        refutes_claim=True,
    )
    with pytest.raises(ValueError, match="proof-step attack cannot refute"):
        CounterexampleTest.model_validate(data)


def test_counterexample_dropped_hypothesis_cannot_refute_claim() -> None:
    # Dropping a claim hypothesis cannot refute the original claim
    # (KERNEL_SAFETY_RULES Example And Counterexample Rules), enforced at the
    # model level, not only cross-artifact.
    data = _counterexample_test(
        satisfies_all_claim_hypotheses=False,
        result="failed",
        refutes_claim=True,
    )
    with pytest.raises(ValueError, match="dropped-hypothesis tests cannot refute"):
        CounterexampleTest.model_validate(data)


def test_counterexample_not_tested_cannot_refute_claim() -> None:
    # An asserted-but-UNCHECKED counterexample (result=not_tested) cannot carry a
    # refutation, even when it satisfies all claim hypotheses. refutes_claim
    # requires a checked 'failed' result. (Dogfood: order-6 "abelian" run, where a
    # genuine S_3 counterexample is asserted from background math but not machine-
    # checked; the kernel must enforce non-refutation, not rely on agent behavior.)
    data = _counterexample_test(
        satisfies_all_claim_hypotheses=True,
        result="not_tested",
        refutes_claim=True,
    )
    with pytest.raises(ValueError, match="can refute the claim only with a checked"):
        CounterexampleTest.model_validate(data)


def test_counterexample_inconclusive_cannot_refute_claim() -> None:
    # An inconclusive test is likewise not a refutation.
    data = _counterexample_test(
        satisfies_all_claim_hypotheses=True,
        result="inconclusive",
        refutes_claim=True,
    )
    with pytest.raises(ValueError, match="can refute the claim only with a checked"):
        CounterexampleTest.model_validate(data)


def test_counterexample_failed_all_hypotheses_may_refute() -> None:
    # The positive case still works: a checked 'failed' all-hypotheses
    # counterexample is allowed to refute (this is the only honest refutation).
    test = CounterexampleTest.model_validate(
        _counterexample_test(
            satisfies_all_claim_hypotheses=True,
            result="failed",
            refutes_claim=True,
        )
    )
    assert test.refutes_claim is True
    assert test.result.value == "failed"


def test_counterexample_accepts_optional_support_fields() -> None:
    # The optional missing_hypothesis / support fields are additive and accepted.
    test = CounterexampleTest.model_validate(
        _counterexample_test(
            target="hypothesis",
            exposes_missing_hypothesis=True,
            missing_hypothesis="projectivity",
            support="cited computation in source S1",
        )
    )
    assert test.missing_hypothesis == "projectivity"
    assert test.support == "cited computation in source S1"


def test_human_choice_schema_cannot_override_applicability() -> None:
    # Human preference cannot override theorem applicability / source status
    # (KERNEL_SAFETY_RULES Human Feedback Rules): HumanChoice has no slot to
    # set applicability or source status, so any such field is rejected.
    data = {
        "claim_id": "claim_001",
        "status": "pending",
        "continue_decision": "pending",
        "applicability_status": "applicable",
    }
    with pytest.raises(ValueError, match="Extra inputs"):
        HumanChoice.model_validate(data)


def test_feedback_example_cli_writes_valid_optional_artifact(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )

    result = CliRunner().invoke(
        app,
        [
            "feedback-example",
            "--run",
            str(run_dir),
            "--target",
            "ex_special_cubic",
            "--label",
            "dangerous",
            "--reason",
            "This is the first example I would test.",
        ],
    )

    assert result.exit_code == 0, result.output
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["human_example_feedback"] == "human_example_feedback.yaml"
    feedback = HumanExampleFeedback.model_validate(read_yaml(run_dir / "human_example_feedback.yaml"))
    assert feedback.feedback[0].feedback_id == "feedback_001"
    assert feedback.feedback[0].target_id == "ex_special_cubic"
    validate_run(run_dir)


def test_feedback_example_cli_rejects_unknown_target_without_writing(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )

    result = CliRunner().invoke(
        app,
        [
            "feedback-example",
            "--run",
            str(run_dir),
            "--target",
            "ex_not_real",
            "--label",
            "interesting",
            "--reason",
            "Invalid target.",
        ],
    )

    assert result.exit_code != 0
    assert not (run_dir / "human_example_feedback.yaml").exists()
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "human_example_feedback" not in manifest.get("optional_artifacts", {})


def test_summarize_feedback_cli_writes_ranking_only_summary(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    feedback_result = CliRunner().invoke(
        app,
        [
            "feedback-example",
            "--run",
            str(run_dir),
            "--target",
            "ex_special_cubic",
            "--label",
            "too_easy",
            "--reason",
            "This only checks a toy dimension fact.",
        ],
    )
    assert feedback_result.exit_code == 0, feedback_result.output

    result = CliRunner().invoke(app, ["summarize-feedback", "--run", str(run_dir)])

    assert result.exit_code == 0, result.output
    summary = ExampleFeedbackSummary.model_validate(read_yaml(run_dir / "example_feedback_summary.yaml"))
    assert summary.ranking_adjustments[0].direction.value == "down"
    assert summary.ranking_adjustments[0].feedback_ids == ["feedback_001"]
    validate_run(run_dir)


def test_generate_agent_cli_writes_admission_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Study KKPY Hodge atoms for cubic fourfold mirror symmetry.", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["generate-agent", "--mode", "v0", "--prompt", str(prompt), "--project", "generated_hodge"],
    )

    assert result.exit_code == 0, result.output
    project_dir = tmp_path / "projects" / "generated_hodge"
    for relative in [
        "workflow_spec.yaml",
        "playbook.md",
        "agent_contracts.yaml",
        "admission_check.yaml",
        "trial_fixture/input.md",
        "trial_fixture/expected.yaml",
    ]:
        assert (project_dir / relative).exists()
    WorkflowSpec.model_validate(read_yaml(project_dir / "workflow_spec.yaml"))
    AgentContracts.model_validate(read_yaml(project_dir / "agent_contracts.yaml"))
    admission = AdmissionCheck.model_validate(read_yaml(project_dir / "admission_check.yaml"))
    assert admission.admitted is False
    assert admission.human_confirmed is False
    assert admission.blockers


def test_admission_check_rejects_admitted_with_blockers_or_missing_checks() -> None:
    with pytest.raises(ValueError, match="requires all admission checks"):
        AdmissionCheck.model_validate(
            {
                "project_id": "bad",
                "workflow_spec_valid": True,
                "agent_contracts_exist": True,
                "required_artifacts_declared": True,
                "trial_fixture_exists": True,
                "human_confirmed": False,
                "admitted": True,
                "blockers": [],
            }
        )
    with pytest.raises(ValueError, match="cannot have blockers"):
        AdmissionCheck.model_validate(
            {
                "project_id": "bad",
                "workflow_spec_valid": True,
                "agent_contracts_exist": True,
                "required_artifacts_declared": True,
                "trial_fixture_exists": True,
                "human_confirmed": True,
                "admitted": True,
                "blockers": ["still needs review"],
            }
        )


def test_classify_failures_routes_typed_categories(tmp_path: Path) -> None:
    # Step 5 (Failure Classifier): reviewer/source/example signals become typed,
    # advisory routing categories; an unbacked claim_refuted is never emitted.
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    result = CliRunner().invoke(app, ["classify-failures", "--run", str(run_dir)])
    assert result.exit_code == 0, result.output
    report = read_yaml(run_dir / "failure_classification.yaml")
    categories = {item["category"] for item in report["classifications"]}
    assert "source_gap" in categories
    assert "strategy_failed_by_example" in categories
    assert "claim_refuted" not in categories  # no admissible counterexample in this run
    for item in report["classifications"]:
        assert set(item) == {"category", "signal", "recommended_route", "explanation"}


def test_classify_failures_claim_refuted_requires_admissible_counterexample(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "refuted_by_all_hypotheses_counterexample" / "input.md",
        tmp_path,
    )
    result = CliRunner().invoke(app, ["classify-failures", "--run", str(run_dir)])
    assert result.exit_code == 0, result.output
    categories = {
        item["category"]
        for item in read_yaml(run_dir / "failure_classification.yaml")["classifications"]
    }
    assert "claim_refuted" in categories  # admissible all-hypotheses refutation
    validate_run(run_dir)  # accepted


def test_failure_classification_rejects_unbacked_claim_refuted(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim_id = read_yaml(run_dir / "idea_grade.yaml")["claim_id"]
    report = {
        "schema_version": 1,
        "claim_id": claim_id,
        "run_id": manifest["run_id"],
        "generated_at": "2026-06-03T00:00:00+00:00",
        "classifications": [
            {
                "category": "claim_refuted",
                "signal": "fabricated",
                "recommended_route": "stop",
                "explanation": "should be rejected: no admissible counterexample",
            }
        ],
        "notes": [],
    }
    (run_dir / "failure_classification.yaml").write_text(yaml.safe_dump(report), encoding="utf-8")
    manifest.setdefault("optional_artifacts", {})["failure_classification"] = (
        "failure_classification.yaml"
    )
    (run_dir / "run_manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(KernelValidationError, match="admissible all-hypotheses counterexample"):
        validate_run(run_dir)


def test_failure_classification_report_rejects_status_field() -> None:
    from espresso_proof.models import FailureClassificationReport

    data = {
        "schema_version": 1,
        "claim_id": "claim_001",
        "run_id": "run_001",
        "generated_at": "2026-06-03T00:00:00+00:00",
        "classifications": [],
        "source_backed": True,
    }
    with pytest.raises(ValueError, match="Extra inputs"):
        FailureClassificationReport.model_validate(data)


def test_classify_failures_emits_budget_exhausted(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    loop_path = run_dir / "loop_control.yaml"
    loop = read_yaml(loop_path)
    loop["budget_exhausted"] = True
    loop["should_stop"] = True
    loop["stop_reasons"] = ["budget exhausted"]
    loop["next_action"] = "stop"
    loop_path.write_text(yaml.safe_dump(loop), encoding="utf-8")

    from espresso_proof.failure_classifier import build_failure_classification

    report = build_failure_classification(run_dir)
    categories = {item["category"] for item in report["classifications"]}
    assert "budget_exhausted" in categories


def test_classify_failures_emits_no_progress(tmp_path: Path) -> None:
    run_dir = run_mock_critique(
        "hodge_atoms",
        FIXTURES / "hodge_all_cubic_fourfolds" / "input.md",
        tmp_path,
    )
    loop_path = run_dir / "loop_control.yaml"
    loop = read_yaml(loop_path)
    loop["no_new_counterexamples_cycles"] = 2
    loop["no_new_references_cycles"] = 2
    loop["grade_delta_last_two"] = 0
    loop["should_stop"] = True
    loop["stop_reasons"] = ["no progress"]
    loop["next_action"] = "ask_human"
    loop_path.write_text(yaml.safe_dump(loop), encoding="utf-8")

    from espresso_proof.failure_classifier import build_failure_classification

    report = build_failure_classification(run_dir)
    categories = {item["category"] for item in report["classifications"]}
    assert "no_progress" in categories


# ---------------------------------------------------------------------------
# Goal 21: refuted is a kernel-earned status (admissible checked counterexample)
# ---------------------------------------------------------------------------
def _refuted_run(tmp_path: Path) -> Path:
    return run_mock_critique(
        "hodge_atoms",
        FIXTURES / "refuted_by_all_hypotheses_counterexample" / "input.md",
        tmp_path,
    )


def test_refuted_grade_with_admissible_counterexample_is_valid(tmp_path: Path) -> None:
    run_dir = _refuted_run(tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    assert read_yaml(run_dir / artifacts["idea_grade"])["status"] == "refuted"
    tests = read_yaml(run_dir / artifacts["counterexamples"])["tests"]
    assert any(t["satisfies_all_claim_hypotheses"] is True and t["refutes_claim"] for t in tests)
    validate_run(run_dir)  # honest refutation validates


def test_refuted_grade_without_admissible_counterexample_rejected(tmp_path: Path) -> None:
    # Strip admissibility from every counterexample (now non-refuting / survived),
    # leaving idea_grade.status=refuted. The kernel must reject: refuted requires
    # an admissible checked counterexample, not just a negative grade.
    run_dir = _refuted_run(tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    ce = read_yaml(run_dir / artifacts["counterexamples"])
    for test in ce["tests"]:
        test["refutes_claim"] = False
        test["result"] = "survived"
    write_yaml(run_dir / artifacts["counterexamples"], ce)
    with pytest.raises(KernelValidationError, match="admissible checked counterexample"):
        validate_run(run_dir)


def test_subgoal_contradicted_cannot_stand_on_unbacked_refuted_grade(tmp_path: Path) -> None:
    # The subgoal-graph `contradicted` gate trusts idea_grade.status==refuted; with
    # the grade itself now requiring an admissible checked counterexample, a
    # refuted grade cannot be sustained once the refutation is removed -- so a
    # contradicted subgoal can never stand on a forged/unbacked refuted grade.
    from espresso_proof.subgoal_graph import build_subgoal_graph

    run_dir = _refuted_run(tmp_path)
    build_subgoal_graph(run_dir)  # builds + re-validates while the refutation is real
    validate_run(run_dir)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    ce = read_yaml(run_dir / artifacts["counterexamples"])
    for test in ce["tests"]:
        test["refutes_claim"] = False
        test["result"] = "survived"
    write_yaml(run_dir / artifacts["counterexamples"], ce)
    with pytest.raises(KernelValidationError, match="admissible checked counterexample"):
        validate_run(run_dir)
