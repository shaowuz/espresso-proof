from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from espresso_proof.cli import app
from espresso_proof.agents.front_half_agents import run_front_half_agent_pass
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.memory.memory import (
    load_memory_index,
    summarize_memory,
    write_memory_entry,
    write_run_memory,
)
from espresso_proof.memory.memory_context import build_memory_context
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")


class UnsafeGradeClient:
    provider = "unsafe-grade"
    model = "unsafe-grade"

    def complete_json(self, *, system: str, prompt: str) -> dict:
        return {
            "reasons": ["Keep source gaps explicit."],
            "missing_hypotheses": [],
            "source_gaps": ["Need exact locator."],
            "repair_options": [],
            "next_cheapest_test": "The theorem applies to this step.",
        }


def _run(tmp_path: Path) -> Path:
    return run_mock_critique(
        "memory_v2",
        FIXTURES / "promising_but_unproved" / "input.md",
        tmp_path,
    )


def _project(run_dir: Path) -> Path:
    return run_dir.parents[1]


def _claim_id(run_dir: Path) -> str:
    return str(read_yaml(run_dir / "locked_claim.yaml")["id"])


def _entry(run_dir: Path, **overrides) -> dict:
    data = {
        "id": "mem_safe",
        "timestamp": "2026-06-15T00:00:00+00:00",
        "project": _project(run_dir).name,
        "type": "source_gap",
        "claim_id": _claim_id(run_dir),
        "summary": "A prior run requested an exact source locator.",
        "provenance": [
            f"projects/{_project(run_dir).name}/runs/{run_dir.name}/idea_grade.yaml"
        ],
        "reusable_lesson": "request exact source support before ranking this route",
    }
    data.update(overrides)
    return data


REVIEWER_STATUS_ASSERTIONS = [
    "No counterexample refutes the claim.",
    "No examples failed.",
    "Example alpha survived.",
    "This source supports the claim.",
    "The theorem is applicable to this step.",
    "The applicability status: applicable.",
    "theorem-applies: true",
    "proof status is complete.",
    "proof language status is source checked argument.",
    "grade is promising.",
]


NEGATED_STATUS_ASSERTIONS = [
    "Examples did not fail.",
    "The examples did not fail.",
    "Counterexample search did not find pressure.",
    "The counterexample search found no pressure.",
    "This route was not refuted.",
    "The claim was not refuted.",
    "The theorem was not refuted.",
    "No refutation remains for the claim.",
    "The claim is unrefuted.",
]


PROOF_AND_SOURCE_STATUS_ASSERTIONS = [
    "This establishes the theorem.",
    "The route is settled.",
    "The claim is settled.",
    "The theorem is settled.",
    "The route is supported by the cited paper.",
    "The claim is supported by the source.",
    "The theorem is supported by the reference.",
    "The candidate reference backs this step.",
    "The source backs this route.",
    "The proof is complete.",
    "The route has source support.",
    "This route is source supported.",
    "The theorem is applicable.",
    "The theorem applies here.",
    "This gives a proof of the theorem.",
    "The proof checks out.",
    "The source verifies the claim.",
    "The theorem applicable here.",
    "Applicable theorem for this step.",
    "The route is established.",
    "This is a complete argument.",
    "The examples pass.",
    "counterexample cx1: this refutes the claim",
    "No counterexample was found.",
    "The proof establishes the theorem.",
    "Smith theorem verifies this step.",
    "Human preferred route A as evidence.",
    "human preference shows route A works",
]


COMMON_STATUS_ASSERTION_VARIANTS = [
    "The theorem applies.",
    "The theorem applies for this route.",
    "All hypotheses are satisfied, so the theorem applies.",
    "The claim has been established.",
    "The route has been established.",
    "The paper supports the claim.",
    "The source confirms the claim.",
    "Example alpha passes.",
    "Example alpha does not fail.",
    "No failing examples.",
    "Examples verify the claim.",
    "No counterexamples were found.",
    "Counterexample search found none.",
    "The claim survived counterexample search.",
]


