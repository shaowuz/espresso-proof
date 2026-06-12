"""Goal 42E cycle 3: machine-answered prover blockers + honest gap routing."""

from __future__ import annotations

from pathlib import Path

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.llm_agents import _split_machine_answered_blockers

BLOCKERS = [
    "Exact theorem supporting the main reduction.",
    "Source or exact computation for the most dangerous example.",
    "A sketch-only argument cannot become proof-complete evidence.",
]


def _pv_report(claim_id: str, *, confirmed: bool) -> dict:
    return {
        "schema_version": 1,
        "claim_id": claim_id,
        "run_id": "r",
        "generated_at": "2026-06-12T00:00:00+00:00",
        "entries": [
            {
                "id": "e1",
                "verifier_type": "finite_identity_table",
                "claim_id": claim_id,
                "target": "t",
                "input_summary": "i",
                "result": "holds",
                "trusted": True,
                "confirmed": confirmed,
                "earned_status": "instance_checked" if confirmed else "unverified",
                "scope_note": "this table only",
            }
        ],
        "notes": [],
    }


REQUESTS = {
    "Exact theorem supporting the main reduction.",
    "Source or exact computation for the most dangerous example.",
}


def test_exact_computation_blocker_answered_only_with_confirmed_evidence(
    tmp_path: Path,
) -> None:
    write_yaml(tmp_path / "proof_verification.yaml", _pv_report("c1", confirmed=True))
    open_blockers, answered = _split_machine_answered_blockers(
        list(BLOCKERS), run_dir=tmp_path, claim_id="c1", source_request_texts=REQUESTS
    )
    assert [a["request"] for a in answered] == [
        "Source or exact computation for the most dangerous example."
    ]
    # the answer is BOUND to the discharging entries.
    assert answered[0]["entry_ids"] == ["e1"]
    # source/theorem requests and standing risks are NEVER machine-answered.
    assert "Exact theorem supporting the main reduction." in open_blockers
    assert "A sketch-only argument cannot become proof-complete evidence." in open_blockers


def test_specific_requests_and_non_source_provenance_stay_open(tmp_path: Path) -> None:
    # Kernel Safety + Math Workflow blockers: request-blind matching is
    # forbidden. A request naming SPECIFIC objects must never be discharged
    # by unrelated claim-scoped evidence, and a non-source-request item must
    # never be answered no matter how it is phrased.
    write_yaml(tmp_path / "proof_verification.yaml", _pv_report("c1", confirmed=True))
    specific = "Exact computation of the Hodge numbers requires the source's spectral sequence."
    open_blockers, answered = _split_machine_answered_blockers(
        [specific], run_dir=tmp_path, claim_id="c1", source_request_texts={specific}
    )
    assert answered == [] and open_blockers == [specific]
    # generic text but NOT a source request (e.g. a missing hypothesis):
    generic = "Source or exact computation for the most dangerous example."
    open_blockers, answered = _split_machine_answered_blockers(
        [generic], run_dir=tmp_path, claim_id="c1", source_request_texts=set()
    )
    assert answered == [] and open_blockers == [generic]
    # variant phrasing of the generic family still answers (case/space only).
    variant = "source OR exact computation for the most dangerous case"
    open_blockers, answered = _split_machine_answered_blockers(
        [variant], run_dir=tmp_path, claim_id="c1", source_request_texts={variant}
    )
    assert [a["request"] for a in answered] == [variant]


def test_nothing_answered_without_evidence_or_on_claim_mismatch(tmp_path: Path) -> None:
    # no proof_verification.yaml at all
    assert _split_machine_answered_blockers(
        list(BLOCKERS), run_dir=tmp_path, claim_id="c1", source_request_texts=REQUESTS
    ) == (BLOCKERS, [])
    # unconfirmed entry
    write_yaml(tmp_path / "proof_verification.yaml", _pv_report("c1", confirmed=False))
    assert _split_machine_answered_blockers(
        list(BLOCKERS), run_dir=tmp_path, claim_id="c1", source_request_texts=REQUESTS
    ) == (BLOCKERS, [])
    # confirmed but for a DIFFERENT claim
    write_yaml(tmp_path / "proof_verification.yaml", _pv_report("other", confirmed=True))
    assert _split_machine_answered_blockers(
        list(BLOCKERS), run_dir=tmp_path, claim_id="c1", source_request_texts=REQUESTS
    ) == (BLOCKERS, [])
    # no run_dir context
    assert _split_machine_answered_blockers(
        list(BLOCKERS), run_dir=None, claim_id="c1", source_request_texts=REQUESTS
    ) == (BLOCKERS, [])


