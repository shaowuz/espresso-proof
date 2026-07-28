"""Autonomous LLM-Guided Literature Retrieval V0.

Espresso can plan queries and find CANDIDATE sources autonomously (no manually
provided source), then hand a readable local candidate to the existing grounding
pipeline. Retrieval never earns reference support: only Source Grounding (exact
locator + hypothesis match + attribution) does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from espresso_proof.literature.autonomous_retrieval import run_autonomous_retrieval
from espresso_proof.claim.claim_clear import ensure_claim_clear_for_downstream
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.literature.literature_planner import plan_literature_search
from espresso_proof.agents.llm_agents import StubLLMClient
from espresso_proof.ui.memo import build_concise_memo
from espresso_proof.orchestration.genesis import run_mock_critique
from espresso_proof.kernel.models import AutonomousRetrievalCandidate, LiteratureSearchPlan
from espresso_proof.validation.errors import KernelValidationError
from espresso_proof.kernel.validators import validate_run

FIXTURES = Path("tests/fixtures")
_P2_THEOREM = (
    "Theorem 1.1. Let G be a group with |G| = p^2 where p is prime; then the "
    "center Z(G) is nontrivial and G is abelian.\n"
)


class _FakeClient:
    provider = "fake"
    model = "fake-model"

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def complete_json(self, *, system: str, prompt: str) -> dict:
        return dict(self._payload)


def _p2_corpus(tmp_path: Path, *, text: str = _P2_THEOREM, attribution: bool = True) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir(exist_ok=True)
    (corpus / "p2_groups.txt").write_text(text, encoding="utf-8")
    if attribution:
        write_yaml(
            corpus / "p2_groups.meta.yaml",
            {
                "title": "Groups of Order p^2 Are Abelian",
                "authors": ["A. Mathematician"],
                "locator": {"kind": "theorem", "value": "Theorem 1.1"},
            },
        )
    return corpus


def _p2_run(tmp_path: Path) -> Path:
    run_dir = run_mock_critique(
        "hodge_atoms", FIXTURES / "hodge_all_cubic_fourfolds" / "input.md", tmp_path
    )
    claim = read_yaml(run_dir / "locked_claim.yaml")
    claim["normalized_statement"] = "For every prime p and every group G with |G| = p^2, G is abelian."
    claim["objects"] = ["a prime p", "a group G of order p^2", "the center Z(G)"]
    claim["conclusion"] = "G is abelian"
    claim["hypotheses"] = [
        {"name": "p is prime", "status": "explicit", "source": "agent"},
        {"name": "group of order p^2", "status": "explicit", "source": "agent"},
        {"name": "G is a group", "status": "explicit", "source": "agent"},
    ]
    write_yaml(run_dir / "locked_claim.yaml", claim)
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["claim_precision"] = "precise"
    ledger["missing_or_unclear"] = []
    write_yaml(ledger_path, ledger)
    ensure_claim_clear_for_downstream(run_dir)
    sg = read_yaml(run_dir / "source_grounding.yaml")
    sg["source_refs"] = []
    sg["steps"] = [
        {
            "proof_step_id": "ps_center",
            "needed_theorem": "a group of order p^2 with p prime is abelian via its center",
            "status": "candidate",
            "source_refs": [],
            "hypothesis_match": {"matches": "unclear", "mismatches": []},
            "note": "Proposed proof-route step; needs a cited source.",
        }
    ]
    write_yaml(run_dir / "source_grounding.yaml", sg)
    return run_dir


# ---------------------------------------------------------------------------
# 1) Planner: normalized, candidate-only, deterministic fallback.
# ---------------------------------------------------------------------------


def test_planner_output_is_normalized_and_cannot_emit_source_backed(tmp_path: Path) -> None:
    run_dir = _p2_run(tmp_path)
    # A hostile planner tries to smuggle a status / source_backed field.
    client = _FakeClient(
        {
            "generated_queries": ["groups of order p^2 are abelian", "finite p-group nontrivial center"],
            "search_questions": ["Which theorem proves groups of order p^2 are abelian?"],
            "status": "source_backed",
            "source_backed": True,
            "hits": [{"status": "source_backed"}],
        }
    )
    plan = plan_literature_search(run_dir=run_dir, client=client)
    # validates against the model (which has NO status/source-backed field).
    LiteratureSearchPlan.model_validate(plan)
    # the smuggled status/hits fields never reach the artifact as keys.
    for smuggled in ("status", "source_backed", "hits"):
        assert smuggled not in plan
    assert "groups of order p^2 are abelian" in plan["generated_queries"]
    # risk_terms intentionally REMIND the reader retrieval is never source_backed.
    assert any("source_backed" in t for t in plan["risk_terms"])


def test_planner_has_deterministic_fallback_without_llm(tmp_path: Path) -> None:
    run_dir = _p2_run(tmp_path)
    plan = plan_literature_search(run_dir=run_dir, client=_FakeClient({}))
    assert plan["generated_queries"], plan
    assert plan["risk_terms"]  # forbidden-upgrade reminders always present
    validate_run(run_dir)


# ---------------------------------------------------------------------------
# 2) Retrieved candidates are candidate-only.
# ---------------------------------------------------------------------------


def test_candidate_model_rejects_non_candidate_status() -> None:
    with pytest.raises(ValueError):
        AutonomousRetrievalCandidate.model_validate(
            {"id": "c1", "provider": "arxiv", "query": "q", "title": "T", "status": "source_backed"}
        )


def test_retrieved_candidates_are_candidate_only(tmp_path: Path) -> None:
    run_dir = _p2_run(tmp_path)
    corpus = _p2_corpus(tmp_path)
    report = run_autonomous_retrieval(
        run_dir, client=StubLLMClient(), corpus_dir=corpus, providers=["local_corpus"], ground=False
    )
    assert report["candidates"], report
    assert all(c["status"] == "candidate" for c in report["candidates"])
    validate_run(run_dir)


# ---------------------------------------------------------------------------
# 3) Hard gates unchanged: abstract-only / missing attribution / missing
#    locator cannot become reference-supported.
# ---------------------------------------------------------------------------


def test_missing_attribution_cannot_become_reference_supported(tmp_path: Path) -> None:
    run_dir = _p2_run(tmp_path)
    corpus = _p2_corpus(tmp_path, attribution=False)  # no .meta.yaml -> no authors
    report = run_autonomous_retrieval(
        run_dir, client=StubLLMClient(), corpus_dir=corpus, providers=["local_corpus"], ground=True
    )
    assert report["earned_source_backed"] is False
    sg = read_yaml(run_dir / "source_grounding.yaml")
    assert all(s["status"] != "source_backed" for s in sg["steps"])


def test_stale_autonomous_earned_flag_requires_source_grounding_support(
    tmp_path: Path,
) -> None:
    run_dir = _p2_run(tmp_path)
    corpus = _p2_corpus(tmp_path, attribution=False)
    run_autonomous_retrieval(
        run_dir, client=StubLLMClient(), corpus_dir=corpus, providers=["local_corpus"], ground=True
    )
    report_path = run_dir / "autonomous_literature_retrieval.yaml"
    report = read_yaml(report_path)
    report["earned_source_backed"] = True
    write_yaml(report_path, report)

    with pytest.raises(KernelValidationError, match="earned_source_backed requires"):
        build_concise_memo(run_dir)
    with pytest.raises(KernelValidationError, match="earned_source_backed requires"):
        validate_run(run_dir)


def test_abstract_without_exact_locator_cannot_become_reference_supported(tmp_path: Path) -> None:
    run_dir = _p2_run(tmp_path)
    # Attributed, but the text is an abstract-style sentence with NO "Theorem X.Y"
    # label -> no exact locator extracted.
    corpus = _p2_corpus(
        tmp_path,
        text="Informally, for a group G with |G| = p^2 and p prime, G is abelian.\n",
    )
    run_autonomous_retrieval(
        run_dir, client=StubLLMClient(), corpus_dir=corpus, providers=["local_corpus"], ground=True
    )
    sg = read_yaml(run_dir / "source_grounding.yaml")
    assert all(s["status"] != "source_backed" for s in sg["steps"])


def test_no_readable_candidate_fails_closed(tmp_path: Path) -> None:
    run_dir = _p2_run(tmp_path)
    # No corpus at all -> nothing to ground; everything stays candidate.
    report = run_autonomous_retrieval(
        run_dir, client=StubLLMClient(), corpus_dir=None, providers=["local_corpus"], ground=True
    )
    assert report["grounded"] is False
    sg = read_yaml(run_dir / "source_grounding.yaml")
    assert all(s["status"] != "source_backed" for s in sg["steps"])


# ---------------------------------------------------------------------------
# 4) Retrieval result can be handed to grounding and earn support when the hard
#    gates pass; the p^2 demo runs without a manually provided source.
# ---------------------------------------------------------------------------


def test_retrieval_handed_to_grounding_earns_support_with_locator_and_attribution(tmp_path: Path) -> None:
    run_dir = _p2_run(tmp_path)
    corpus = _p2_corpus(tmp_path)  # attributed + exact "Theorem 1.1" locator in text
    report = run_autonomous_retrieval(
        run_dir, client=StubLLMClient(), corpus_dir=corpus, providers=["local_corpus"], ground=True
    )
    assert report["grounded"] is True
    assert report["earned_source_backed"] is True
    assert report["grounded_candidate_id"]
    assert report["grounded_retrieval_source_id"] == "ret_p2_groups"
    assert report["earned_source_ref_ids"]
    assert report["earned_proof_step_ids"] == ["ps_center"]
    sg = read_yaml(run_dir / "source_grounding.yaml")
    assert sg["steps"][0]["status"] == "source_backed", sg["steps"][0]
    backed = [r for r in sg["source_refs"] if r["status"] == "source_backed"]
    assert backed and backed[0]["locator"]["kind"] == "theorem"
    assert [a for a in backed[0]["authors"] if a.strip()]
    # memo distinguishes autonomous-search provenance and stays conservative.
    memo = build_concise_memo(run_dir)
    assert "Found by autonomous literature search" in memo
    assert "Reference supports this stated claim" in memo
    lower = memo.lower()
    for phrase in ("proof-close", "proof complete", "we proved", "proved the claim"):
        assert phrase not in lower


def test_retrieval_support_flag_is_not_satisfied_by_unrelated_existing_grounding(
    tmp_path: Path,
) -> None:
    run_dir = _p2_run(tmp_path)
    corpus = _p2_corpus(tmp_path)
    first = run_autonomous_retrieval(
        run_dir, client=StubLLMClient(), corpus_dir=corpus, providers=["local_corpus"], ground=True
    )
    assert first["earned_source_backed"] is True

    empty_corpus = tmp_path / "empty_corpus"
    empty_corpus.mkdir()
    second = run_autonomous_retrieval(
        run_dir,
        client=_FakeClient({"queries": ["unmatched source query"]}),
        corpus_dir=empty_corpus,
        providers=["local_corpus"],
        ground=True,
    )

    assert second["grounded"] is False
    assert second["earned_source_backed"] is False
    assert second["earned_source_ref_ids"] == []
    assert second["earned_proof_step_ids"] == []
    validate_run(run_dir)


def test_forged_retrieval_binding_to_wrong_source_is_rejected(tmp_path: Path) -> None:
    run_dir = _p2_run(tmp_path)
    corpus = _p2_corpus(tmp_path)
    run_autonomous_retrieval(
        run_dir, client=StubLLMClient(), corpus_dir=corpus, providers=["local_corpus"], ground=True
    )
    report_path = run_dir / "autonomous_literature_retrieval.yaml"
    report = read_yaml(report_path)
    report["grounded_retrieval_source_id"] = "ret_other_source"
    write_yaml(report_path, report)

    with pytest.raises(KernelValidationError, match="grounded source does not match"):
        validate_run(run_dir)


def test_p2_demo_runs_without_manual_source(tmp_path: Path) -> None:
    run_dir = _p2_run(tmp_path)
    corpus = _p2_corpus(tmp_path)
    report = run_autonomous_retrieval(
        run_dir, client=StubLLMClient(), corpus_dir=corpus, providers=["local_corpus"], ground=True
    )
    # the autonomous artifacts exist, are registered, and the run stays valid.
    assert (run_dir / "literature_search_plan.yaml").exists()
    assert (run_dir / "autonomous_literature_retrieval.yaml").exists()
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert manifest["optional_artifacts"]["literature_search_plan"] == "literature_search_plan.yaml"
    assert (
        manifest["optional_artifacts"]["autonomous_literature_retrieval"]
        == "autonomous_literature_retrieval.yaml"
    )
    assert report["queries"], "planner produced no queries"
    assert any(c["provider"] == "local_corpus" for c in report["candidates"])
    validate_run(run_dir)  # fail-closed full kernel gate

    # idea_grade is NOT upgraded by retrieval (no proof/grade change).
    grade = read_yaml(run_dir / "idea_grade.yaml")
    assert grade["proof_language_status"] == "idea_sketch"


def test_retrieval_with_empty_corpus_is_a_validated_no_op(tmp_path: Path) -> None:
    # Retrieval that finds nothing (empty/absent corpus) must be a
    # no-op that fails closed — no candidates, no grounding attempt, no status
    # change anywhere, and the run still validates.
    from espresso_proof.literature.autonomous_retrieval import run_autonomous_retrieval
    from espresso_proof.kernel.validators import validate_run

    run_dir = _p2_run(tmp_path)
    sg_before = read_yaml(run_dir / "source_grounding.yaml")
    empty_corpus = tmp_path / "empty_corpus"
    empty_corpus.mkdir()
    report = run_autonomous_retrieval(
        run_dir,
        client=_FakeClient({"queries": ["group of order p^2 abelian"]}),
        corpus_dir=empty_corpus,
        providers=["local_corpus"],
    )
    assert report["candidates"] == []
    assert report["grounded"] is False
    assert report["earned_source_backed"] is False
    validate_run(run_dir)
    sg_after = read_yaml(run_dir / "source_grounding.yaml")
    assert sg_after["steps"][0]["status"] == sg_before["steps"][0]["status"] == "candidate"
    assert all(ref.get("status") != "source_backed" for ref in sg_after.get("source_refs", []))