KERNEL_BLOCK_STATUS_ASSERTIONS = [
    "The claim is false.",
    "This disproves the theorem.",
    "No proof gaps remain.",
    "Reviewer certified the proof.",
    "Prover completed the proof.",
    "The literature candidate supports the claim.",
    "The theorem supports the claim.",
    "This theorem gives the proof.",
    "Human preference supports the claim.",
    "Human chose route A, evidence for the theorem.",
    "Human marked the example interesting, so it is evidence.",
    "No counterexample was produced.",
    "No counterexample appeared.",
    "The search produced no counterexample.",
    "The run found zero counterexamples.",
    "Literature candidate is source support.",
    "This route has reference support.",
    "Route A is proof of the theorem.",
    "Route A gives the proof.",
    "Counterexample search came back empty.",
    "Step s1 has source support.",
    "Theorem T applies to step s1.",
    "No counterexample to this claim was found.",
    "Reviewer R certified proof of step s1.",
    "Paper P supports proof step s1.",
    "The theorem holds.",
    "The proof is valid.",
    "Reference establishes step s1.",
    "The source validates this step.",
    "Reviewer approved the proof.",
    "Prover found a proof.",
    "The theorem covers this case.",
    "Empty counterexample search.",
    "The counterexample search was empty.",
    "Counterexample search returned empty.",
    "No obstruction was found.",
]


MIXED_DISCLAIMER_STATUS_ASSERTIONS = [
    "not merely source_backed, the theorem is true",
    "not just a candidate, the claim is true",
    "No source found and proof complete.",
    "This is not source backed and Q" "ED.",
    "not a checked proof and we proved the theorem.",
]


STATUS_ASSERTION_ALIASES = [
    "Source status is source backed for this route.",
    "No counterexamples remain for the claim.",
    "No counterexamples found for the claim.",
    "No counterexamples exist for the claim.",
    "applicability_status=applicable for this theorem.",
    "The theorem applies to this step.",
    *REVIEWER_STATUS_ASSERTIONS,
    *NEGATED_STATUS_ASSERTIONS,
    *PROOF_AND_SOURCE_STATUS_ASSERTIONS,
    *COMMON_STATUS_ASSERTION_VARIANTS,
    *KERNEL_BLOCK_STATUS_ASSERTIONS,
    *MIXED_DISCLAIMER_STATUS_ASSERTIONS,
]


@pytest.mark.parametrize(
    "summary",
    STATUS_ASSERTION_ALIASES,
)
def test_memory_entry_rejects_status_assertion_aliases(
    tmp_path: Path, summary: str
) -> None:
    run_dir = _run(tmp_path)

    with pytest.raises(ValueError, match="current mathematical status"):
        write_memory_entry(
            _project(run_dir),
            _entry(run_dir, id=f"mem_alias_{abs(hash(summary))}", summary=summary),
        )


@pytest.mark.parametrize(
    "summary",
    [
        "request exact source support before ranking this route",
        "not source-backed; rerun source validation before use",
        "not a checked proof; keep this as an advisory memory item",
    ],
)
def test_memory_entry_allows_advisory_and_disclaimer_language(
    tmp_path: Path, summary: str
) -> None:
    run_dir = _run(tmp_path)

    write_memory_entry(
        _project(run_dir),
        _entry(run_dir, id=f"mem_advisory_{abs(hash(summary))}", summary=summary),
    )


def test_memory_source_candidate_cannot_become_source_backed(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)

    with pytest.raises(ValueError, match="current mathematical status"):
        write_memory_entry(
            _project(run_dir),
            _entry(run_dir, summary="Memory says this candidate is source_backed."),
        )


def test_memory_example_cannot_become_checked_evidence(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)

    with pytest.raises(ValueError, match="current mathematical status"):
        write_memory_entry(
            _project(run_dir),
            _entry(
                run_dir,
                id="mem_example_checked",
                type="tested_example",
                summary="Example e1 survived and is checked evidence.",
            ),
        )


def test_human_preference_memory_cannot_become_evidence(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)

    with pytest.raises(ValueError, match="current mathematical status"):
        write_memory_entry(
            _project(run_dir),
            _entry(
                run_dir,
                id="mem_human_evidence",
                type="user_decision",
                summary="Human preferred this route; checked evidence supports it.",
                reusable_lesson="human preference is checked evidence",
            ),
        )


