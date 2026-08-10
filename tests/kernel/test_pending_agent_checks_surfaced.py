"""An agent-proposed machine check that never ran must be visible, not silent.

Found by inspecting a live run. The general proposal channel worked — a
reviewer recorded a `verification_requests` entry asking for a finite-group
lookup — but the stage that harvests proposals into
`scoped_verification_requests.yaml` runs BEFORE the prover/reviewer pass and its
guard stops at the human gate. So the request existed, no deterministic verifier
had seen it, and every surface (final report, warnings) was silent about it. The
reader could only conclude the reviewers had asked for nothing.

The rule now: the difference between "proposed by an agent" and "harvested for
execution" is reported as a surface warning. This says nothing about whether a
request is valid or will ever run — only that no machine has looked at it.
"""

from __future__ import annotations

import yaml

from espresso_proof.kernel.surface_warnings import (
    AGENT_PROPOSED_CHECKS_PENDING,
    collect_surface_warnings,
    pending_agent_verification_requests,
    render_warning_lines,
)


def _write(path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")


def _request(request_id: str, kind: str = "named_finite_witness") -> dict:
    return {"id": request_id, "kind": kind, "target": "something checkable"}


def _run(tmp_path, *, reviews=None, harvested=None, prover=None, manifest=True):
    if manifest:
        _write(tmp_path / "run_manifest.yaml", {"artifacts": {}, "optional_artifacts": {}})
    _write(
        tmp_path / "llm_agent_run.yaml",
        {"prover_attempt": prover or {}, "verifier_reviews": reviews or []},
    )
    if harvested is not None:
        _write(
            tmp_path / "scoped_verification_requests.yaml",
            {"claim_id": "claim_001", "requests": harvested},
        )
    return tmp_path


# --- the gap itself


def test_an_unharvested_reviewer_request_is_reported(tmp_path) -> None:
    run = _run(tmp_path, reviews=[{"verification_requests": [_request("rev_001_req_00")]}])
    pending = pending_agent_verification_requests(run)
    assert [r["id"] for r in pending] == ["rev_001_req_00"]

    warnings = collect_surface_warnings(
        grade={}, review={}, examples={}, counterexamples={}, tests={},
        pending_agent_checks=pending,
    )
    entry = next(w for w in warnings if w["category"] == AGENT_PROPOSED_CHECKS_PENDING)
    assert entry["ids"] == ["rev_001_req_00"]
    text = entry["text"]
    assert "named_finite_witness" in text
    assert "no deterministic verifier" in text
    assert "rev_001_req_00" in text
    # The wording must not promise execution.
    assert "will run" not in text and "will be" not in text
    assert any("rev_001_req_00" in line for line in render_warning_lines(warnings))


def test_a_harvested_request_is_not_reported(tmp_path) -> None:
    run = _run(
        tmp_path,
        reviews=[{"verification_requests": [_request("rev_001_req_00")]}],
        harvested=[_request("rev_001_req_00")],
    )
    assert pending_agent_verification_requests(run) == []


def test_prover_requests_count_too(tmp_path) -> None:
    run = _run(tmp_path, prover={"verification_requests": [_request("prover_req_00")]})
    assert [r["id"] for r in pending_agent_verification_requests(run)] == ["prover_req_00"]


def test_later_rounds_are_scanned(tmp_path) -> None:
    run = _run(tmp_path)
    _write(
        run / "prover_rounds" / "round_002_x.yaml",
        {"verifier_reviews": [{"verification_requests": [_request("round2_req_00")]}]},
    )
    assert [r["id"] for r in pending_agent_verification_requests(run)] == ["round2_req_00"]


def test_the_same_request_is_reported_once(tmp_path) -> None:
    # A round file repeating an earlier record must not double-count.
    run = _run(tmp_path, reviews=[{"verification_requests": [_request("dup_req")]}])
    _write(
        run / "prover_rounds" / "round_001_x.yaml",
        {"verifier_reviews": [{"verification_requests": [_request("dup_req")]}]},
    )
    assert len(pending_agent_verification_requests(run)) == 1


# --- nothing to report must stay silent


def test_no_requests_yields_no_warning(tmp_path) -> None:
    run = _run(tmp_path, reviews=[{"objections": ["something"]}])
    assert pending_agent_verification_requests(run) == []
    warnings = collect_surface_warnings(
        grade={}, review={}, examples={}, counterexamples={}, tests={},
        pending_agent_checks=[],
    )
    assert all(w["category"] != AGENT_PROPOSED_CHECKS_PENDING for w in warnings)


def test_an_idless_request_cannot_produce_a_warning(tmp_path) -> None:
    # Without an id there is nothing a reader could act on or look up; counting
    # it would produce a number pointing at nothing.
    warnings = collect_surface_warnings(
        grade={}, review={}, examples={}, counterexamples={}, tests={},
        pending_agent_checks=[{"kind": "named_finite_witness"}, {"id": "   "}],
    )
    assert all(w["category"] != AGENT_PROPOSED_CHECKS_PENDING for w in warnings)


def test_missing_and_malformed_artifacts_fail_quiet(tmp_path) -> None:
    assert pending_agent_verification_requests(tmp_path) == []
    (tmp_path / "run_manifest.yaml").write_text(": not yaml :", encoding="utf-8")
    assert pending_agent_verification_requests(tmp_path) == []
    (tmp_path / "llm_agent_run.yaml").write_text("- not a mapping", encoding="utf-8")
    assert pending_agent_verification_requests(tmp_path) == []


def test_manifest_relocation_is_honoured(tmp_path) -> None:
    _write(
        tmp_path / "run_manifest.yaml",
        {
            "artifacts": {},
            "optional_artifacts": {
                "llm_agent_run": "nested/agents.yaml",
                "scoped_verification_requests": "nested/requests.yaml",
            },
        },
    )
    _write(
        tmp_path / "nested" / "agents.yaml",
        {"verifier_reviews": [{"verification_requests": [_request("a"), _request("b")]}]},
    )
    _write(tmp_path / "nested" / "requests.yaml", {"requests": [_request("a")]})
    assert [r["id"] for r in pending_agent_verification_requests(tmp_path)] == ["b"]


def test_the_real_run_shape_is_covered(tmp_path) -> None:
    # The exact shape observed live: one reviewer, one request, no harvest file.
    run = _run(
        tmp_path,
        reviews=[
            {"verifier_id": "strategy_reviewer_001",
             "verification_requests": [
                 {"id": "strategy_reviewer_001_req_00",
                  "kind": "named_finite_witness",
                  "witness": "smallest counterexample bound",
                  "identity": "",
                  "target": "smallest_counterexample_bound_for_odd_perfect_numbers"}]},
            {"verifier_id": "evidence_reviewer_002"},
            {"verifier_id": "line_proof_reviewer_003"},
        ],
    )
    pending = pending_agent_verification_requests(run)
    assert len(pending) == 1
    warnings = collect_surface_warnings(
        grade={}, review={}, examples={}, counterexamples={}, tests={},
        pending_agent_checks=pending,
    )
    assert any(w["category"] == AGENT_PROPOSED_CHECKS_PENDING for w in warnings)


def test_the_disclosure_line_survives_the_final_report_language_gate() -> None:
    """The kernel's own honesty line must not be flagged as dishonesty.

    Found live on a graduate-problem run: the pending-checks disclosure —
    "...no deterministic verifier has been run for them and they carry no
    evidence: <id>" — was rejected by the final-report support-misuse gate,
    which killed the whole run at validation. Two coincidences aligned: the
    request id contains "reviewer" (a context term) and the sentence contains
    "evidence" (a support term), and the disclaimer list knew "not evidence"
    but not the noun-negated form "carry no evidence".
    """

    from espresso_proof.kernel.validators.final_gates import (
        _first_final_report_unchecked_support_misuse,
    )

    warnings = collect_surface_warnings(
        grade={}, review={}, examples={}, counterexamples={}, tests={},
        pending_agent_checks=[
            {"id": "evidence_reviewer_002_req_00_e815075e", "kind": "named_finite_witness"}
        ],
    )
    text = "\n".join(render_warning_lines(warnings))
    assert "carry no evidence" in text
    assert _first_final_report_unchecked_support_misuse(text) is None

    # The gate must still catch a REAL misuse next to the honest line.
    dishonest = text + "\nThe reviewer approval supports the theorem for all primes."
    assert _first_final_report_unchecked_support_misuse(dishonest) is not None
