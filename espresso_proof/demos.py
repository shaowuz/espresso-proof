"""Reliable mathematical showcase demos (Goal 32).

Each demo is a one-click, fresh-checkout-reproducible run built through the
EXISTING kernel paths — no validator is bypassed and nothing is overclaimed:

- ``p2-group``    — fast core pass + the trusted finite-group verifier machine-
                    checks p = 2 and p = 3 (earned ``instance_checked``, scoped).
- ``order6-false``— a kernel-validated refutation fixture: S3 satisfies every
                    hypothesis of "every group of order 6 is abelian" and fails
                    the conclusion; the refutation hard gate (Goal 21) accepts it.
- ``evt-compactness`` — a missing-hypothesis claim; the advisory judgment +
                    failure-pattern layers flag risk, and nothing is refuted or
                    proved by a missing hypothesis.
- ``matrix-rank`` — fast core pass + the trusted matrix verifier machine-checks
                    rank([[1,2,3],[4,5,6],[7,8,9]]) = 2 (earned, this matrix only).

Demos are NOT a run mode: "Fast preview" and "Codex deep run" are modes; "Open
demo" opens a pre-validated showcase run. Demo metadata here is display copy —
it never sets or upgrades any proof/source/evidence/grade/claim status, and
``create_demo_run`` ends with ``validate_run`` (fail closed).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from .agent_run_status import set_agent_status
from .io import read_yaml, write_yaml
from .orchestrator import orchestrate_run
from .validators import validate_run

# Demo registry: id -> display card (Goal 32 §2/§7 fields). Pure copy.
DEMOS: dict[str, dict[str, str]] = {
    "p2-group": {
        "title": "p² group theorem",
        "claim": "Every group of order p^2, where p is prime, is abelian.",
        "demonstrates": "Honest scoped checking on a true classical theorem.",
        "lesson": (
            "Machine-checked groups for p = 2 and p = 3 earn green 'checked in this "
            "finite/computational scope' — and Espresso still refuses to call the "
            "universal claim proved."
        ),
        "first_result": "Claim card, preliminary routes, and four machine-checked groups (every group of order 4 and 9).",
        "next_click": "Run Examples in the Evidence Agents panel.",
        "trust_boundary": (
            "The p = 2 and p = 3 checks are machine-verified by a trusted finite-group "
            "verifier; the universal claim is NOT proved and the routes stay preliminary."
        ),
    },
    "order6-false": {
        "title": "Order-6 abelian (false)",
        "claim": "Every group of order 6 is abelian.",
        "demonstrates": "The refutation hard gate on a false finite-group claim.",
        "lesson": (
            "S3 satisfies every hypothesis and fails the conclusion, so the claim is "
            "refuted — and only an admissible checked counterexample can do that."
        ),
        "first_result": "A refuted claim with the S3 counterexample and the honest red framing.",
        "next_click": "Read the counterexample in the Evidence Agents panel.",
        "trust_boundary": (
            "The S3 facts are seeded as a checked test for this demo; the kernel's "
            "admissibility gate (all hypotheses satisfied + checked failure) is what "
            "accepts the refutation. A dropped-hypothesis example could not."
        ),
    },
    "evt-compactness": {
        "title": "EVT without compactness",
        "claim": "Every continuous real function on an open interval attains a maximum.",
        "demonstrates": "Missing-hypothesis discipline on a classic trap.",
        "lesson": (
            "A missing hypothesis triggers advisory risk flags and keeps the route "
            "preliminary — it neither proves nor refutes anything by itself."
        ),
        "first_result": "Advisory risk warnings and a preliminary route; no refutation, no proof.",
        "next_click": "Run Counterexamples in the Evidence Agents panel.",
        "trust_boundary": (
            "Risk flags are advisory: Espresso does not refute the claim from a missing "
            "hypothesis, and nothing here earns checked or reference-supported status."
        ),
    },
    "matrix-rank": {
        "title": "Matrix rank check",
        "claim": "The 3x3 integer matrix [[1,2,3],[4,5,6],[7,8,9]] has rank 2.",
        "demonstrates": "A scoped finite/computational check, earned by a machine.",
        "lesson": (
            "The trusted matrix verifier confirms rank = 2 for THIS matrix — green, "
            "scoped, and explicitly not a statement about any other matrix."
        ),
        "first_result": "One machine-checked instance in the proof-route step table.",
        "next_click": "Open the Proof-Route Steps table to see the scoped check.",
        "trust_boundary": (
            "The check is exact and machine-verified for this single matrix; it is "
            "never generalized."
        ),
    },
}

DEMO_PROJECT_PREFIX = "demo-"


def demo_for_project(project_name: str) -> dict[str, str] | None:
    """The demo card for a run's project directory name, if it is a demo run."""

    if not project_name.startswith(DEMO_PROJECT_PREFIX):
        return None
    return DEMOS.get(project_name[len(DEMO_PROJECT_PREFIX):])


