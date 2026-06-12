"""Goal 42E cycle 6: true-theorem source engagement (p^2-abelian dogfood)."""

from __future__ import annotations

from pathlib import Path

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.mock_pipeline import _detect_scenario

P2_CLAIM = "Every group of order p^2, where p is prime, is abelian.\n"


def test_replay_probe_true_theorem_source_engaged(tmp_path: Path) -> None:
    from espresso_proof.loop_replay import _probe_true_theorem_source_engaged as probe

    ok, note = probe(tmp_path)
    assert not ok and "no source_grounding.yaml" in note
    write_yaml(
        tmp_path / "source_grounding.yaml",
        {"claim_id": "c", "steps": [], "source_refs": []},
    )
    ok, note = probe(tmp_path)
    assert not ok and "never engaged" in note
    # smoke mode can never earn reference support: fail closed.
    write_yaml(
        tmp_path / "source_grounding.yaml",
        {
            "claim_id": "c",
            "steps": [
                {"proof_step_id": "s1", "status": "source_backed", "source_refs": ["r"]}
            ],
            "source_refs": [
                {
                    "id": "r",
                    "title": "T",
                    "status": "source_backed",
                    "locator": {"kind": "theorem", "value": "Theorem 1"},
                    "hypothesis_match": {"matches": True, "mismatches": []},
                }
            ],
        },
    )
    ok, note = probe(tmp_path)
    assert not ok and "never be earned" in note
    # an inexact locator, missing attribution, or an unresolved hypothesis
    # match is not engagement.
    write_yaml(
        tmp_path / "source_grounding.yaml",
        {
            "claim_id": "c",
            "steps": [{"proof_step_id": "s1", "status": "candidate", "source_refs": ["r"]}],
            "source_refs": [
                {
                    "id": "r",
                    "title": "T",
                    "status": "candidate",
                    "locator": {"kind": "unknown", "value": "to_be_filled"},
                    "hypothesis_match": {"matches": "unclear", "mismatches": []},
                }
            ],
        },
    )
    ok, note = probe(tmp_path)
    assert not ok and "exact locator" in note
    write_yaml(
        tmp_path / "source_grounding.yaml",
        {
            "claim_id": "c",
            "steps": [{"proof_step_id": "s1", "status": "candidate", "source_refs": ["r"]}],
            "source_refs": [
                {
                    "id": "r",
                    "title": "Classical group theory text",
                    "status": "candidate",
                    "locator": {"kind": "theorem", "value": "Corollary (class equation)"},
                    "hypothesis_match": {"matches": True, "mismatches": []},
                }
            ],
        },
    )
    ok, note = probe(tmp_path)
    assert not ok and "attribution" in note  # authors are required
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
    assert ok and "held honestly at candidate" in note


def test_p2_claim_routes_to_classical_source_scenario(tmp_path: Path) -> None:
    path = tmp_path / "case" / "input.md"
    assert _detect_scenario(path, P2_CLAIM) == "true_theorem_classical_source"
    # the false-abelian dogfood claim (order 6, composite) is unaffected.
    assert (
        _detect_scenario(path, "Every group of order 6 is abelian.\n")
        != "true_theorem_classical_source"
    )
    # anchored fullmatch (Kernel Safety): negated, weakened, primality-free,
    # or modified variants are DIFFERENT (often false) claims — the favorable
    # classical-source scaffold must never catch them.
    for variant in (
        "Every group of order p^2, where p is prime, is non-abelian.\n",
        "No group of order p^2 is abelian.\n",
        "Some group of order p^2, where p is prime, is not abelian.\n",
        "Every group of order p^2 is abelian.\n",  # missing primality clause
        "Every group of order p^2, where p is prime, is cyclic.\n",
    ):
        assert (
            _detect_scenario(path, variant) != "true_theorem_classical_source"
        ), variant
    # fixture inputs are still matched by directory name BEFORE text rules.
    fixture = tmp_path / "vague_claim_unclear" / "input.md"
    assert _detect_scenario(fixture, P2_CLAIM) == "vague_claim_unclear"


def test_p2_run_engages_grounding_and_validates(tmp_path: Path) -> None:
    from espresso_proof.io import write_text
    from espresso_proof.orchestrator import orchestrate_run
    from espresso_proof.validators import validate_run

    input_path = tmp_path / "input.md"
    write_text(input_path, P2_CLAIM)
    result = orchestrate_run(
        project="probe_true_theorem",
        input_path=input_path,
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    validate_run(run_dir)
    grounding = read_yaml(run_dir / "source_grounding.yaml")
    assert grounding["steps"], "source grounding must engage for the true theorem"
    assert grounding["source_refs"], "a classical reference must be cited"
    assert not any(
        item.get("status") == "source_backed"
        for item in grounding["steps"] + grounding["source_refs"]
    ), "smoke mode can never earn reference support"
    assert any(
        (ref.get("hypothesis_match") or {}).get("matches") is True
        and (ref.get("locator") or {}).get("kind") == "theorem"
        for ref in grounding["source_refs"]
    ), "the citation must carry an exact locator and a recorded hypothesis match"
    # the locked claim must carry the group-theory hypotheses, not the
    # algebraic-geometry placeholder triple.
    claim = read_yaml(run_dir / "locked_claim.yaml")
    names = " ".join(h["name"] for h in claim["hypotheses"]).lower()
    assert "p^2" in names or "order" in names
    assert "smooth" not in names and "projective" not in names


def test_ledger_engages_and_honest_p1_remains(tmp_path: Path) -> None:
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
    cycle_dir = run_loop_cycle(
        out_root=tmp_path / "loop_runs", mode="smoke", cases_path=cases_file
    )
    ledger = read_yaml(cycle_dir / "theorem_use_ledger.yaml")
    entries = [e for e in ledger["entries"] if e["case_id"] == "p2_groups_true"]
    assert entries, "theorem use must now be recorded for the true theorem"
    assert all(e["step_status"] != "reference_supported" for e in entries)
    assert any(e["exact_locator"] for e in entries)
    run_review_pass(cycle_dir)
    report = ReviewReport.model_validate(
        read_yaml(cycle_dir / "review_reports" / "source_applicability.yaml")
    )
    # the engagement gap (P2: nothing recorded at all) is gone...
    assert not any(
        "no theorem/source usage was recorded" in f.what_happened
        and "p2_groups_true" in f.what_happened
        for f in report.actionable_findings
    )
    # ...while the honest standing P1 (stuck at candidate without real
    # verification) remains AT P1: engagement is not evidence, and the
    # finding must not be quietly demoted.
    assert any(
        "p2_groups_true" in f.what_happened
        and "stayed candidate" in f.what_happened
        and f.severity == "P1"
        for f in report.actionable_findings
    )