@pytest.mark.parametrize(
    ("entry_type", "summary"),
    [
        ("user_decision", "Human preference supports the claim."),
        ("user_decision", "Human chose route A, evidence for the theorem."),
        (
            "human_example_feedback",
            "Human marked the example interesting, so it is evidence.",
        ),
    ],
)
def test_human_memory_types_cannot_phrase_feedback_as_evidence(
    tmp_path: Path, entry_type: str, summary: str
) -> None:
    run_dir = _run(tmp_path)

    with pytest.raises(ValueError, match="current mathematical status"):
        write_memory_entry(
            _project(run_dir),
            _entry(
                run_dir,
                id=f"mem_human_status_{abs(hash(summary))}",
                type=entry_type,
                summary=summary,
                reusable_lesson="human feedback is evidence for this route",
            ),
        )


@pytest.mark.parametrize(
    "summary",
    [
        "The prior run proved the theorem and Q" "ED.",
        "The theorem applicability status is applicable.",
        "The current theorem status is verified.",
        "The route is refuted.",
    ],
)
def test_memory_proof_complete_or_current_theorem_status_rejected(
    tmp_path: Path, summary: str
) -> None:
    run_dir = _run(tmp_path)

    with pytest.raises(ValueError, match="current mathematical status"):
        write_memory_entry(
            _project(run_dir),
            _entry(run_dir, id=f"mem_{abs(hash(summary))}", summary=summary),
        )


