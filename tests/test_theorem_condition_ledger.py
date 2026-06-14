from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.io import read_yaml, write_yaml
from espresso_proof.models import (
    LedgerCondition,
    MatchStatus,
    TheoremConditionLedger,
    TheoremConditionLedgerEntry,
)
from espresso_proof.orchestrator import orchestrate_run, resume_run
from espresso_proof.theorem_condition_ledger import build_theorem_condition_ledger
from espresso_proof.validators import validate_run

# A reviewer's verbatim source requests + the conditions to check against the claim.
_SOURCE_REQUESTS = ["Cauchy theorem for finite groups", "Class equation argument"]
_MISSING_HYPOTHESES = ["G is finite", "p is prime"]


# ---------------------------------------------------------------------------
# run fixtures (mirror tests/test_subgoal_graph.py)
# ---------------------------------------------------------------------------
def _claim(tmp_path: Path) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(
        "Let p be a prime. Every group G of order p^2 is abelian.\n",
        encoding="utf-8",
    )
    return path


def _resumed_run(tmp_path: Path) -> Path:
    result = orchestrate_run(
        project="tcl_demo",
        input_path=_claim(tmp_path),
        output_root=tmp_path,
        provider="stub",
        front_half="stub",
    )
    run_dir = Path(result["run_dir"])
    brief = read_yaml(run_dir / "strategy_brief.yaml")
    selected = brief["top_strategies"][0]["id"]
    resume_run(run_dir=run_dir, selected_strategy=selected, effort="quick_test")
    return run_dir


def _inject_reviewer_requests(
    run_dir: Path, source_requests: list[str], missing_hypotheses: list[str]
) -> None:
    """Add verbatim source_requests + missing_hypotheses to the first verifier
    review of the gate-time llm_agent_run, mirroring the same values into the
    matching round_001 review so the run cross-checks still pass."""
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    optional = manifest["optional_artifacts"]

    agent_path = run_dir / optional["llm_agent_run"]
    agent = read_yaml(agent_path)
    verifier_id = agent["verifier_reviews"][0]["verifier_id"]
    agent["verifier_reviews"][0]["source_requests"] = source_requests
    agent["verifier_reviews"][0]["missing_hypotheses"] = missing_hypotheses
    write_yaml(agent_path, agent)

    index = read_yaml(run_dir / optional["prover_round_index"])
    round_one = next(r for r in index["rounds"] if r["round_index"] == 1)
    round_path = run_dir / round_one["artifact_path"]
    round_report = read_yaml(round_path)
    for review in round_report["verifier_reviews"]:
        if review["verifier_id"] == verifier_id:
            review["source_requests"] = source_requests
            review["missing_hypotheses"] = missing_hypotheses
    write_yaml(round_path, round_report)