def test_e2e_p2_run_resolves_computation_request_and_keeps_source_requests(
    tmp_path: Path,
) -> None:
    from espresso_proof.io import write_text
    from espresso_proof.orchestrator import orchestrate_run, resume_run
    from espresso_proof.validators import validate_run

    input_path = tmp_path / "input.md"
    write_text(input_path, "Every group of order p^2, where p is prime, is abelian.\n")
    result = orchestrate_run(
        project="probe_blockers",
        input_path=input_path,
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    brief = read_yaml(run_dir / manifest["artifacts"]["strategy_brief"])
    selected = brief["top_strategies"][0]["id"] if brief.get("top_strategies") else "none"
    resume_run(
        run_dir=run_dir,
        selected_strategy=selected,
        effort="normal_pass",
        prover_provider="stub",
    )
    validate_run(run_dir)
    # evidence was wired BEFORE the prover pass (orchestrator ordering).
    assert (run_dir / "proof_verification.yaml").exists()
    loop = read_yaml(run_dir / "prover_loop.yaml")
    all_blockers = [
        text for attempt in loop.get("attempts", []) for text in attempt.get("blockers", [])
    ]
    assert all_blockers, "stub reviewers still raise open blockers"
    assert not any("exact computation" in b.lower() for b in all_blockers), (
        "the exact-computation request must be machine-answered"
    )
    # source/theorem requests honestly remain open.
    assert any("theorem" in b.lower() for b in all_blockers)
    # the resolution is auditable: the note QUOTES the answered request and
    # names the discharging entries.
    noted = [
        a for a in loop["attempts"]
        if "Machine-answered reviewer request(s)" in a.get("next_action", "")
    ]
    assert noted, "machine-answered requests must be recorded transparently"
    assert any(
        "Source or exact computation for the most dangerous example." in a["next_action"]
        and "auto_req_fg_p2_g1_commutative" in a["next_action"]
        for a in noted
    )


def test_e2e_run_without_evidence_keeps_all_blockers(tmp_path: Path) -> None:
    from espresso_proof.io import write_text
    from espresso_proof.orchestrator import orchestrate_run

    input_path = tmp_path / "input.md"
    write_text(input_path, "Every group of order 6 is abelian.\n")
    result = orchestrate_run(
        project="probe_noev",
        input_path=input_path,
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    loop = read_yaml(run_dir / "prover_loop.yaml")
    all_blockers = [
        text for attempt in loop.get("attempts", []) for text in attempt.get("blockers", [])
    ]
    assert any("exact computation" in b.lower() for b in all_blockers), (
        "without machine evidence nothing may be answered"
    )


def test_blocker_taxonomy_routes_theorem_requests_to_source_gap(tmp_path: Path) -> None:
    from espresso_proof.loop_harness import run_loop_cycle

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
                    "what_this_case_tests": ["proof-search progress"],
                    "must_not_claim": ["Espresso proved the theorem"],
                }
            ]
        },
    )
    cycle_dir = run_loop_cycle(
        out_root=tmp_path / "loop_runs", mode="smoke", cases_path=cases_file
    )
    blockers = read_yaml(cycle_dir / "blockers.yaml")["blockers"]
    by_text = {b["blocker_text"]: b["blocking_evidence_type"] for b in blockers}
    assert by_text["Exact theorem supporting the main reduction."] == "source_gap"
    assert by_text["Exact locator for every external theorem step."] == "source_gap"
    # the machine-answered computation request no longer appears at all.
    assert not any("exact computation" in t.lower() for t in by_text)


def test_registry_required_blocker_families_route_to_goal_proposals(tmp_path: Path) -> None:
    # Goal 42E cycle 3: a recurring source/theorem-request blocker cannot be
    # honestly machine-resolved (fabricated citations are forbidden), so its
    # P1 finding must carry the oversize marker, and triage must never SELECT
    # such a family as the cycle's gap (when one heads the priority order it
    # becomes a new-goal proposal and selection moves on).
    from espresso_proof.loop_harness import run_loop_cycle
    from espresso_proof.loop_models import ReviewReport
    from espresso_proof.loop_reviewers import run_review_pass
    from espresso_proof.loop_triage import run_triage
    from tests.loop_gap_case import gap_cases

    cases_file = tmp_path / "cases.yaml"
    write_yaml(cases_file, gap_cases())
    root = tmp_path / "loop_runs"
    run_loop_cycle(out_root=root, mode="smoke", cases_path=cases_file)
    second = run_loop_cycle(out_root=root, mode="smoke", cases_path=cases_file)
    run_review_pass(second)
    report = ReviewReport.model_validate(
        read_yaml(second / "review_reports" / "proof_search.yaml")
    )
    registry = [
        f for f in report.actionable_findings
        if "theorem supporting" in f.what_happened
        or "locator" in f.what_happened.lower()
    ]
    assert registry, "recurring source-request families must still be reported"
    assert all("broad rewrite" in f.recommended_fix for f in registry)
    decision = run_triage(second)
    if decision.selected_gap_id.startswith("proof_search"):
        selected = next(
            f for f in report.actionable_findings
            if f.finding_id == decision.selected_gap_id
        )
        assert "broad rewrite" not in selected.recommended_fix