def test_memory_replay_under_new_locked_claim_requires_revalidation(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    write_memory_entry(
        _project(run_dir),
        _entry(
            run_dir,
            id="mem_other_claim",
            claim_id="prior_claim_999",
            summary=(
                "Prior run requested an exact source locator for "
                f"{read_yaml(run_dir / 'locked_claim.yaml')['normalized_statement']}."
            ),
        ),
    )

    ctx = build_memory_context(run_dir)
    match = next(entry for entry in ctx["matched_entries"] if entry["id"] == "mem_other_claim")
    assert match["memory_claim_id"] == "prior_claim_999"
    assert match["recommended_use"] == "note"
    assert "pending revalidation" in match["relevance_reason"]
    assert match["revalidation_required"] is True
    assert match["revalidated_against_locked_claim"] is False
    validate_run(run_dir)

    raw = read_yaml(run_dir / "memory_context.yaml")
    raw["matched_entries"][0]["revalidation_required"] = False
    write_yaml(run_dir / "memory_context.yaml", raw)
    with pytest.raises(KernelValidationError, match="requires revalidation"):
        validate_run(run_dir)


def test_legacy_memory_context_with_matched_entries_validates_as_pending_notes(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    write_memory_entry(
        _project(run_dir),
        _entry(run_dir, id="mem_legacy_context"),
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


def test_legacy_memory_context_status_alias_rejected_after_adaptation(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    write_memory_entry(
        _project(run_dir),
        _entry(run_dir, id="mem_legacy_context_status"),
    )
    build_memory_context(run_dir)
    raw = read_yaml(run_dir / "memory_context.yaml")
    raw["schema_version"] = 1
    raw["matched_entries"][0]["summary"] = "Route A gives the proof."
    for entry in raw["matched_entries"]:
        entry.pop("memory_claim_id", None)
        entry.pop("provenance", None)
        entry.pop("revalidation_required", None)
        entry.pop("revalidated_against_locked_claim", None)
    write_yaml(run_dir / "memory_context.yaml", raw)

    with pytest.raises(KernelValidationError, match="schema validation failed"):
        validate_run(run_dir)


def test_cross_claim_memory_schema_downgrade_renders_only_pending_notes(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    write_memory_entry(
        _project(run_dir),
        _entry(
            run_dir,
            id="mem_cross_claim_downgrade",
            claim_id="prior_claim_998",
            summary=(
                "Prior run requested an exact source locator for "
                f"{read_yaml(run_dir / 'locked_claim.yaml')['normalized_statement']}."
            ),
        ),
    )
    build_memory_context(run_dir)
    raw = read_yaml(run_dir / "memory_context.yaml")
    raw["schema_version"] = 1
    raw["matched_entries"][0]["revalidation_required"] = False
    write_yaml(run_dir / "memory_context.yaml", raw)

    validate_run(run_dir)
    memo = build_concise_memo(run_dir)
    assert "[pending revalidation; no action]" in memo
    assert "[request source]" not in memo


def test_cross_claim_pending_memory_context_must_be_non_actionable(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    write_memory_entry(
        _project(run_dir),
        _entry(
            run_dir,
            id="mem_cross_claim_actionable",
            claim_id="prior_claim_997",
            summary=(
                "Prior run requested an exact source locator for "
                f"{read_yaml(run_dir / 'locked_claim.yaml')['normalized_statement']}."
            ),
        ),
    )
    build_memory_context(run_dir)
    raw = read_yaml(run_dir / "memory_context.yaml")
    raw["matched_entries"][0]["recommended_use"] = "request_source"
    write_yaml(run_dir / "memory_context.yaml", raw)

    with pytest.raises(KernelValidationError, match="non-actionable"):
        validate_run(run_dir)


def test_memory_context_entry_missing_provenance_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    write_memory_entry(_project(run_dir), _entry(run_dir, id="mem_no_context_provenance"))
    build_memory_context(run_dir)
    raw = read_yaml(run_dir / "memory_context.yaml")
    raw["matched_entries"][0]["provenance"] = []
    write_yaml(run_dir / "memory_context.yaml", raw)

    with pytest.raises(KernelValidationError, match="requires provenance"):
        validate_run(run_dir)


def test_memory_context_entry_non_run_provenance_is_rejected(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    write_memory_entry(_project(run_dir), _entry(run_dir, id="mem_bad_context_provenance"))
    build_memory_context(run_dir)
    raw = read_yaml(run_dir / "memory_context.yaml")
    raw["matched_entries"][0]["provenance"] = ["not-a-run-artifact"]
    write_yaml(run_dir / "memory_context.yaml", raw)

    with pytest.raises(KernelValidationError, match="run artifact reference"):
        validate_run(run_dir)


def test_memory_context_constraints_cannot_store_status_assertions(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    write_memory_entry(_project(run_dir), _entry(run_dir, id="mem_constraint_status"))
    build_memory_context(run_dir)
    raw = read_yaml(run_dir / "memory_context.yaml")
    raw["constraints"].append("proof status is complete.")
    write_yaml(run_dir / "memory_context.yaml", raw)

    with pytest.raises(KernelValidationError, match="schema validation failed"):
        validate_run(run_dir)


def test_legacy_generated_memory_context_constraints_remain_compatible(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    write_memory_entry(_project(run_dir), _entry(run_dir, id="mem_legacy_constraints"))
    build_memory_context(run_dir)
    raw = read_yaml(run_dir / "memory_context.yaml")
    raw["constraints"] = [
        "advisory only; memory never sets source_backed / checked evidence / "
        "proof status / claim refutation, and never promotes a failure-pattern tier",
        "memory only influences route ranking, time allocation, and what to test "
        "first; the kernel validators remain the only gate that earns any status",
        "memory never overrides a current-run artifact",
    ]
    write_yaml(run_dir / "memory_context.yaml", raw)

    validate_run(run_dir)


def test_memory_summary_moving_examples_to_survived_or_failed_is_rejected(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)

    with pytest.raises(ValueError, match="current mathematical status"):
        write_memory_entry(
            _project(run_dir),
            _entry(
                run_dir,
                id="mem_example_survived",
                type="tested_example",
                summary="Example alpha survived; failed examples are evidence.",
            ),
        )

    assert "Example alpha survived" not in summarize_memory(
        _project(run_dir), claim_text="group order p squared"
    )


def test_run_memory_sanitizes_reviewer_objection_status_text(
    tmp_path: Path,
) -> None:
    run_dir = run_mock_critique(
        "memory_v2_counter",
        FIXTURES / "refuted_by_all_hypotheses_counterexample" / "input.md",
        tmp_path,
    )
    summary = summarize_memory(run_dir.parents[1], claim_text="integer prime parity")

    assert "Reviewer objections" in summary
    assert "reviewer objection recorded" in summary
    assert "checked example" not in summary.lower()
    assert "refutes" not in summary.lower()
    assert "refuted" not in summary.lower()


def test_legacy_memory_index_with_old_provenance_remains_readable(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "projects" / "legacy_memory_project"
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True)
    write_yaml(
        memory_dir / "index.yaml",
        {
            "project_id": "legacy_memory_project",
            "last_updated": "2026-06-15T00:00:00+00:00",
            "lookup_keys": [],
            "entries": [
                {
                    "id": "legacy_safe",
                    "entry_path": "missing_entry.yaml",
                    "claim_id": "legacy_claim",
                    "type": "source_gap",
                    "tags": ["legacy"],
                    "summary": "prior run requested exact locator",
                    "provenance": ["run:demo"],
                },
                {
                    "id": "legacy_status",
                    "entry_path": "missing_status.yaml",
                    "claim_id": "legacy_claim",
                    "type": "source_gap",
                    "tags": [],
                    "summary": "proof status is complete",
                    "provenance": ["run:demo"],
                },
            ],
        },
    )

    summary = summarize_memory(project_dir, claim_text="prior exact locator")

    assert "legacy_safe" in summary
    assert "projects/legacy_memory_project/runs/__legacy_memory_index__/index.yaml" in summary
    assert "legacy_status" not in summary


def test_legacy_memory_index_with_run_shaped_status_text_is_quarantined(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "projects" / "legacy_run_shape_project"
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True)
    write_yaml(
        memory_dir / "index.yaml",
        {
            "project_id": "legacy_run_shape_project",
            "last_updated": "2026-06-15T00:00:00+00:00",
            "lookup_keys": [],
            "entries": [
                {
                    "id": "legacy_safe_run_shape",
                    "entry_path": "missing_safe.yaml",
                    "claim_id": "legacy_claim",
                    "type": "source_gap",
                    "tags": ["legacy"],
                    "summary": "prior run requested exact locator",
                    "provenance": [
                        "projects/legacy_run_shape_project/runs/run_old/idea_grade.yaml"
                    ],
                },
                {
                    "id": "legacy_counterexample_overclaim",
                    "entry_path": "missing_counterexample.yaml",
                    "claim_id": "legacy_claim",
                    "type": "counterexample",
                    "tags": [],
                    "summary": "counterexample cx1: this refutes the claim",
                    "provenance": [
                        "projects/legacy_run_shape_project/runs/run_old/counterexamples.yaml"
                    ],
                },
                {
                    "id": "legacy_reviewer_overclaim",
                    "entry_path": "missing_reviewer.yaml",
                    "claim_id": "legacy_claim",
                    "type": "reviewer_objection",
                    "tags": [],
                    "summary": "reviewer certified the proof",
                    "provenance": [
                        "projects/legacy_run_shape_project/runs/run_old/review.yaml"
                    ],
                },
                {
                    "id": "legacy_strategy_title_overclaim",
                    "entry_path": "missing_strategy.yaml",
                    "claim_id": "legacy_claim",
                    "type": "failed_attempt",
                    "tags": [],
                    "summary": "grade score 68; top strategy: The theorem applies here",
                    "provenance": [
                        "projects/legacy_run_shape_project/runs/run_old/strategy_brief.yaml"
                    ],
                },
            ],
        },
    )

    index = load_memory_index(project_dir)
    summary = summarize_memory(project_dir, claim_text="prior exact locator")

    assert {entry.id for entry in index.entries} == {"legacy_safe_run_shape"}
    assert "legacy_safe_run_shape" in summary
    assert "legacy_counterexample_overclaim" not in summary
    assert "legacy_reviewer_overclaim" not in summary
    assert "legacy_strategy_title_overclaim" not in summary


def test_legacy_memory_index_with_old_provenance_remains_appendable(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "projects" / "legacy_append_project"
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True)
    write_yaml(
        memory_dir / "index.yaml",
        {
            "project_id": "legacy_append_project",
            "last_updated": "2026-06-15T00:00:00+00:00",
            "lookup_keys": [],
            "entries": [
                {
                    "id": "legacy_safe",
                    "entry_path": "missing_entry.yaml",
                    "claim_id": "legacy_claim",
                    "type": "source_gap",
                    "tags": ["legacy"],
                    "summary": "prior run requested exact locator",
                    "provenance": ["run:demo"],
                },
            ],
        },
    )
    run_dir = tmp_path / "projects" / "legacy_append_project" / "runs" / "run_new"

    write_memory_entry(
        project_dir,
        {
            "id": "new_safe",
            "timestamp": "2026-06-15T00:00:00+00:00",
            "project": "legacy_append_project",
            "type": "source_gap",
            "claim_id": "new_claim",
            "summary": "A current run requested an exact source locator.",
            "provenance": [
                f"projects/legacy_append_project/runs/{run_dir.name}/idea_grade.yaml"
            ],
            "reusable_lesson": "request exact source support before ranking this route",
        },
    )

    index = load_memory_index(project_dir)
    assert {entry.id for entry in index.entries} == {"legacy_safe", "new_safe"}


def test_partial_memory_index_fails_and_is_not_overwritten(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "projects" / "partial_index_project"
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True)
    index_path = memory_dir / "index.yaml"
    write_yaml(
        index_path,
        {
            "project_id": "partial_index_project",
            "last_updated": "2026-06-15T00:00:00+00:00",
            "entries": [{"id": "partial"}],
        },
    )
    before_index = index_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="malformed memory index entry"):
        load_memory_index(project_dir)
    with pytest.raises(ValueError, match="malformed memory index entry"):
        write_memory_entry(
            project_dir,
            {
                "id": "new_safe",
                "timestamp": "2026-06-15T00:00:00+00:00",
                "project": "partial_index_project",
                "type": "source_gap",
                "claim_id": "new_claim",
                "summary": "A current run requested an exact source locator.",
                "provenance": [
                    "projects/partial_index_project/runs/run_new/idea_grade.yaml"
                ],
                "reusable_lesson": "request exact source support before ranking this route",
            },
        )

    assert index_path.read_text(encoding="utf-8") == before_index
    assert not (memory_dir / "new_safe.yaml").exists()


def test_memory_index_tag_validation_failure_does_not_write_entry_or_index(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "projects" / "bad_tag_project"
    memory_dir = project_dir / "memory"
    entry = {
        "id": "new_safe",
        "timestamp": "2026-06-15T00:00:00+00:00",
        "project": "bad_tag_project",
        "type": "source_gap",
        "claim_id": "new_claim",
        "summary": "A current run requested an exact source locator.",
        "provenance": [
            "projects/bad_tag_project/runs/run_new/idea_grade.yaml"
        ],
        "reusable_lesson": "request exact source support before ranking this route",
    }

    with pytest.raises(ValueError, match="current mathematical status"):
        write_memory_entry(project_dir, entry, tags=["proof_status"])

    assert not (memory_dir / "new_safe.yaml").exists()
    assert not (memory_dir / "index.yaml").exists()


def test_choose_strategy_with_legacy_bad_memory_index_does_not_partial_fail(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    memory_dir = _project(run_dir) / "memory"
    write_yaml(
        memory_dir / "index.yaml",
        {
            "project_id": _project(run_dir).name,
            "last_updated": "2026-06-15T00:00:00+00:00",
            "lookup_keys": [],
            "entries": [
                {
                    "id": "legacy_counterexample_overclaim",
                    "entry_path": "missing_counterexample.yaml",
                    "claim_id": _claim_id(run_dir),
                    "type": "counterexample",
                    "tags": [],
                    "summary": "counterexample cx1: this refutes the claim",
                    "provenance": [
                        f"projects/{_project(run_dir).name}/runs/{run_dir.name}/counterexamples.yaml"
                    ],
                }
            ],
        },
    )
    before_choice = read_yaml(run_dir / "human_choice.yaml")
    before_manifest = read_yaml(run_dir / "run_manifest.yaml")

    result = CliRunner().invoke(
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
            "Pin exact locator before spending more time.",
        ],
    )

    assert result.exit_code == 0, result.output
    assert read_yaml(run_dir / "human_choice.yaml") != before_choice
    assert read_yaml(run_dir / "run_manifest.yaml") != before_manifest
    index = load_memory_index(_project(run_dir))
    assert "legacy_counterexample_overclaim" not in {entry.id for entry in index.entries}
    assert any(entry.id.startswith("user_decision_") for entry in index.entries)


def test_choose_strategy_rejects_unsafe_memory_reason_without_artifact_mutation(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    before_choice = read_yaml(run_dir / "human_choice.yaml")
    before_manifest = read_yaml(run_dir / "run_manifest.yaml")

    result = CliRunner().invoke(
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
            "proof status is complete.",
        ],
    )

    assert result.exit_code != 0
    assert read_yaml(run_dir / "human_choice.yaml") == before_choice
    assert read_yaml(run_dir / "run_manifest.yaml") == before_manifest
    assert not any((run_dir.parents[1] / "memory").glob("*human_choice*.yaml"))


def test_choose_strategy_rejects_human_preference_as_evidence_without_mutation(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    before_choice = read_yaml(run_dir / "human_choice.yaml")
    before_manifest = read_yaml(run_dir / "run_manifest.yaml")

    result = CliRunner().invoke(
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
            "human preference shows this route works",
        ],
    )

    assert result.exit_code != 0
    assert read_yaml(run_dir / "human_choice.yaml") == before_choice
    assert read_yaml(run_dir / "run_manifest.yaml") == before_manifest
    assert not any((run_dir.parents[1] / "memory").glob("*human_choice*.yaml"))


def test_choose_strategy_memory_omits_status_like_strategy_title(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    brief = read_yaml(run_dir / "strategy_brief.yaml")
    strategy_id = brief["top_strategies"][0]["id"]
    brief["top_strategies"][0]["title"] = "The theorem applies here"
    write_yaml(run_dir / "strategy_brief.yaml", brief)
    synthesis = read_yaml(run_dir / "strategy_synthesis.yaml")
    for item in synthesis.get("routes", []):
        if item.get("id") == strategy_id:
            item["title"] = "The theorem applies here"
    write_yaml(run_dir / "strategy_synthesis.yaml", synthesis)

    result = CliRunner().invoke(
        app,
        [
            "choose-strategy",
            "--run",
            str(run_dir),
            "--strategy",
            strategy_id,
            "--effort",
            "quick_test",
            "--decision",
            "continue",
            "--reason",
            "Pin exact locator before spending more time.",
        ],
    )

    assert result.exit_code == 0, result.output
    memory = summarize_memory(run_dir.parents[1], claim_text=strategy_id.replace("_", " "))
    assert strategy_id in memory
    assert "The theorem applies here" not in memory


def test_run_memory_sanitizes_counterexample_explanation_status_text(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    data = read_yaml(run_dir / "counterexamples.yaml")
    data["tests"] = [
        {
            "id": "cx_status_text",
            "operator_id": "manual",
            "example_id": "example_1",
            "satisfies_all_claim_hypotheses": True,
            "target": "proof_step",
            "result": "failed",
            "refutes_claim": False,
            "refutes_step_only": True,
            "exposes_missing_hypothesis": False,
            "missing_hypothesis": None,
            "support": None,
            "explanation": "this refutes the claim",
            "repaired_claim_suggestion": None,
        }
    ]
    write_yaml(run_dir / "counterexamples.yaml", data)

    write_run_memory(
        run_dir.parents[1],
        run_dir,
        project_id=run_dir.parents[1].name,
        run_id=run_dir.name,
    )
    summary = summarize_memory(run_dir.parents[1], claim_text="counterexample pressure")

    assert "counterexample pressure recorded" in summary
    assert "this refutes the claim" not in summary


def test_run_memory_omits_status_like_strategy_title(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    brief = read_yaml(run_dir / "strategy_brief.yaml")
    strategy_id = brief["top_strategies"][0]["id"]
    brief["top_strategies"][0]["title"] = "The theorem applies here"
    write_yaml(run_dir / "strategy_brief.yaml", brief)
    synthesis = read_yaml(run_dir / "strategy_synthesis.yaml")
    for item in synthesis.get("routes", []):
        if item.get("id") == strategy_id:
            item["title"] = "The theorem applies here"
    write_yaml(run_dir / "strategy_synthesis.yaml", synthesis)

    write_run_memory(
        run_dir.parents[1],
        run_dir,
        project_id=run_dir.parents[1].name,
        run_id=run_dir.name,
    )
    summary = summarize_memory(run_dir.parents[1], claim_text=strategy_id.replace("_", " "))

    assert f"top strategy id: {strategy_id}" in summary
    assert "The theorem applies here" not in summary


def test_a_status_asserting_grade_sentence_is_dropped_not_stored(
    tmp_path: Path,
) -> None:
    """The model wrote "The theorem applies to this step." into the grade's
    next-cheapest-test field.

    That assertion must never be stored — but it used to be stopped by the
    memory boundary RAISING, which rolled the entire front half back and exited
    the run non-zero. A live round-5 run died exactly that way, discarding ten
    agent calls that had already been paid for, over one sentence in an
    advisory artifact. The sentence is now dropped where it appears: it reaches
    neither the grade nor memory, the drop is recorded, and the run continues.
    """

    run_dir = _run(tmp_path)
    grade_path = run_dir / "idea_grade.yaml"
    memory_dir = _project(run_dir) / "memory"

    run_front_half_agent_pass(
        run_dir=run_dir,
        client=UnsafeGradeClient(),
        stages=["grade_memory_memo"],
    )

    grade_text = grade_path.read_text(encoding="utf-8")
    assert "The theorem applies to this step" not in grade_text
    for path in memory_dir.glob("*.yaml"):
        assert "The theorem applies to this step" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "summary",
    [
        *REVIEWER_STATUS_ASSERTIONS,
        *NEGATED_STATUS_ASSERTIONS,
        *PROOF_AND_SOURCE_STATUS_ASSERTIONS,
        *COMMON_STATUS_ASSERTION_VARIANTS,
        *KERNEL_BLOCK_STATUS_ASSERTIONS,
        *MIXED_DISCLAIMER_STATUS_ASSERTIONS,
    ],
)
def test_hand_edited_memory_context_status_aliases_fail_validation(
    tmp_path: Path, summary: str
) -> None:
    run_dir = _run(tmp_path)
    write_memory_entry(
        _project(run_dir),
        _entry(
            run_dir,
            id="mem_safe_context",
            summary=(
                "Prior run requested an exact source locator for "
                f"{read_yaml(run_dir / 'locked_claim.yaml')['normalized_statement']}."
            ),
        ),
    )
    build_memory_context(run_dir)
    raw = read_yaml(run_dir / "memory_context.yaml")
    raw["matched_entries"][0]["summary"] = summary
    write_yaml(run_dir / "memory_context.yaml", raw)

    with pytest.raises(KernelValidationError, match="schema validation failed"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "summary",
    [
        *REVIEWER_STATUS_ASSERTIONS,
        *NEGATED_STATUS_ASSERTIONS,
        *PROOF_AND_SOURCE_STATUS_ASSERTIONS,
        *COMMON_STATUS_ASSERTION_VARIANTS,
        *KERNEL_BLOCK_STATUS_ASSERTIONS,
        *MIXED_DISCLAIMER_STATUS_ASSERTIONS,
    ],
)
def test_hand_edited_memory_index_status_aliases_are_quarantined(
    tmp_path: Path, summary: str
) -> None:
    run_dir = _run(tmp_path)
    write_memory_entry(_project(run_dir), _entry(run_dir, id="mem_safe_index"))
    index_path = _project(run_dir) / "memory" / "index.yaml"
    index = read_yaml(index_path)
    bad_id = index["entries"][0]["id"]
    index["entries"][0]["summary"] = summary
    write_yaml(index_path, index)

    loaded = load_memory_index(_project(run_dir))
    build_memory_context(run_dir)
    context = read_yaml(run_dir / "memory_context.yaml")

    assert bad_id not in {entry.id for entry in loaded.entries}
    assert summary not in [entry["summary"] for entry in context["matched_entries"]]
