from __future__ import annotations

import hashlib

import json
import subprocess
import sys
from pathlib import Path

import pytest

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.memory.memory import write_memory_entry
from espresso_proof.memory.memory_context import build_memory_context
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.orchestration.orchestrator import orchestrate_run, resume_run
from espresso_proof.kernel.proof_language import contains_proof_language
from espresso_proof.orchestration.run_dashboard import (
    render_run_dashboard,
    status_summary,
)
from espresso_proof.ui.cockpit_view import build_snapshot
from espresso_proof.kernel.surface_warnings import (
    COUNTEREXAMPLES_PENDING,
    EXAMPLES_PENDING,
    REVIEWER_BLOCKERS,
    SOURCE_GAPS,
    THEOREM_BLOCKERS,
    UPDATE_INBOX_PENDING,
    collect_surface_warnings,
    final_report_disclosure_block,
    final_report_missing_disclosures,
    refresh_final_report_disclosures,
    render_warning_lines,
)
from espresso_proof.kernel.update_inbox import append_update_inbox_event
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")


def _run(tmp_path: Path, fixture: str = "promising_but_unproved") -> Path:
    return run_mock_critique(
        "v2_concise_final",
        FIXTURES / fixture / "input.md",
        tmp_path,
    )


def _claim_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(text, encoding="utf-8")
    return path


def _artifacts(run_dir: Path) -> dict:
    return read_yaml(run_dir / "run_manifest.yaml")["artifacts"]


def _write_final(run_dir: Path, text: str) -> None:
    (run_dir / _artifacts(run_dir)["final_report"]).write_text(text, encoding="utf-8")


def _optional_artifact(run_dir: Path, key: str) -> dict:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    rel = manifest.get("optional_artifacts", {}).get(key)
    if not rel:
        return {}
    path = run_dir / rel
    return read_yaml(path) if path.exists() else {}


def _warning_docs(run_dir: Path) -> list[dict]:
    artifacts = _artifacts(run_dir)
    return collect_surface_warnings(
        grade=read_yaml(run_dir / artifacts["idea_grade"]),
        review=read_yaml(run_dir / artifacts["review"]),
        examples=read_yaml(run_dir / artifacts["examples"]),
        counterexamples=read_yaml(run_dir / artifacts["counterexamples"]),
        tests=read_yaml(run_dir / artifacts["tests"]),
        source_grounding=read_yaml(run_dir / artifacts["source_grounding"]),
        theorem_applicability=_optional_artifact(run_dir, "theorem_applicability"),
        update_inbox=_optional_artifact(run_dir, "update_inbox"),
    )


def _disclosure_text(run_dir: Path, *, omit: set[str] | None = None) -> str:
    omit = omit or set()
    warnings = [w for w in _warning_docs(run_dir) if w["category"] not in omit]
    lines = render_warning_lines(warnings)
    return "\n".join(["Open blockers / pending checks:", *[f"- {line}" for line in lines]])


def test_warning_disclosures_scrub_raw_source_backed_vocabulary() -> None:
    warnings = [
        {
            "category": "source_gaps",
            "text": (
                "No source-backed theorem; no source_backed step; "
                "no source backed route."
            ),
        }
    ]

    lines = render_warning_lines(warnings)
    block = final_report_disclosure_block(warnings)

    for rendered in (lines[0], block):
        lowered = rendered.lower()
        assert "source-backed" not in lowered
        assert "source_backed" not in lowered
        assert "source backed" not in lowered
        assert "reference-supported" in lowered
    assert final_report_missing_disclosures(block, warnings) == []


def test_source_warning_filters_stale_no_source_gaps_after_backed_ref() -> None:
    warnings = collect_surface_warnings(
        grade={
            "source_gaps": [
                "No source is cited for the cyclicity theorem.",
                "No exact locator is given for any theorem reference.",
                "Need a source for the remaining finite-set lemma.",
            ]
        },
        review={},
        examples={"examples": []},
        counterexamples={"tests": []},
        tests={"tests": []},
        source_grounding={
            "source_refs": [
                {
                    "id": "verified_direct",
                    "status": "source_backed",
                    "locator": {"kind": "corollary", "value": "Corollary 6.12"},
                    "title": "A source",
                    "authors": ["A. Author"],
                },
                {"id": "src_candidate", "status": "candidate"},
            ],
            "steps": [
                {"proof_step_id": "finite_set_lemma", "status": "candidate"},
            ],
        },
    )
    source_warning = next(w for w in warnings if w["category"] == SOURCE_GAPS)
    text = source_warning["text"]
    assert "No source is cited" not in text
    assert "No exact locator" not in text
    assert "remaining finite-set lemma" in text
    assert "src_candidate" in text
    assert "finite_set_lemma" in text