def _fast_run(root: Path, demo_id: str, claim: str) -> Path:
    inbox = root / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    input_path = inbox / f"demo-{uuid.uuid4().hex}.md"
    input_path.write_text(claim + "\n", encoding="utf-8")
    try:
        result = orchestrate_run(
            project=f"{DEMO_PROJECT_PREFIX}{demo_id}",
            input_path=input_path,
            output_root=root,
            provider="stub",
            front_half="stub",
            fast=True,
        )
    finally:
        input_path.unlink(missing_ok=True)
    return result["run_dir"]


def _seed_order6_refutation(run_dir: Path) -> None:
    """Overwrite the scaffolded artifacts with a coherent, kernel-validated
    S3 refutation set (modeled 1:1 on the proven arithmetic refutation fixture).

    Every file goes through the same schemas + ``validate_run``; the refutation
    is accepted ONLY because the kernel's admissibility gate holds (satisfies all
    hypotheses + checked failed result). Nothing bypasses a validator.
    """

    from .mock_pipeline import _parallel_review  # proven fixture review shape

    claim_id = read_yaml(run_dir / "locked_claim.yaml")["id"]
    claim = {
        "id": claim_id,
        "raw_text": "Every group of order 6 is abelian.",
        "normalized_statement": "Every group of order 6 is abelian.",
        "objects": ["finite group G"],
        "hypotheses": [
            {"name": "G is a group", "status": "explicit", "source": "user"},
            {"name": "|G| = 6", "status": "explicit", "source": "user"},
        ],
        "conclusion": "G is abelian",
        "ambiguous_terms": [],
        "locked": True,
        "repaired_from": None,
        "human_confirmed": False,
    }
    write_yaml(run_dir / "locked_claim.yaml", claim)
    write_yaml(run_dir / "hypothesis_ledger.yaml", {
        "claim_id": claim_id,
        "hypotheses": claim["hypotheses"],
        "missing_or_unclear": [],
        "notes": ["The claim is precise enough to test by an all-hypotheses example."],
    })
    write_yaml(run_dir / "literature_review.yaml", {
        "claim_id": claim_id,
        "query_summary": "No literature is needed for this finite-group counterexample demo.",
        "candidate_references": [],
        "known_examples": ["the symmetric group S3"],
        "known_counterexample_families": ["nonabelian groups of order 2p"],
        "notes": ["This demo shows refutation discipline, not source search."],
    })
    write_yaml(run_dir / "examples.yaml", {
        "claim_id": claim_id,
        "examples": [{
            "id": "ex_s3",
            "template_id": "symmetric_group",
            "name": "The symmetric group S3",
            "satisfies_all_claim_hypotheses": True,
            "hypothesis_mismatches": [],
            "what_it_tests": ["all hypotheses", "commutativity of the conclusion"],
            "expected_conclusion": "S3 should be abelian if the claim were true.",
            "observed_or_inferred_behavior": "(12)(13) = (132) but (13)(12) = (123); S3 is nonabelian.",
            "result_for_current_idea": "failed",
            "source_or_reason": "Direct computation with permutations.",
            "nontriviality": "routine",
        }],
    })
    write_yaml(run_dir / "computation_checks.yaml", {
        "claim_id": claim_id,
        "checks": [{
            "id": "check_s3_order",
            "example_id": "ex_s3",
            "claim_id": claim_id,
            "computed_quantity": "|S3|",
            "input_data": ["S3 = permutations of 3 letters"],
            "method": "exact_arithmetic",
            "tool_or_source": "exact arithmetic (3! = 6)",
            "result": "6",
            "status": "checked",
            "notes": "The counterexample satisfies the order-6 hypothesis exactly.",
        }],
    })
    write_yaml(run_dir / "counterexamples.yaml", {
        "claim_id": claim_id,
        "tests": [{
            "id": "test_s3",
            "operator_id": "satisfy_all_hypotheses_test",
            "example_id": "ex_s3",
            "satisfies_all_claim_hypotheses": True,
            "target": "theorem",
            "result": "failed",
            "refutes_claim": True,
            "refutes_step_only": False,
            "exposes_missing_hypothesis": False,
            "explanation": "S3 has order 6, satisfies every hypothesis, and is not abelian.",
            "repaired_claim_suggestion": "Restrict to groups of prime order, or of order p^2.",
        }],
    })
    write_yaml(run_dir / "tests.yaml", {
        "claim_id": claim_id,
        "target": "claim",
        "summary": "A checked all-hypotheses counterexample refutes the locked claim.",
        "tests": [{
            "id": "test_report_s3",
            "description": "Check S3 against the locked claim.",
            "uses_examples": ["ex_s3"],
            "uses_counterexamples": ["test_s3"],
            "result": "failed",
            "evidence_status": "checked",
            "main_observation": "The conclusion fails while every stated hypothesis holds.",
            "next_action": "Repair the claim (e.g. prime order or p^2) or abandon it.",
        }],
    })
    write_yaml(run_dir / "review.yaml", _parallel_review(
        claim_id,
        attempt_status="failed",
        verdict="refuted",
        strategy_verdict="fail",
        evidence_verdict="fail",
        line_verdict="unclear",
        line_applicability="sketch_only",
        strongest_objection="A checked example (S3) satisfies all hypotheses and violates the conclusion.",
        example_risks=["test_s3 refutes the locked claim"],
        downgrade_reasons=["All-hypotheses counterexample found."],
    ))
    write_yaml(run_dir / "idea_grade.yaml", {
        "claim_id": claim_id,
        "status": "refuted",
        "score": 3,
        "confidence": "high",
        "reasons": ["The checked example S3 refutes the locked claim."],
        "survived_tests": [],
        "failed_tests": ["test_s3"],
        "counterexample_candidates": ["test_s3"],
        "missing_hypotheses": [],
        "source_gaps": [],
        "repair_options": ["Restrict the order (prime, or p^2), or abandon the statement."],
        "next_cheapest_test": "Decide whether to repair the claim or discard it.",
        "human_review_required": True,
        "proof_language_status": "idea_sketch",
    })
    # The scaffold's final report text described an unclear claim; replace it
    # with a conservative line coherent with the refuted state (display only).
    (run_dir / "final_report.md").write_text(
        "Refuted: the symmetric group S3 satisfies every hypothesis of the locked "
        "claim and is not abelian. The refutation rests on this admissible checked "
        "counterexample; no proof or repair is claimed.\n",
        encoding="utf-8",
    )
    # Process board: the demo's example/counterexample evidence exists and is
    # validated, so those agents read Done; literature was not needed.
    set_agent_status(run_dir, "examples", "done", finished_at=None, increment_attempt=True)
    set_agent_status(run_dir, "counterexamples", "done", finished_at=None, increment_attempt=True)