# ---------------------------------------------------------------------------
# builder behaviour
# ---------------------------------------------------------------------------
def test_builder_creates_one_entry_per_source_request(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _inject_reviewer_requests(run_dir, _SOURCE_REQUESTS, _MISSING_HYPOTHESES)
    ledger = build_theorem_condition_ledger(run_dir)

    requests = [entry["requested_theorem"] for entry in ledger["entries"]]
    # every injected request appears verbatim, one entry each (no dedup collapse)
    for request in _SOURCE_REQUESTS:
        assert request in requests
    assert len(requests) == len(set(requests))  # one entry per distinct request

    injected = [e for e in ledger["entries"] if e["requested_theorem"] in _SOURCE_REQUESTS]
    assert len(injected) == len(_SOURCE_REQUESTS)
    for entry in injected:
        assert entry["locator_status"] == "needs_exact_locator"
        assert entry["locator"] is None
        assert entry["source_request_origin"] == "reviewer"
        assert entry["repair_subgoal_id"].startswith("subgoal_tcl_")
        assert entry["conditions"]
        for condition in entry["conditions"]:
            assert condition["match_status"] == "unknown"


def test_missing_hypotheses_become_verbatim_conditions(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _inject_reviewer_requests(run_dir, ["Some theorem"], _MISSING_HYPOTHESES)
    ledger = build_theorem_condition_ledger(run_dir)
    entry = next(e for e in ledger["entries"] if e["requested_theorem"] == "Some theorem")
    texts = [c["text"] for c in entry["conditions"]]
    for hypothesis in _MISSING_HYPOTHESES:
        assert hypothesis in texts


def test_entry_gets_placeholder_condition_when_no_missing_hypotheses(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    # one request, zero missing hypotheses anywhere → placeholder condition
    _inject_reviewer_requests(run_dir, ["Lonely theorem"], [])
    # also clear missing_hypotheses on any other reviews so none leak in
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    agent_path = run_dir / manifest["optional_artifacts"]["llm_agent_run"]
    agent = read_yaml(agent_path)
    agent["verifier_reviews"] = [
        r for r in agent["verifier_reviews"] if r["source_requests"] == ["Lonely theorem"]
    ] or agent["verifier_reviews"][:1]
    write_yaml(agent_path, agent)

    ledger = build_theorem_condition_ledger(run_dir)
    entry = next(e for e in ledger["entries"] if e["requested_theorem"] == "Lonely theorem")
    assert entry["conditions"] == [
        {
            "text": "Hypotheses to check against the claim",
            "claim_value": "",
            "match_status": "unknown",
        }
    ]


def test_builder_is_idempotent(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _inject_reviewer_requests(run_dir, _SOURCE_REQUESTS, _MISSING_HYPOTHESES)
    first = build_theorem_condition_ledger(run_dir)
    first_bytes = (run_dir / "theorem_condition_ledger.yaml").read_bytes()
    second = build_theorem_condition_ledger(run_dir)
    second_bytes = (run_dir / "theorem_condition_ledger.yaml").read_bytes()
    assert first == second
    assert first_bytes == second_bytes


def test_validate_run_passes_with_ledger(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _inject_reviewer_requests(run_dir, _SOURCE_REQUESTS, _MISSING_HYPOTHESES)
    build_theorem_condition_ledger(run_dir)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["theorem_condition_ledger"] == (
        "theorem_condition_ledger.yaml"
    )
    # the new optional artifact must not trip the kernel validator
    validate_run(run_dir)


def test_builder_writes_empty_ledger_without_requests(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    # clear every reviewer source_request so there is nothing to ledger
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    agent_path = run_dir / manifest["optional_artifacts"]["llm_agent_run"]
    agent = read_yaml(agent_path)
    verifier_id = agent["verifier_reviews"][0]["verifier_id"]
    for review in agent["verifier_reviews"]:
        review["source_requests"] = []
    write_yaml(agent_path, agent)
    index = read_yaml(run_dir / manifest["optional_artifacts"]["prover_round_index"])
    round_one = next(r for r in index["rounds"] if r["round_index"] == 1)
    round_path = run_dir / round_one["artifact_path"]
    round_report = read_yaml(round_path)
    for review in round_report["verifier_reviews"]:
        review["source_requests"] = []
    write_yaml(round_path, round_report)

    ledger = build_theorem_condition_ledger(run_dir)
    assert ledger["entries"] == []
    validate_run(run_dir)


# ---------------------------------------------------------------------------
# model safety (StrictBaseModel — no smuggled trusted status)
# ---------------------------------------------------------------------------
def test_ledger_condition_defaults_match_status_unknown() -> None:
    condition = LedgerCondition(text="G is finite")
    assert condition.match_status is MatchStatus.unknown
    assert condition.match_status == "unknown"


def test_entry_rejects_bogus_status_field() -> None:
    with pytest.raises(ValidationError):
        TheoremConditionLedgerEntry(
            id="tcl_1",
            requested_theorem="Cauchy theorem",
            conditions=[LedgerCondition(text="G is finite")],
            repair_subgoal_id="subgoal_tcl_1",
            source_backed=True,  # type: ignore[call-arg]  # forbidden extra field
        )


def test_ledger_rejects_bogus_status_field() -> None:
    with pytest.raises(ValidationError):
        TheoremConditionLedger(
            claim_id="claim_001",
            run_id="run_x",
            generated_at="2026-06-13T00:00:00+00:00",
            status="proved",  # type: ignore[call-arg]  # forbidden extra field
        )


def test_condition_rejects_bogus_status_field() -> None:
    with pytest.raises(ValidationError):
        LedgerCondition(
            text="G is finite",
            checked=True,  # type: ignore[call-arg]  # forbidden extra field
        )


def test_entry_requires_at_least_one_condition() -> None:
    with pytest.raises(ValidationError):
        TheoremConditionLedgerEntry(
            id="tcl_1",
            requested_theorem="Cauchy theorem",
            conditions=[],
            repair_subgoal_id="subgoal_tcl_1",
        )