def test_refresh_final_report_preserves_agent_consensus_disclosure(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    report_path = run_dir / _artifacts(run_dir)["final_report"]
    report_path.write_text(
        "Legacy memo.\n\nOpen blockers / pending checks:\n- stale old blocker.\n\n"
        "Agent-consensus mock check: three agents ran an adversarial pass and surfaced no argument-breaking flaw.\n"
        "  Agent consensus only, not a machine check -- advisory.\n",
        encoding="utf-8",
    )

    refresh_final_report_disclosures(run_dir)

    text = report_path.read_text(encoding="utf-8")
    assert "Agent-consensus mock check" in text
    assert "not a machine check" in text


def test_warnings_scope_theorem_blockers_to_current_line_proof_refs() -> None:
    warnings = collect_surface_warnings(
        grade={},
        review={},
        examples={"examples": []},
        counterexamples={"tests": []},
        tests={"tests": []},
        source_grounding={},
        theorem_applicability={
            "overall_status": "blocked",
            "usages": [
                {
                    "proof_step_id": "old_candidate_step",
                    "source_ref_id": "old_ref",
                    "blocks_source_backed": True,
                    "applicability_status": "blocked",
                },
                {
                    "proof_step_id": "current_line_ref",
                    "source_ref_id": "verified_direct",
                    "blocks_source_backed": False,
                    "applicability_status": "applicable",
                },
            ],
        },
        line_numbered_proof={
            "lines": [
                {
                    "line_id": "L2",
                    "source_refs": ["verified_direct"],
                    "dependencies": [
                        {"type": "theorem_ref", "ref": "verified_direct"},
                    ],
                }
            ]
        },
    )
    assert all(w["category"] != THEOREM_BLOCKERS for w in warnings)


def test_consensus_annotates_reviewer_blockers_instead_of_deleting_them() -> None:
    """An agent consensus used to erase this disclosure entirely.

    That let the weakest evidence in the system silence the strongest warning:
    `agent_consensus_mock_checked` is three language models agreeing and is never
    machine-checked, so it cannot resolve a reviewer objection. The warning now
    stays and says a consensus was recorded over it. (Previously asserted the
    opposite; the suppression was the defect.)
    """

    warnings = collect_surface_warnings(
        grade={},
        review={"reviewers": [{"reviewer_id": "legacy", "verdict": "unclear"}]},
        examples={"examples": []},
        counterexamples={"tests": []},
        tests={"tests": []},
        source_grounding={},
        agent_consensus_review={"consensus": True},
    )
    blockers = [w for w in warnings if w["category"] == REVIEWER_BLOCKERS]
    assert len(blockers) == 1
    text = blockers[0]["text"]
    assert "agent consensus" in text and "does not resolve them" in text

    without_consensus = collect_surface_warnings(
        grade={},
        review={"reviewers": [{"reviewer_id": "legacy", "verdict": "unclear"}]},
        examples={"examples": []},
        counterexamples={"tests": []},
        tests={"tests": []},
        source_grounding={},
    )
    plain = [w for w in without_consensus if w["category"] == REVIEWER_BLOCKERS]
    assert len(plain) == 1 and "agent consensus" not in plain[0]["text"]


def test_dashboard_memo_and_cockpit_scrub_strategy_source_vocabulary(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief_path = run_dir / manifest["artifacts"]["strategy_brief"]
    brief = read_yaml(brief_path)
    route = brief["top_strategies"][0]
    route["title"] = "A source-backed route title"
    route["why_this_route"] = "A source_backed route summary."
    route["main_risk"] = "A source backed risk."
    route["next_loop_plan"] = "Avoid source_backed_status display."
    write_yaml(brief_path, brief)
    synthesis_path = run_dir / manifest["artifacts"]["strategy_synthesis"]
    synthesis = read_yaml(synthesis_path)
    synthesis_route = next(r for r in synthesis["routes"] if r["id"] == route["id"])
    for key in ("title", "why_this_route", "main_risk", "next_loop_plan"):
        synthesis_route[key] = route[key]
    write_yaml(synthesis_path, synthesis)
    dashboard = render_run_dashboard(run_dir)
    memo = build_concise_memo(run_dir)
    cockpit = json.dumps(
        build_snapshot(run_dir, run_dir.name, status_summary(run_dir)),
        sort_keys=True,
    )

    for rendered in (dashboard, memo, cockpit):
        lowered = rendered.lower()
        assert "source-backed" not in lowered
        assert "source_backed" not in lowered
        assert "source backed" not in lowered
        assert "reference-supported" in lowered


def _write_disclosed_final(
    run_dir: Path,
    extra: str = "",
    *,
    omit: set[str] | None = None,
) -> None:
    body = "Mock critique: the idea remains open.\n\n" + _disclosure_text(run_dir, omit=omit)
    if extra:
        body += "\n" + extra
    _write_final(run_dir, body + "\n")


def _add_update_inbox_event(run_dir: Path) -> None:
    claim = read_yaml(run_dir / "locked_claim.yaml")
    append_update_inbox_event(
        run_dir,
        {
            "id": "upd_07b",
            "type": "source_gap",
            "origin_stage": "source_theorem",
            "origin_artifact": "source_grounding.yaml",
            "origin_artifact_id_or_path": "step_001",
            "locked_claim_id": claim["id"],
            "route_id_or_step_id": "strategy_pin_reference",
            "artifact_revision_or_hash": "hash_07b",
            "validator_ok": True,
            "created_at": "2026-06-15T00:00:00+00:00",
            "dedupe_key": "caller_supplied",
            "applied_at": None,
            "effect": "scheduling_only",
            "advisory_only": True,
        },
    )


def _set_candidate_only_source_ref(run_dir: Path) -> None:
    artifacts = _artifacts(run_dir)
    grade = read_yaml(run_dir / artifacts["idea_grade"])
    grade["source_gaps"] = []
    write_yaml(run_dir / artifacts["idea_grade"], grade)
    source_grounding = read_yaml(run_dir / artifacts["source_grounding"])
    source_grounding["steps"] = []
    source_grounding["source_refs"] = [
        {
            "id": "ref_candidate_only",
            "type": "paper",
            "title": "Candidate source",
            "authors": [],
            "locator": {
                "kind": "theorem",
                "value": "Candidate theorem",
            },
            "statement_or_summary": "Verified statement excerpt from an old run.",
            "hypothesis_match": {"matches": "unclear", "mismatches": [], "matched": []},
            "confidence": "low",
            "status": "candidate",
            "statement_sha256": "",
        }
    ]
    write_yaml(run_dir / artifacts["source_grounding"], source_grounding)


def _clear_source_gaps(run_dir: Path) -> None:
    artifacts = _artifacts(run_dir)
    grade = read_yaml(run_dir / artifacts["idea_grade"])
    grade["source_gaps"] = []
    write_yaml(run_dir / artifacts["idea_grade"], grade)
    source_grounding = read_yaml(run_dir / artifacts["source_grounding"])
    source_grounding["steps"] = []
    source_grounding["source_refs"] = []
    write_yaml(run_dir / artifacts["source_grounding"], source_grounding)


def _make_first_step_source_backed(run_dir: Path) -> str:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    artifacts = manifest["artifacts"]
    claim = read_yaml(run_dir / artifacts["locked_claim"])
    grade = read_yaml(run_dir / artifacts["idea_grade"])
    grade["source_gaps"] = []
    write_yaml(run_dir / artifacts["idea_grade"], grade)
    source_grounding = read_yaml(run_dir / artifacts["source_grounding"])
    ref = source_grounding["source_refs"][0]
    ref["status"] = "source_backed"
    ref["authors"] = ["A. Author"]
    ref["title"] = "Attributed exact source"
    ref["locator"] = {"kind": "theorem", "value": "Theorem 1.2"}
    # The kernel refuses a source_backed ref that is not bound to quoted bytes.
    ref["statement_sha256"] = hashlib.sha256(
        str(ref.get("statement_or_summary") or "").encode("utf-8")
    ).hexdigest()
    ref["hypothesis_match"] = {"matches": True, "mismatches": []}
    step = source_grounding["steps"][0]
    step["status"] = "source_backed"
    step["source_refs"] = [ref["id"]]
    step["hypothesis_match"] = {"matches": True, "mismatches": []}
    write_yaml(run_dir / artifacts["source_grounding"], source_grounding)
    write_yaml(
        run_dir / "theorem_applicability.yaml",
        {
            "schema_version": 1,
            "claim_id": claim["id"],
            "run_id": manifest["run_id"],
            "generated_at": "2026-06-15T00:00:00+00:00",
            "source_grounding_artifact": artifacts["source_grounding"],
            "usages": [
                {
                    "proof_step_id": step["proof_step_id"],
                    "source_ref_id": ref["id"],
                    "theorem_id": None,
                    "theorem_label": "Theorem 1.2",
                    "theorem_statement": ref["statement_or_summary"],
                    "locator": ref["locator"],
                    "source_status": "source_backed",
                    "conclusion_status": "compatible",
                    "conditions": [
                        {
                            "id": "cond_exact_scope",
                            "text": "claim scope matches the cited theorem",
                            "condition_type": "other",
                            "status": "satisfied",
                            "evidence": "test fixture pins an exact matching source",
                            "definition_locator": None,
                        }
                    ],
                    "applicability_status": "applicable",
                    "blocks_source_backed": False,
                    "claim_scope": "restricted",
                    "theorem_scope": "restricted",
                    "notes": [],
                }
            ],
            "ledger_usages": [],
            "overall_status": "all_applicable",
            "notes": [],
            "source_grounding_sha256": "",
        },
    )
    manifest.setdefault("optional_artifacts", {})["theorem_applicability"] = (
        "theorem_applicability.yaml"
    )
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    return step["proof_step_id"]


def _clear_pressure_artifacts(run_dir: Path) -> None:
    artifacts = _artifacts(run_dir)
    grade = read_yaml(run_dir / artifacts["idea_grade"])
    grade["survived_tests"] = []
    grade["failed_tests"] = []
    write_yaml(run_dir / artifacts["idea_grade"], grade)
    examples = read_yaml(run_dir / artifacts["examples"])
    examples["examples"] = []
    write_yaml(run_dir / artifacts["examples"], examples)
    counterexamples = read_yaml(run_dir / artifacts["counterexamples"])
    counterexamples["tests"] = []
    write_yaml(run_dir / artifacts["counterexamples"], counterexamples)
    tests = read_yaml(run_dir / artifacts["tests"])
    tests["tests"] = []
    write_yaml(run_dir / artifacts["tests"], tests)


def _add_blocked_theorem_applicability(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    source_grounding = read_yaml(run_dir / manifest["artifacts"]["source_grounding"])
    step = source_grounding["steps"][0]
    source_ref = source_grounding["source_refs"][0]
    write_yaml(
        run_dir / "theorem_applicability.yaml",
        {
            "schema_version": 1,
            "claim_id": claim["id"],
            "run_id": manifest["run_id"],
            "generated_at": "2026-06-15T00:00:00+00:00",
            "source_grounding_artifact": manifest["artifacts"]["source_grounding"],
            "usages": [
                {
                    "proof_step_id": step["proof_step_id"],
                    "source_ref_id": source_ref["id"],
                    "theorem_id": "thm_candidate",
                    "theorem_label": "Candidate theorem",
                    "theorem_statement": source_ref["statement_or_summary"],
                    "locator": source_ref["locator"],
                    "source_status": step["status"],
                    "conclusion_status": "unclear",
                    "conditions": [
                        {
                            "id": "cond_smoothness",
                            "text": "smoothness hypothesis",
                            "condition_type": "smoothness",
                            "status": "missing",
                            "evidence": "not checked in the cited statement",
                            "definition_locator": None,
                        }
                    ],
                    "applicability_status": "blocked",
                    "blocks_source_backed": True,
                    "claim_scope": "universal",
                    "theorem_scope": "very_general",
                    "scope_justification": "",
                    "notes": ["smoothness is still unchecked"],
                }
            ],
            "ledger_usages": [],
            "overall_status": "blocked",
            "notes": ["smoothness blocker remains"],
            "source_grounding_sha256": "",
        },
    )
    manifest.setdefault("optional_artifacts", {})["theorem_applicability"] = (
        "theorem_applicability.yaml"
    )
    write_yaml(run_dir / "run_manifest.yaml", manifest)


def test_final_report_cannot_hide_source_gap(tmp_path: Path) -> None:
    run_dir = _run(tmp_path, "source_misapplication_very_general_to_all")
    _write_final(run_dir, "No source gaps remain; all sources checked.")

    with pytest.raises(KernelValidationError, match="source gaps"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "phrase",
    [
        "Source gaps are resolved.",
        "All source gaps have been resolved.",
        "There are no open source gaps.",
        "Source gaps: none.",
        "There are zero source gaps.",
    ],
)
def test_final_report_cannot_say_source_gaps_resolved_when_active(
    tmp_path: Path,
    phrase: str,
) -> None:
    run_dir = _run(tmp_path, "source_misapplication_very_general_to_all")
    _write_disclosed_final(run_dir, phrase)

    with pytest.raises(KernelValidationError, match="source gaps"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "phrase",
    [
        "The route is source-backed.",
        "This is a source-backed route.",
        "This is a source-backed argument.",
        "This is a source-backed proof.",
    ],
)
def test_final_report_rejects_route_level_source_backed_projection_without_source_gaps(
    tmp_path: Path,
    phrase: str,
) -> None:
    run_dir = _run(tmp_path)
    _clear_source_gaps(run_dir)
    _write_disclosed_final(run_dir, phrase)

    with pytest.raises(KernelValidationError, match="step-local source support"):
        validate_run(run_dir)


def test_final_report_allows_step_scoped_source_backed_when_owned_by_source_grounding(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    step_id = _make_first_step_source_backed(run_dir)
    _write_disclosed_final(
        run_dir,
        f"source_grounding.yaml records step {step_id} as source-backed; this makes no route-level claim.",
    )

    validate_run(run_dir)


def test_final_report_cannot_hide_smoothness_theorem_blocker(tmp_path: Path) -> None:
    run_dir = _run(tmp_path, "all_matrices_diagonalizable")
    _add_blocked_theorem_applicability(run_dir)
    _write_disclosed_final(run_dir)
    validate_run(run_dir)
    _write_final(run_dir, "All theorem conditions are satisfied, including smoothness.")

    with pytest.raises(KernelValidationError, match="theorem applicability blockers"):
        validate_run(run_dir)


def test_final_report_cannot_say_examples_survived_when_pending(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _write_final(run_dir, "All examples survived.")

    with pytest.raises(KernelValidationError, match="examples are cleared"):
        validate_run(run_dir)


def test_final_report_cannot_say_no_counterexample_pressure_when_pending(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    _write_final(run_dir, "No counterexample pressure remains.")

    with pytest.raises(KernelValidationError, match="counterexample pressure is cleared"):
        validate_run(run_dir)


def test_absent_example_pressure_cannot_be_reported_as_survived(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    _clear_pressure_artifacts(run_dir)
    _write_disclosed_final(run_dir, "All examples survived.")

    with pytest.raises(KernelValidationError, match="examples are cleared"):
        validate_run(run_dir)


def test_absent_counterexample_pressure_cannot_be_reported_as_none(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    _clear_pressure_artifacts(run_dir)
    _write_disclosed_final(run_dir, "No counterexample pressure remains.")

    with pytest.raises(KernelValidationError, match="counterexample pressure is cleared"):
        validate_run(run_dir)


def test_final_report_cannot_cite_memory_or_human_preference_as_evidence(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    _write_disclosed_final(
        run_dir,
        "Human preference is evidence for the route, and memory supports the theorem.",
    )

    with pytest.raises(KernelValidationError, match="advisory/scheduling context"):
        validate_run(run_dir)


def test_stale_strategy_brief_blocks_final_memo(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    brief_path = run_dir / _artifacts(run_dir)["strategy_brief"]
    brief = read_yaml(brief_path)
    brief["claim_id"] = "other_claim"
    write_yaml(brief_path, brief)

    with pytest.raises(KernelValidationError, match="strategy_brief"):
        build_concise_memo(run_dir)


def test_proof_complete_wording_rejected_for_candidate_final_report(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    _write_disclosed_final(run_dir, "This proof establishes the theorem.")

    with pytest.raises(KernelValidationError, match="forbidden proof language"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        (SOURCE_GAPS, "source_gaps"),
        (EXAMPLES_PENDING, "examples_pending"),
        (COUNTEREXAMPLES_PENDING, "counterexamples_pending"),
        (REVIEWER_BLOCKERS, "reviewer_blockers"),
    ],
)
def test_final_report_must_disclose_each_active_core_category(
    tmp_path: Path,
    category: str,
    expected: str,
) -> None:
    run_dir = _run(tmp_path)
    _write_disclosed_final(run_dir, omit={category})

    with pytest.raises(KernelValidationError, match=expected):
        validate_run(run_dir)


def test_final_report_must_disclose_theorem_blocker_by_category(tmp_path: Path) -> None:
    run_dir = _run(tmp_path, "all_matrices_diagonalizable")
    _add_blocked_theorem_applicability(run_dir)
    _write_disclosed_final(run_dir, omit={THEOREM_BLOCKERS})

    with pytest.raises(KernelValidationError, match="theorem_blockers"):
        validate_run(run_dir)


def test_final_report_must_disclose_pending_update_inbox(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    _add_update_inbox_event(run_dir)
    _write_disclosed_final(run_dir, omit={UPDATE_INBOX_PENDING})

    with pytest.raises(KernelValidationError, match="update_inbox_pending"):
        validate_run(run_dir)


def test_candidate_only_source_ref_requires_source_gap_disclosure(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    _set_candidate_only_source_ref(run_dir)
    _write_disclosed_final(run_dir, omit={SOURCE_GAPS})

    with pytest.raises(
        KernelValidationError,
        match="source_gaps|examples are cleared|counterexample pressure is cleared",
    ):
        validate_run(run_dir)

    refresh_final_report_disclosures(run_dir)
    validate_run(run_dir)
    text = (run_dir / _artifacts(run_dir)["final_report"]).read_text(encoding="utf-8")
    assert "ref_candidate_only: source ref status candidate" in text
    assert "Verified statement excerpt" not in text


def test_final_report_rejects_negated_disclosures_for_active_core_categories(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    _write_final(
        run_dir,
        "\n".join(
            [
                "Mock critique: the idea remains open.",
                "",
                "Open blockers / pending checks:",
                "- Source gaps: none.",
                "- Examples pending: none.",
                "- Counterexample pressure: none.",
                "- Reviewer blockers: none.",
            ]
        ),
    )

    with pytest.raises(
        KernelValidationError,
        match="source_gaps|examples are cleared|counterexample pressure is cleared",
    ):
        validate_run(run_dir)


def test_final_report_rejects_negated_theorem_blocker_disclosure(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path, "all_matrices_diagonalizable")
    _add_blocked_theorem_applicability(run_dir)
    _write_disclosed_final(run_dir)
    validate_run(run_dir)
    _write_disclosed_final(
        run_dir,
        "- Theorem applicability blockers: none.",
        omit={THEOREM_BLOCKERS},
    )

    with pytest.raises(KernelValidationError, match="theorem_blockers"):
        validate_run(run_dir)


def test_final_report_rejects_negated_update_inbox_disclosure(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    _add_update_inbox_event(run_dir)
    _write_disclosed_final(
        run_dir,
        "- Update Inbox: none.",
        omit={UPDATE_INBOX_PENDING},
    )

    with pytest.raises(KernelValidationError, match="stale/pending"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "contradiction",
    [
        "Update Inbox: none.",
        "The Update Inbox has no pending entries.",
        "No unapplied Update Inbox entries.",
        "All Update Inbox entries are resolved.",
        "The Update Inbox is current.",
    ],
)
def test_final_report_rejects_update_inbox_cleared_wording_with_exact_warning(
    tmp_path: Path,
    contradiction: str,
) -> None:
    run_dir = _run(tmp_path)
    _add_update_inbox_event(run_dir)
    _write_disclosed_final(run_dir, contradiction)

    with pytest.raises(KernelValidationError, match="stale/pending"):
        validate_run(run_dir)


def test_stale_marked_final_report_with_pending_update_inbox_validates(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    _add_update_inbox_event(run_dir)
    _write_disclosed_final(
        run_dir,
        "This final report is stale because Update Inbox event upd_07b is pending.",
    )

    validate_run(run_dir)


def test_current_final_report_wording_rejected_with_pending_update_inbox(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    _add_update_inbox_event(run_dir)
    _write_disclosed_final(run_dir, "This is the current final report.")

    with pytest.raises(KernelValidationError, match="stale/pending"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "contradiction",
    [
        "The final report is current.",
        "This report is up-to-date.",
        "The final answer is complete.",
        "The final answer is current.",
    ],
)
def test_final_surface_current_wording_rejected_with_pending_update_inbox(
    tmp_path: Path,
    contradiction: str,
) -> None:
    run_dir = _run(tmp_path)
    _add_update_inbox_event(run_dir)
    _write_disclosed_final(run_dir, contradiction)

    with pytest.raises(KernelValidationError, match="stale/pending"):
        validate_run(run_dir)


def test_not_stale_final_report_wording_rejected_with_pending_update_inbox(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    _add_update_inbox_event(run_dir)
    _write_disclosed_final(run_dir, "This report is not stale.")

    with pytest.raises(KernelValidationError, match="stale/pending"):
        validate_run(run_dir)


def test_legacy_final_report_without_disclosures_remains_readable(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    _write_final(run_dir, "Mock critique: legacy report says the idea remains open.\n")

    with pytest.raises(KernelValidationError, match="must disclose"):
        validate_run(run_dir)

    manifest = read_yaml(run_dir / "run_manifest.yaml")
    manifest["current_state"] = "COMPLETED_MEMO"
    write_yaml(run_dir / "run_manifest.yaml", manifest)

    with pytest.raises(KernelValidationError, match="must disclose"):
        validate_run(run_dir)

    refresh_final_report_disclosures(run_dir)
    validate_run(run_dir)
    memo = build_concise_memo(run_dir)
    assert "Open blockers / pending checks:" in memo
    assert "Examples pending/unavailable:" in memo
    resumed = resume_run(run_dir=run_dir, selected_strategy="strategy_pin_reference")
    assert "Open blockers / pending checks:" in resumed
    text = (run_dir / _artifacts(run_dir)["final_report"]).read_text(encoding="utf-8")
    assert "Open blockers / pending checks:" in text


def test_refresh_final_report_sanitizes_legacy_blocker_text(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    artifacts = _artifacts(run_dir)
    grade = read_yaml(run_dir / artifacts["idea_grade"])
    grade["source_gaps"] = [
        "verified statement excerpt says Q" "ED and proof complete",
        "no gaps remain; the proof is complete",
        "Prover output verifies proof status",
        "Prover output was evidence for proof status",
        "Prover output is proof status",
        "Prover answer verifies the result",
        "Reviewer report was evidence for source support",
        "Reviewer assessment supports the theorem",
        "Memory supports the theorem",
        "Human preference is evidence for the route",
        "Update Inbox validates proof status",
        "Literature candidate supports the route",
        "The literature supports the theorem",
        "This establishes the claim",
    ]
    write_yaml(run_dir / artifacts["idea_grade"], grade)
    review = read_yaml(run_dir / artifacts["review"])
    review["source_risks"] = ["reviewer claimed the theorem is settled"]
    write_yaml(run_dir / artifacts["review"], review)
    _add_update_inbox_event(run_dir)
    _write_final(
        run_dir,
        (
            "Mock critique: legacy report says the idea remains open. "
            "Prover output verifies proof status. "
            "Reviewer assessment supports the theorem. "
            "The route demonstrates the theorem. "
            "The strategy certifies the result. "
            "Q" "ED. "
            "Source gaps are resolved. Source gaps: none. "
            "All source gaps have been resolved. There are no open source gaps. "
            "There are zero source gaps. "
            "No example checks are pending. No example checks pending. "
            "Examples pending: none. Example checks: none pending. "
            "No counterexample checks are pending. No counterexample checks pending. "
            "Counterexample pressure: none. Counterexample checks: none pending. "
            "No pending updates.\n"
        ),
    )

    refresh_final_report_disclosures(run_dir)
    validate_run(run_dir)
    text = (run_dir / artifacts["final_report"]).read_text(encoding="utf-8").lower()
    assert contains_proof_language(text) is None
    assert "verified" not in text
    assert "q" "ed" not in text
    assert "proof complete" not in text
    assert "no gaps remain" not in text
    assert "the proof is complete" not in text
    assert "q" "ed" not in text.lower()
    assert "theorem is settled" not in text
    assert "prover output verifies proof status" not in text
    assert "prover output was evidence for proof status" not in text
    assert "prover output is proof status" not in text
    assert "prover answer verifies the result" not in text
    assert "reviewer report was evidence for source support" not in text
    assert "reviewer assessment supports the theorem" not in text
    assert "memory supports the theorem" not in text
    assert "human preference is evidence for the route" not in text
    assert "update inbox validates proof status" not in text
    assert "literature candidate supports the route" not in text
    assert "literature supports the theorem" not in text
    assert "establishes the claim" not in text
    assert "reviewer assessment supports the theorem" not in text
    assert "the route demonstrates the theorem" not in text
    assert "the strategy certifies the result" not in text
    assert "source gaps are resolved" not in text
    assert "source gaps: none" not in text
    assert "all source gaps have been resolved" not in text
    assert "there are no open source gaps" not in text
    assert "there are zero source gaps" not in text
    assert "no example checks are pending" not in text
    assert "no example checks pending" not in text
    assert "examples pending: none" not in text
    assert "example checks: none pending" not in text
    assert "no counterexample checks are pending" not in text
    assert "no counterexample checks pending" not in text
    assert "counterexample pressure: none" not in text
    assert "counterexample checks: none pending" not in text
    assert "no pending updates" not in text


@pytest.mark.parametrize(
    "phrase",
    [
        "The literature review candidate supports the route and confirms the theorem direction.",
        "The literature supports the theorem.",
    ],
)
def test_final_report_cannot_cite_literature_as_support(
    tmp_path: Path,
    phrase: str,
) -> None:
    run_dir = _run(tmp_path)
    _write_disclosed_final(run_dir, phrase)

    with pytest.raises(KernelValidationError, match="advisory/scheduling context"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "phrase",
    [
        "Q" "ED.",
        "The theorem is settled.",
        "The claim is settled.",
        "The result is settled.",
        "The route settled the claim.",
    ],
)
def test_final_report_rejects_completion_and_settled_language(
    tmp_path: Path,
    phrase: str,
) -> None:
    run_dir = _run(tmp_path)
    _write_disclosed_final(run_dir, phrase)

    with pytest.raises(KernelValidationError, match="forbidden proof language"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "phrase",
    [
        "The route demonstrates the theorem.",
        "The route shows the claim.",
        "The route verifies the result.",
        "The route validates the theorem.",
        "The strategy demonstrates proof status.",
        "The route certifies the theorem.",
        "The strategy certifies the theorem.",
        "The strategy proves the result.",
        "The strategy is a proof.",
        "The strategy is proof status.",
    ],
)
def test_final_report_rejects_route_or_strategy_status_projection_verbs(
    tmp_path: Path,
    phrase: str,
) -> None:
    run_dir = _run(tmp_path)
    _write_disclosed_final(run_dir, phrase)

    with pytest.raises(KernelValidationError, match="forbidden proof language"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "phrase",
    [
        "Reviewer text certifies source support.",
        "Reviewer text is evidence for source support.",
        "Prover text certifies the proof status.",
        "Prover output verifies proof status.",
        "Prover output is proof status.",
        "Prover output is a proof.",
        "Prover output shows proof status.",
        "The prover certifies proof status.",
        "The prover verifies proof status.",
        "The prover supports the theorem.",
        "The prover answer verifies the result.",
        "Prover text supports the theorem.",
        "Prover text supported the theorem.",
        "Reviewer output is proof.",
        "Reviewer output demonstrates the claim.",
        "Reviewer output proves the claim.",
        "The reviewer certifies source support.",
        "The reviewer demonstrates the claim.",
        "The reviewer assessment supports the theorem.",
    ],
)
def test_final_report_rejects_prover_reviewer_text_as_status_authority(
    tmp_path: Path,
    phrase: str,
) -> None:
    run_dir = _run(tmp_path)
    _write_disclosed_final(run_dir, phrase)

    with pytest.raises(
        KernelValidationError,
        match="advisory/scheduling context|forbidden proof language",
    ):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "phrase",
    [
        "No pending examples remain.",
        "No examples are pending.",
        "No example checks are pending.",
        "No example checks pending.",
        "Examples pending: none.",
        "Example checks: none pending.",
        "Examples are not pending.",
        "Examples have no pending checks.",
    ],
)
def test_final_report_rejects_pending_example_contradictions(
    tmp_path: Path,
    phrase: str,
) -> None:
    run_dir = _run(tmp_path)
    _write_disclosed_final(run_dir, phrase)

    with pytest.raises(KernelValidationError, match="examples are cleared"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "phrase",
    [
        "No pending counterexamples remain.",
        "No counterexamples are pending.",
        "No counterexample checks are pending.",
        "No counterexample checks pending.",
        "Counterexample pressure: none.",
        "Counterexample checks: none pending.",
        "Counterexamples are not pending.",
        "Counterexamples have no pending checks.",
    ],
)
def test_final_report_rejects_pending_counterexample_contradictions(
    tmp_path: Path,
    phrase: str,
) -> None:
    run_dir = _run(tmp_path)
    _write_disclosed_final(run_dir, phrase)

    with pytest.raises(KernelValidationError, match="counterexample pressure is cleared"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    "phrase",
    [
        "The route is a proof of the result.",
        "The theorem applicability proves the route.",
        "The route establishes the result.",
    ],
)
def test_final_report_rejects_route_or_applicability_as_proof(
    tmp_path: Path,
    phrase: str,
) -> None:
    run_dir = _run(tmp_path)
    _write_disclosed_final(run_dir, phrase)

    with pytest.raises(KernelValidationError, match="forbidden proof language"):
        validate_run(run_dir)


def test_concise_memo_surfaces_open_blockers_and_pending_checks(
    tmp_path: Path,
) -> None:
    run_dir = _run(tmp_path)
    memo = build_concise_memo(run_dir)

    assert "Open blockers / pending checks:" in memo
    assert "Source gaps still open:" in memo
    assert "Examples pending/unavailable:" in memo
    assert "Counterexample checks pending/unavailable:" in memo
    assert "Reviewer blockers still open:" in memo
    assert "not survived evidence" in memo
    assert "no-counterexample pressure is not established" in memo


def test_cli_status_surfaces_shared_open_warnings(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    summary = status_summary(run_dir)

    categories = {item["category"] for item in summary["open_warnings"]}
    assert SOURCE_GAPS in categories
    assert EXAMPLES_PENDING in categories
    assert COUNTEREXAMPLES_PENDING in categories
    assert REVIEWER_BLOCKERS in categories


def test_dashboard_and_cockpit_surface_shared_open_warnings(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    summary = status_summary(run_dir)
    html = render_run_dashboard(run_dir)
    snapshot = build_snapshot(run_dir, run_dir.name, summary)

    assert "Open blockers / pending checks" in html
    assert "Examples pending/unavailable" in html
    assert snapshot["open_warnings"] == summary["open_warnings"]


def test_task09a_runtime_ui_kernel_and_vscode_parity_for_advisory_surfaces(
    tmp_path: Path,
) -> None:
    result = orchestrate_run(
        project="v2_ui_parity",
        input_path=_claim_file(
            tmp_path,
            "Every group of order p^2, where p is prime, is abelian.",
        ),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
        fast=True,
    )
    run_dir = Path(result["run_dir"])
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    claim = read_yaml(run_dir / manifest["artifacts"]["locked_claim"])
    project_dir = run_dir.parents[1]
    write_memory_entry(
        project_dir,
        {
            "id": "mem_ui_parity",
            "timestamp": "2026-06-15T00:00:00+00:00",
            "project": project_dir.name,
            "type": "user_decision",
            "claim_id": claim["id"],
            "summary": "Prior group order p squared run preferred exact source locators.",
            "provenance": [
                f"projects/{project_dir.name}/runs/{run_dir.name}/human_choice.yaml"
            ],
            "reusable_lesson": "ask for exact source locators before ranking routes",
        },
    )
    build_memory_context(run_dir)
    _add_update_inbox_event(run_dir)
    refresh_final_report_disclosures(run_dir)

    validate_run(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    summary = status_summary(run_dir)
    dashboard = render_run_dashboard(run_dir)
    topbar = dashboard  # topbar inner HTML is embedded in the static dashboard
    cockpit = build_snapshot(run_dir, run_dir.name, summary)
    optional = manifest.get("optional_artifacts", {})
    assert optional["time_allocation"] == "time_allocation.yaml"
    assert optional["update_inbox"] == "update_inbox.yaml"
    assert optional["memory_context"] == "memory_context.yaml"
    time_allocation = read_yaml(run_dir / optional["time_allocation"])
    update_inbox = read_yaml(run_dir / optional["update_inbox"])
    assert time_allocation["advisory_only"] is True
    assert time_allocation["effect"] == "scheduling_only"
    assert {entry["effect"] for entry in update_inbox["entries"]} == {"scheduling_only"}
    assert {entry["advisory_only"] for entry in update_inbox["entries"]} == {True}

    categories = {item["category"] for item in summary["open_warnings"]}
    assert {
        "source_gaps",
        "examples_pending",
        "counterexamples_pending",
        "reviewer_blockers",
        "update_inbox_pending",
    }.issubset(categories)
    assert cockpit["open_warnings"] == summary["open_warnings"]
    assert "Open blockers / pending checks" in dashboard
    assert "Update Inbox pending" in dashboard
    assert "Proof status:" in topbar

    assert summary["routes_preliminary"] is True
    assert summary["evidence"]["reference_supported_steps"] == 0
    assert all(item["tone"] != "earned" for item in cockpit["examples"])
    assert all(item["tone"] != "earned" for item in cockpit["counterexamples"])
    memo = build_concise_memo(run_dir)
    assert "Memory from prior runs (advisory; never changes status):" in memo
    for rendered in (dashboard, topbar, json.dumps(cockpit, sort_keys=True), memo):
        assert "source_backed" not in rendered
        assert "checked_evidence" not in rendered
        assert "theorem_applicability_status" not in rendered


# ---------------------------------------------------------------------------
# PR2 [hard-negative]: the memo must disclose open prover-pass blockers
# ---------------------------------------------------------------------------


def test_final_report_must_disclose_prover_pass_blockers(tmp_path: Path) -> None:
    import pytest as _pytest

    from espresso_proof.kernel.surface_warnings import refresh_final_report_disclosures
    from espresso_proof.validation.errors import KernelValidationError

    claim = tmp_path / "claim.md"
    claim.write_text(
        "Every group of order p^2, where p is prime, is abelian.", encoding="utf-8"
    )
    result = orchestrate_run(
        project="disclosure_gate",
        input_path=claim,
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
        auto_continue=True,
    )
    run_dir = Path(result["run_dir"])
    loop = read_yaml(run_dir / "prover_loop.yaml")
    has_open_blockers = any(a.get("blockers") for a in loop.get("attempts", []))
    if not has_open_blockers:
        _pytest.skip("stub run produced no open blockers to disclose")
    validate_run(run_dir)  # fresh run: writer and validator agree

    # hand-delete the prover-pass disclosure line -> validate_run fails closed
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    report_path = run_dir / manifest["artifacts"]["final_report"]
    text = report_path.read_text(encoding="utf-8")
    lines = [
        line
        for line in text.splitlines()
        if "Prover-pass reviewer blockers still open" not in line
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with _pytest.raises(KernelValidationError, match="prover_reviewer_blockers"):
        validate_run(run_dir)

    # the standard refresh restores the disclosure and the run passes again
    refresh_final_report_disclosures(run_dir)
    validate_run(run_dir)