def create_demo_run(root: Path, demo_id: str) -> Path:
    """Create one demo run from a fresh root. Deterministic, model-free, and
    kernel-validated end to end (fail closed). Returns the run directory."""

    if demo_id not in DEMOS:
        raise ValueError(f"unknown demo: {demo_id}")
    root = Path(root)
    claim = DEMOS[demo_id]["claim"]
    run_dir = _fast_run(root, demo_id, claim)

    if demo_id == "p2-group":
        from .proof_verification import verify_finite_groups

        # Trusted verifier: machine-checks p = 2 and p = 3 (earned, scoped).
        verify_finite_groups(run_dir, primes=[2, 3])
    elif demo_id == "matrix-rank":
        from .proof_verification import verify_matrix_rank_in_run

        verify_matrix_rank_in_run(
            run_dir,
            matrix=[[1, 2, 3], [4, 5, 6], [7, 8, 9]],
            entry_id="demo_matrix_rank",
            expected_rank=2,
            target="rank of the explicit 3x3 integer matrix",
        )
    elif demo_id == "evt-compactness":
        from .failure_pattern_bank import audit_failure_patterns
        from .mathematical_judgment import judge_run

        # Advisory layers only: risk flags, never a status change.
        judge_run(run_dir)
        audit_failure_patterns(run_dir)
    elif demo_id == "order6-false":
        _seed_order6_refutation(run_dir)

    validate_run(run_dir)
    # Refresh the dashboard so the demo opens with its evidence visible.
    from .run_dashboard import write_run_dashboard

    write_run_dashboard(run_dir)
    return run_dir


__all__ = ["DEMOS", "DEMO_PROJECT_PREFIX", "demo_for_project", "create_demo_run"]
