"""Goal 42E cycle 5: source-discipline dogfooding (very-general vs all)."""

from __future__ import annotations

from pathlib import Path

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.mock_pipeline import _detect_scenario

DISCIPLINE_CLAIM = (
    "Every cubic fourfold has a nontrivial Hodge atom obstruction, so all "
    "cubic fourfolds are irrational.\n"
)


def test_universal_irrationality_routes_to_source_misapplication(tmp_path: Path) -> None:
    path = tmp_path / "case" / "input.md"
    assert (
        _detect_scenario(path, DISCIPLINE_CLAIM)
        == "source_misapplication_very_general_to_all"
    )
    # the hard-sketch dogfood text (very general, no "all") is unaffected.
    assert _detect_scenario(
        path,
        "A Hodge-theoretic atom decomposition argument shows very general "
        "cubic fourfolds are irrational.\n",
    ) == "vague_claim_unclear"
    # fixture inputs are matched by directory name BEFORE text rules.
    fixture = tmp_path / "hodge_all_cubic_fourfolds" / "input.md"
    assert _detect_scenario(fixture, DISCIPLINE_CLAIM) == "hodge_all_cubic_fourfolds"


def test_discipline_run_records_refusal_and_holds_candidate(tmp_path: Path) -> None:
    from espresso_proof.io import write_text
    from espresso_proof.orchestrator import orchestrate_run
    from espresso_proof.validators import validate_run

    input_path = tmp_path / "input.md"
    write_text(input_path, DISCIPLINE_CLAIM)
    result = orchestrate_run(
        project="probe_discipline",
        input_path=input_path,
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    validate_run(run_dir)
    grounding = read_yaml(run_dir / "source_grounding.yaml")
    assert not any(
        r.get("status") == "source_backed" for r in grounding.get("source_refs", [])
    ), "the misapplied source must never earn support"
    mismatches = [
        m
        for item in grounding.get("steps", []) + grounding.get("source_refs", [])
        for m in ((item.get("hypothesis_match") or {}).get("mismatches", []) or [])
    ]
    assert mismatches, "the very-general-vs-all refusal must be recorded"


def test_refusal_reaches_ledger_and_reviewer_treats_it_as_earned(tmp_path: Path) -> None:
    from espresso_proof.loop_harness import run_loop_cycle
    from espresso_proof.loop_models import ReviewReport
    from espresso_proof.loop_reviewers import run_review_pass

    cases_file = tmp_path / "cases.yaml"
    write_yaml(
        cases_file,
        {
            "cases": [
                {
                    "case_id": "source_sensitive_very_general",
                    "claim": DISCIPLINE_CLAIM.strip(),
                    "case_family": "source_sensitive",
                    "expected_difficulty": "normal",
                    "known_truth_status": "evaluator_only_false",
                    "what_this_case_tests": ["source applicability"],
                    "must_not_claim": [
                        "a very-general source supports an all-objects claim"
                    ],
                }
            ]
        },
    )
    cycle_dir = run_loop_cycle(
        out_root=tmp_path / "loop_runs", mode="smoke", cases_path=cases_file
    )
    ledger = read_yaml(cycle_dir / "theorem_use_ledger.yaml")
    entries = [
        e for e in ledger["entries"]
        if e["case_id"] == "source_sensitive_very_general"
    ]
    assert entries, "theorem use must be recorded for the discipline case"
    assert any(e["missing_or_unclear_hypotheses"] for e in entries), (
        "the hypothesis mismatch must reach the theorem-use ledger"
    )
    assert all(e["step_status"] != "reference_supported" for e in entries)
    run_review_pass(cycle_dir)
    report = ReviewReport.model_validate(
        read_yaml(cycle_dir / "review_reports" / "source_applicability.yaml")
    )
    # the recorded refusal is the earned-correct outcome: no P1 for this case.
    assert not any(
        "source_sensitive_very_general" in f.what_happened
        and "stayed candidate" in f.what_happened
        for f in report.actionable_findings
    )
    assert any(
        "misapplication refusal recorded" in a.note for a in report.advisory_findings
    )


def test_true_theorem_case_still_gets_the_p1(tmp_path: Path) -> None:
    # the earned-refusal carve-out is for the source_sensitive family ONLY: a
    # true theorem with sources stuck at candidate is still a real gap.
    from espresso_proof.loop_harness import run_loop_cycle
    from espresso_proof.loop_models import ReviewReport
    from espresso_proof.loop_reviewers import run_review_pass

    cases_file = tmp_path / "cases.yaml"
    write_yaml(
        cases_file,
        {
            "cases": [
                {
                    "case_id": "p2_groups_true",
                    "claim": "Every group of order p^2, where p is prime, is abelian.",
                    "case_family": "true_theorem",
                    "expected_difficulty": "smoke",
                    "known_truth_status": "evaluator_only_true",
                    "what_this_case_tests": ["source grounding"],
                    "must_not_claim": ["Espresso proved the theorem"],
                }
            ]
        },
    )
    cycle_dir = run_loop_cycle(
        out_root=tmp_path / "loop_runs", mode="smoke", cases_path=cases_file
    )
    run_review_pass(cycle_dir)
    report = ReviewReport.model_validate(
        read_yaml(cycle_dir / "review_reports" / "source_applicability.yaml")
    )
    findings = [
        f for f in report.actionable_findings if "p2_groups_true" in f.what_happened
    ]
    assert findings, "true-theorem candidate-stuck sources must still be flagged"


def test_unclear_conditions_alone_do_not_count_as_refusal(tmp_path: Path) -> None:
    # Review-round hardening: the carve-out keys on the replay probe's exact
    # predicate over the run's OWN grounding. A ledger entry whose
    # missing_or_unclear_hypotheses came only from unclear/not_checked
    # applicability conditions (external-mode shape: the misapplication
    # machinery never engaged) must still draw the P1, never the advisory.
    from espresso_proof.loop_harness import run_loop_cycle
    from espresso_proof.loop_models import ReviewReport
    from espresso_proof.loop_reviewers import run_review_pass

    cases_file = tmp_path / "cases.yaml"
    write_yaml(
        cases_file,
        {
            "cases": [
                {
                    "case_id": "source_sensitive_very_general",
                    "claim": DISCIPLINE_CLAIM.strip(),
                    "case_family": "source_sensitive",
                    "expected_difficulty": "normal",
                    "known_truth_status": "evaluator_only_false",
                    "what_this_case_tests": ["source applicability"],
                    "must_not_claim": [
                        "a very-general source supports an all-objects claim"
                    ],
                }
            ]
        },
    )
    cycle_dir = run_loop_cycle(
        out_root=tmp_path / "loop_runs", mode="smoke", cases_path=cases_file
    )
    # strip every recorded mismatch from the run's grounding, leaving the
    # stale ledger notes in place — the reviewer must not trust the ledger.
    manifest = read_yaml(cycle_dir / "run_manifest.yaml")
    stripped = 0
    for run in manifest["runs"]:
        grounding_path = Path(run["run_dir"]) / "source_grounding.yaml"
        if not grounding_path.exists():
            continue
        grounding = read_yaml(grounding_path)
        for item in grounding.get("steps", []) + grounding.get("source_refs", []):
            match = item.get("hypothesis_match")
            if match and match.get("mismatches"):
                match["mismatches"] = []
                stripped += 1
        write_yaml(grounding_path, grounding)
    assert stripped, "the smoke run must have recorded mismatches to strip"
    ledger = read_yaml(cycle_dir / "theorem_use_ledger.yaml")
    assert any(e["missing_or_unclear_hypotheses"] for e in ledger["entries"]), (
        "the stale ledger notes must survive so the test exercises the drift"
    )
    run_review_pass(cycle_dir)
    report = ReviewReport.model_validate(
        read_yaml(cycle_dir / "review_reports" / "source_applicability.yaml")
    )
    assert any(
        "source_sensitive_very_general" in f.what_happened
        and "stayed candidate" in f.what_happened
        for f in report.actionable_findings
    ), "without a recorded mismatch in the grounding the P1 must stay"
    assert not any(
        "misapplication refusal recorded" in a.note
        for a in report.advisory_findings
    )


def test_mismatch_notes_survive_condition_truncation(tmp_path: Path) -> None:
    # the recorded mismatch is the refusal signal: it takes the ledger slots
    # first and can never be crowded out by eight unclear conditions.
    from espresso_proof.loop_harness import _collect_theorem_use, load_cases

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    write_yaml(
        run_dir / "run_manifest.yaml",
        {
            "artifacts": {
                "source_grounding": "source_grounding.yaml",
                "theorem_applicability": "theorem_applicability.yaml",
            }
        },
    )
    write_yaml(
        run_dir / "source_grounding.yaml",
        {
            "steps": [
                {
                    "proof_step_id": "s1",
                    "status": "misapplied",
                    "source_refs": ["src"],
                    "hypothesis_match": {
                        "matches": False,
                        "mismatches": ["very general hypothesis used as all"],
                    },
                }
            ],
            "source_refs": [{"id": "src", "title": "T", "status": "candidate"}],
        },
    )
    write_yaml(
        run_dir / "theorem_applicability.yaml",
        {
            "usages": [
                {
                    "proof_step_id": "s1",
                    "conditions": [
                        {"text": f"condition {n}", "status": "not_checked"}
                        for n in range(10)
                    ],
                }
            ]
        },
    )
    case = next(c for c in load_cases(None) if c.case_family == "source_sensitive")
    entries = _collect_theorem_use({"case": case, "run_dir": run_dir})
    assert entries
    notes = entries[0].missing_or_unclear_hypotheses
    assert len(notes) <= 8
    assert "very general hypothesis used as all" in notes


def test_replay_probe_source_mismatch_refusal(tmp_path: Path) -> None:
    from espresso_proof.loop_replay import recorded_misapplication_refusal as probe

    ok, note = probe(tmp_path)
    assert not ok and "no source_grounding.yaml" in note
    write_yaml(tmp_path / "source_grounding.yaml", {"claim_id": "c", "steps": [], "source_refs": []})
    ok, note = probe(tmp_path)
    assert not ok and "no hypothesis-mismatch refusal" in note
    write_yaml(
        tmp_path / "source_grounding.yaml",
        {
            "claim_id": "c",
            "steps": [],
            "source_refs": [
                {"id": "s", "status": "source_backed", "hypothesis_match": {"matches": True, "mismatches": []}}
            ],
        },
    )
    ok, note = probe(tmp_path)
    assert not ok and "must never earn support" in note
    write_yaml(
        tmp_path / "source_grounding.yaml",
        {
            "claim_id": "c",
            "steps": [
                {
                    "proof_step_id": "s1",
                    "status": "misapplied",
                    "hypothesis_match": {
                        "matches": False,
                        "mismatches": ["very general hypothesis used as all"],
                    },
                }
            ],
            "source_refs": [],
        },
    )
    ok, note = probe(tmp_path)
    assert ok and "refusal recorded" in note
