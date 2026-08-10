from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.kernel.models import (
    LedgerCondition,
    MatchStatus,
    TheoremConditionLedger,
    TheoremConditionLedgerEntry,
)
from espresso_proof.orchestration.orchestrator import orchestrate_run, resume_run
from espresso_proof.verification.theorem_applicability import audit_theorem_applicability
from espresso_proof.verification.theorem_condition_ledger import (
    LEDGER_FILENAME,
    build_theorem_condition_ledger,
)
from espresso_proof.kernel.validators import validate_run

# A reviewer's verbatim source requests + the conditions to check against the claim.
_SOURCE_REQUESTS = ["Cauchy theorem for finite groups", "Class equation argument"]
_MISSING_HYPOTHESES = ["G is finite", "p is prime"]


# ---------------------------------------------------------------------------
# run fixtures (mirror tests/test_subgoal_graph.py)
# ---------------------------------------------------------------------------
def _claim(tmp_path: Path) -> Path:
    path = tmp_path / "claim.md"
    path.write_text(
        "Every group of order p^2, where p is prime, is abelian.\n",
        encoding="utf-8",
    )
    return path


def _resumed_run(tmp_path: Path) -> Path:
    claim_path = _claim(tmp_path)
    result = orchestrate_run(
        project="tcl_demo",
        input_path=claim_path,
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
    )
    run_dir = Path(result["run_dir"])
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)
    orchestrate_run(
        project="tcl_demo",
        input_path=claim_path,
        output_root=tmp_path,
        provider="stub",
        front_half="mock",
        run_dir=run_dir,
    )
    brief = read_yaml(run_dir / "strategy_brief.yaml")
    selected = brief["top_strategies"][0]["id"]
    effort = next(
        str(option)
        for choice in brief.get("allowed_human_choices") or []
        if choice.get("strategy_id") == selected
        for option in choice.get("effort_options") or []
    )
    resume_run(run_dir=run_dir, selected_strategy=selected, effort=effort)
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
    # The orchestrator always rebuilds the subgoal graph right after the ledger, so
    # The two stay consistent (cap-aware id check). Mirror that here: with
    # an empty ledger the rebuilt graph carries no subgoal_tcl_* nodes.
    from espresso_proof.kernel.subgoal_graph import build_subgoal_graph

    build_subgoal_graph(run_dir)
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


# ---------------------------------------------------------------------------
# theorem applicability audit is default-deny against the ledger
# (regression: an empty grounding-derived audit defaulted
# all_applicable, hiding an inapplicable theorem).
# ---------------------------------------------------------------------------
def _audit_with_ledger(run_dir: Path, *, condition_overrides=None, locator=None) -> dict:
    """Build the ledger, optionally tamper a single entry's condition match_status
    / locator, re-register it, and run the applicability audit. Returns the audit
    report dict."""
    build_theorem_condition_ledger(run_dir)
    if condition_overrides is not None or locator is not None:
        ledger = read_yaml(run_dir / LEDGER_FILENAME)
        assert ledger["entries"], "expected a non-empty ledger to tamper"
        entry = ledger["entries"][0]
        if condition_overrides is not None:
            for condition, status in zip(entry["conditions"], condition_overrides):
                condition["match_status"] = status
        if locator is not None:
            entry["locator_status"], entry["locator"] = locator
        write_yaml(run_dir / LEDGER_FILENAME, ledger)
    return audit_theorem_applicability(run_dir)


def test_unknown_ledger_conditions_are_nonempty_and_not_all_applicable(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _inject_reviewer_requests(run_dir, _SOURCE_REQUESTS, _MISSING_HYPOTHESES)
    report = _audit_with_ledger(run_dir)  # slice-1 default: every condition unknown

    assert report["ledger_usages"], "ledger present => audit must be NON-EMPTY"
    assert report["overall_status"] != "all_applicable"
    for usage in report["ledger_usages"]:
        assert usage["applicability_status"] != "applicable"
        assert usage["source_status"] != "source_backed"
        assert usage["blocks_source_backed"] is True
        assert all(c["status"] == "not_checked" for c in usage["conditions"])


def test_unknown_does_not_yield_all_applicable_or_source_backed(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _inject_reviewer_requests(run_dir, ["Some theorem"], _MISSING_HYPOTHESES)
    report = _audit_with_ledger(run_dir)
    assert report["overall_status"] != "all_applicable"
    # ledger usages never carry source_backed by default
    assert all(u["source_status"] != "source_backed" for u in report["ledger_usages"])


def test_matched_without_exact_locator_is_not_applicable(tmp_path: Path) -> None:
    """An agent-asserted ``matched`` condition WITHOUT an exact locator must not
    produce all_applicable or source_backed (the locator gate fails closed)."""
    run_dir = _resumed_run(tmp_path)
    _inject_reviewer_requests(run_dir, ["Cauchy theorem"], ["G is finite"])
    report = _audit_with_ledger(run_dir, condition_overrides=["matched"])  # locator still needs_exact

    assert report["overall_status"] != "all_applicable"
    usage = report["ledger_usages"][0]
    assert usage["applicability_status"] != "applicable"  # blocked: no exact locator
    assert usage["source_status"] != "source_backed"
    assert usage["blocks_source_backed"] is True


def test_unmatched_condition_yields_nonapplicable_and_overall_not_all(tmp_path: Path) -> None:
    run_dir = _resumed_run(tmp_path)
    _inject_reviewer_requests(run_dir, ["Cauchy theorem"], ["G is finite", "p is prime"])
    report = _audit_with_ledger(run_dir, condition_overrides=["unmatched"])

    assert report["overall_status"] != "all_applicable"
    usage = report["ledger_usages"][0]
    assert usage["applicability_status"] != "applicable"
    assert usage["source_status"] != "source_backed"
    assert any(c["status"] == "mismatched" for c in usage["conditions"])


def test_matched_and_exact_locator_never_earns_source_backed(tmp_path: Path) -> None:
    """Even the only 'ok' shape (all conditions matched + exact locator) earns NO
    source_backed from the ledger — that stays the grounding gate's job."""
    run_dir = _resumed_run(tmp_path)
    _inject_reviewer_requests(run_dir, ["Cauchy theorem"], ["G is finite"])
    report = _audit_with_ledger(
        run_dir,
        condition_overrides=["matched"],
        locator=("exact", "Cauchy 1845, Thm 1"),
    )
    usage = report["ledger_usages"][0]
    # An all-matched + exact-locator entry may read applicable (advisory only)...
    # ...but it must NEVER carry source_backed.
    assert usage["source_status"] != "source_backed"


def test_no_ledger_audit_unchanged(tmp_path: Path) -> None:
    """Regression: with NO theorem_condition_ledger present, the audit behaves
    exactly as before — no ledger usages, and overall is computed only from the
    grounding-derived usages."""
    run_dir = _resumed_run(tmp_path)
    # de-register and delete any auto-built ledger so the audit sees none
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    optional = manifest.get("optional_artifacts", {})
    optional.pop("theorem_condition_ledger", None)
    write_yaml(run_dir / "run_manifest.yaml", manifest)
    (run_dir / LEDGER_FILENAME).unlink(missing_ok=True)

    report = audit_theorem_applicability(run_dir)
    assert report["ledger_usages"] == []
    # overall depends only on grounding usages; must validate as a real report
    assert report["overall_status"] in {"all_applicable", "blocked", "misapplied", "unclear"}
