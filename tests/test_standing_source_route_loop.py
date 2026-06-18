"""Goal 42E cycle 7: engaged-candidate standing gap routes to a goal proposal."""

from __future__ import annotations

from pathlib import Path

from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.loop.loop_reviewers import source_support_fix
from espresso_proof.loop.loop_triage import _BROAD_FIX_MARKERS

P2_CLAIM = "Every group of order p^2, where p is prime, is abelian.\n"


def _p2_cases_file(tmp_path: Path) -> Path:
    cases_file = tmp_path / "cases.yaml"
    write_yaml(
        cases_file,
        {
            "cases": [
                {
                    "case_id": "p2_groups_true",
                    "claim": P2_CLAIM.strip(),
                    "case_family": "true_theorem",
                    "expected_difficulty": "smoke",
                    "known_truth_status": "evaluator_only_true",
                    "what_this_case_tests": ["source grounding"],
                    "must_not_claim": ["Espresso proved the theorem"],
                }
            ]
        },
    )
    return cases_file


def test_routing_predicate_is_scoped() -> None:
    # smoke + engaged -> oversized for the loop (goal proposal)...
    standing = source_support_fix(smoke_only=True, engaged=True)
    assert any(marker in standing for marker in _BROAD_FIX_MARKERS)
    # ...every other shape stays a bounded-fix target.
    for smoke_only, engaged in ((True, False), (False, True), (False, False)):
        bounded = source_support_fix(smoke_only=smoke_only, engaged=engaged)
        assert not any(marker in bounded for marker in _BROAD_FIX_MARKERS), (
            smoke_only,
            engaged,
        )


def test_engaged_candidate_p1_keeps_evidence_and_routes_to_proposal(
    tmp_path: Path,
) -> None:
    from espresso_proof.loop.loop_harness import run_loop_cycle
    from espresso_proof.kernel.loop_models import ReviewReport
    from espresso_proof.loop.loop_reviewers import run_review_pass
    from espresso_proof.loop.loop_triage import run_triage

    cycle_dir = run_loop_cycle(
        out_root=tmp_path / "loop_runs",
        mode="smoke",
        cases_path=_p2_cases_file(tmp_path),
    )
    run_review_pass(cycle_dir)
    report = ReviewReport.model_validate(
        read_yaml(cycle_dir / "review_reports" / "source_applicability.yaml")
    )
    # the honest standing P1 keeps its evidence — no gap-hiding...
    standing = [
        f
        for f in report.actionable_findings
        if "p2_groups_true" in f.what_happened
        and "stayed candidate" in f.what_happened
    ]
    assert standing and all(f.severity == "P1" for f in standing)
    assert all(f.evidence_path for f in standing)
    # ...but it now carries the oversize routing, so triage converts it to a
    # goal proposal instead of selecting an unfixable gap.
    assert all(
        any(marker in f.recommended_fix for marker in _BROAD_FIX_MARKERS)
        for f in standing
    )
    standing_ids = {f.finding_id for f in standing}
    try:
        decision = run_triage(cycle_dir)
    except ValueError as exc:
        # single-case cycle: every remaining finding is standing — triage
        # correctly writes proposals and refuses any autofix this cycle.
        assert "every candidate finding is oversized" in str(exc)
    else:
        assert decision.selected_gap_id not in standing_ids
    proposals = {p.stem for p in (cycle_dir / "new_goal_proposals").glob("*.md")}
    assert standing_ids <= proposals, (
        "the standing finding must be converted to a goal proposal"
    )


def test_unengaged_grounding_still_a_bounded_target(tmp_path: Path) -> None:
    # strip the engagement (attribution) from the run's OWN grounding — the
    # surface the reviewer's routing predicate actually reads: the finding
    # must go back to the bounded-fix message, because a weak grounding
    # shape (e.g. missing attribution) is a gap the loop CAN fix and the
    # routing must not over-generalize.
    from espresso_proof.loop.loop_harness import run_loop_cycle
    from espresso_proof.kernel.loop_models import ReviewReport
    from espresso_proof.loop.loop_reviewers import run_review_pass

    cycle_dir = run_loop_cycle(
        out_root=tmp_path / "loop_runs",
        mode="smoke",
        cases_path=_p2_cases_file(tmp_path),
    )
    manifest = read_yaml(cycle_dir / "run_manifest.yaml")
    stripped = 0
    for run in manifest["runs"]:
        grounding_path = Path(run["run_dir"]) / "source_grounding.yaml"
        if not grounding_path.exists():
            continue
        grounding = read_yaml(grounding_path)
        for ref in grounding.get("source_refs", []):
            if ref.get("authors"):
                ref["authors"] = []
                stripped += 1
        write_yaml(grounding_path, grounding)
    assert stripped, "the smoke run must have an attributed reference to strip"
    run_review_pass(cycle_dir)
    report = ReviewReport.model_validate(
        read_yaml(cycle_dir / "review_reports" / "source_applicability.yaml")
    )
    standing = [
        f
        for f in report.actionable_findings
        if "p2_groups_true" in f.what_happened
        and "stayed candidate" in f.what_happened
    ]
    assert standing
    assert not any(
        any(marker in f.recommended_fix for marker in _BROAD_FIX_MARKERS)
        for f in standing
    )


def test_replay_probe_engaged_candidates_routed(tmp_path: Path) -> None:
    from espresso_proof.loop.loop_replay import (
        _probe_engaged_candidates_routed_to_goal as probe,
    )

    # fail closed: nothing to classify.
    ok, note = probe(tmp_path)
    assert not ok and "nothing to classify" in note
    # an engaged grounding with the shared predicate present passes.
    write_yaml(
        tmp_path / "source_grounding.yaml",
        {
            "claim_id": "c",
            "steps": [{"proof_step_id": "s1", "status": "candidate", "source_refs": ["r"]}],
            "source_refs": [
                {
                    "id": "r",
                    "title": "Classical group theory text",
                    "authors": ["Placeholder Author"],
                    "status": "candidate",
                    "locator": {"kind": "theorem", "value": "Corollary (class equation)"},
                    "hypothesis_match": {"matches": True, "mismatches": []},
                }
            ],
        },
    )
    ok, note = probe(tmp_path)
    assert ok and "routes to a dedicated goal" in note
