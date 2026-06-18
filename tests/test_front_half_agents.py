"""S0 tests: generic scaffolder + front-half live-agent plumbing.

These cover the harness only (no stage normalizers yet): an arbitrary claim
scaffolds into a kernel-valid run, the all-skipped front-half pass leaves a run
valid, and ``run-flow --front-half stub`` stays green.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from espresso_proof.claim.claim_clear import CLAIM_CLEAR_BLOCKED_FRONT_HALF_STAGES
from espresso_proof.cli import app
from espresso_proof.agents.front_half_agents import (
    FRONT_HALF_STAGES,
    registered_stages,
    run_front_half_agent_pass,
)
from espresso_proof.kernel.io import read_yaml, write_yaml
from espresso_proof.agents.llm_agents import StubLLMClient
from espresso_proof.orchestration.mock_pipeline import run_mock_critique
from espresso_proof.kernel.validators import validate_run

ARBITRARY_CLAIM = (
    "The Mathieu group M23 is realizable as a Galois group over Q.\n\n"
    "Please attempt to prove this."
)


def test_new_run_scaffolds_valid_run_for_arbitrary_claim(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "new-run",
            "--input",
            str(claim_path),
            "--project",
            "agent_runtime_demo",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    run_dir = Path(result.output.strip().split("Wrote run artifacts to ")[-1])
    assert run_dir.exists()
    # Scaffolder fell back to the generic "unclear" builder and is kernel-valid.
    validate_run(run_dir)


def test_front_half_pass_runs_registered_skips_rest_and_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)

    report = run_front_half_agent_pass(run_dir=run_dir, client=StubLLMClient())

    assert [s["stage"] for s in report["stages"]] == FRONT_HALF_STAGES
    by_stage = {s["stage"]: s["status"] for s in report["stages"]}
    registered = set(registered_stages())
    for stage, status in by_stage.items():
        if stage in CLAIM_CLEAR_BLOCKED_FRONT_HALF_STAGES:
            assert status == "skipped", report
        elif stage in registered:
            assert status == "ok", report
        else:
            assert status == "skipped", report
    validate_run(run_dir)


class _FakeClient:
    """A client returning a fixed payload, to probe normalizer safety."""

    provider = "fake"
    model = "fake-model"

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def complete_json(self, *, system: str, prompt: str) -> dict:
        return dict(self._payload)


def _mark_claim_clear(run_dir: Path) -> None:
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    ledger_path = run_dir / manifest["artifacts"]["hypothesis_ledger"]
    ledger = read_yaml(ledger_path)
    ledger["missing_or_unclear"] = []
    ledger["claim_precision"] = "precise"
    write_yaml(ledger_path, ledger)


def test_understanding_claim_strips_unsafe_model_fields(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    _mark_claim_clear(run_dir)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    original = read_yaml(run_dir / artifacts["locked_claim"])

    # A hostile model: tries to self-confirm, claim source-attributed hypotheses,
    # use an out-of-enum status, and overwrite id/raw_text.
    malicious = _FakeClient(
        {
            "id": "attacker_claim",
            "raw_text": "TOTALLY DIFFERENT TEXT",
            "normalized_statement": "M23 is realizable as a Galois group over Q.",
            "objects": ["the Mathieu group M23", "the field Q"],
            "hypotheses": [
                {"name": "G = M23", "status": "explicit", "source": "source"},
                {"name": "base field is Q", "status": "totally-bogus", "source": "user"},
            ],
            "conclusion": "There exists a Galois extension of Q with group M23.",
            "ambiguous_terms": ["realizable"],
            "missing_or_unclear": ["explicit polynomial or construction"],
            "human_confirmed": True,
            "locked": False,
            "proof_status": "proved",
        }
    )

    run_front_half_agent_pass(
        run_dir=run_dir, client=malicious, stages=["understanding_claim"]
    )
    validate_run(run_dir)

    claim = read_yaml(run_dir / artifacts["locked_claim"])
    # id + raw_text preserved (downstream refs stable; user text not overwritten).
    assert claim["id"] == original["id"]
    assert claim["raw_text"] == original["raw_text"]
    # agent can never self-confirm; claim stays locked.
    assert claim["human_confirmed"] is False
    assert claim["locked"] is True
    # every hypothesis is agent-attributed; out-of-enum status coerced to unclear.
    assert claim["hypotheses"], claim
    for hyp in claim["hypotheses"]:
        assert hyp["source"] == "agent"
        assert hyp["status"] in {"explicit", "inferred", "missing", "unclear"}
    statuses = [h["status"] for h in claim["hypotheses"]]
    assert "unclear" in statuses  # the "totally-bogus" status landed here
    # no stray model fields leaked into the artifact.
    assert "proof_status" not in claim
    # the refined statement is surfaced and ambiguity recorded.
    assert "M23" in claim["normalized_statement"]
    assert claim["ambiguous_terms"]


def test_literature_review_downgrades_source_backed_to_candidate(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    _mark_claim_clear(run_dir)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    # A model trying to certify references as source_backed / misapplied.
    malicious = _FakeClient(
        {
            "query_summary": "M23 inverse Galois candidates.",
            "candidate_references": [
                {
                    "title": "Topics in Galois Theory (Serre)",
                    "authors": ["J.-P. Serre"],
                    "locator": {"kind": "theorem", "value": "Thm 3.2.1"},
                    "relevance": "core",
                    "status": "source_backed",  # must be downgraded
                    "why_relevant": "rigidity method overview",
                },
                {
                    "title": "Inverse Galois Theory (Malle-Matzat)",
                    "authors": ["Malle", "Matzat"],
                    "locator": {"kind": "unknown", "value": ""},
                    "relevance": "background",
                    "status": "misapplied",  # must be downgraded
                    "why_relevant": "general theory",
                },
            ],
            "known_examples": ["M11, M12 realized over Q"],
            "known_counterexample_families": [],
            "notes": [],
        }
    )

    run_front_half_agent_pass(
        run_dir=run_dir, client=malicious, stages=["literature_review"]
    )
    validate_run(run_dir)

    lit = read_yaml(run_dir / artifacts["literature_review"])
    assert lit["claim_id"] == read_yaml(run_dir / artifacts["locked_claim"])["id"]
    assert len(lit["candidate_references"]) == 2
    for ref in lit["candidate_references"]:
        # never source_backed/misapplied; agent-attributed.
        assert ref["status"] in {"candidate", "unverified", "not_found"}
        assert ref["found_by"] == "literature_review"
    assert lit["known_examples"] == ["M11, M12 realized over Q"]


def test_literature_review_with_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    _mark_claim_clear(run_dir)

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["literature_review"]
    )
    validate_run(run_dir)


def test_examples_cannot_self_certify_as_evidence(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    _mark_claim_clear(run_dir)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    # A model trying to declare an example "survived" and a computation "checked".
    malicious = _FakeClient(
        {
            "examples": [
                {
                    "name": "Quotient construction giving M23",
                    "satisfies_all_claim_hypotheses": True,
                    "hypothesis_mismatches": [],
                    "what_it_tests": ["realizability over Q"],
                    "expected_conclusion": "M23 occurs as a Galois group",
                    "observed_or_inferred_behavior": "construction succeeds",
                    "nontriviality": "research_level",
                    "source_or_reason": "agent reasoning",
                    "result_for_current_idea": "survived",  # must be neutralized
                }
            ],
            "computation_checks": [
                {
                    "example_name": "Quotient construction giving M23",
                    "computed_quantity": "Galois group order",
                    "input_data": ["polynomial f"],
                    "result": "|M23| = 10200960",
                    "method": "exact_arithmetic",  # must be neutralized
                    "status": "checked",  # must be neutralized
                    "notes": "trust me",
                }
            ],
        }
    )

    run_front_half_agent_pass(run_dir=run_dir, client=malicious, stages=["examples"])
    validate_run(run_dir)

    examples = read_yaml(run_dir / artifacts["examples"])
    comps = read_yaml(run_dir / artifacts["computation_checks"])
    assert examples["examples"], examples
    for ex in examples["examples"]:
        # an agent-proposed example is never auto-counted as survived/failed evidence.
        assert ex["result_for_current_idea"] == "not_tested"
    assert comps["checks"], comps
    for ch in comps["checks"]:
        # agent computations are proposals, never self-certified checked evidence.
        assert ch["status"] == "human_needed"
        assert ch["method"] == "manual_review_needed"
        assert ch["example_id"] in {ex["id"] for ex in examples["examples"]}


def test_examples_with_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    _mark_claim_clear(run_dir)

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["examples"]
    )
    validate_run(run_dir)


def test_counterexamples_dropped_hypothesis_and_proof_step_cannot_refute(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    _mark_claim_clear(run_dir)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    # A model trying to refute the claim via a dropped hypothesis AND via a
    # proof-step attack.
    malicious = _FakeClient(
        {
            "counterexamples": [
                {
                    "name": "Drops the over-Q hypothesis (works over C)",
                    "satisfies_all_claim_hypotheses": False,  # dropped hypothesis
                    "target": "hypothesis",
                    "missing_hypothesis": "base field is Q",
                    "support": "M23 is realizable over C-bar trivially",
                    "explanation": "this refutes it",
                    "refutes_claim": True,  # must be neutralized
                },
                {
                    "name": "Attacks the rigidity step",
                    "satisfies_all_claim_hypotheses": True,
                    "target": "proof_step",
                    "explanation": "the rigidity computation fails",
                    "refutes_claim": True,  # must be neutralized
                    "refutes_step_only": True,
                },
            ],
            "tests": [
                {
                    "description": "Run both counterexamples",
                    "uses_counterexamples": ["cex_001", "cex_002", "bogus_id"],
                    "main_observation": "claim is false",
                    "next_action": "declare refuted",
                    "result": "failed",  # must be neutralized
                    "evidence_status": "checked",  # must be neutralized
                }
            ],
            "summary": "Refuted.",
        }
    )

    run_front_half_agent_pass(
        run_dir=run_dir, client=malicious, stages=["counterexamples"]
    )
    validate_run(run_dir)

    cex = read_yaml(run_dir / artifacts["counterexamples"])
    tests = read_yaml(run_dir / artifacts["tests"])
    assert len(cex["tests"]) == 2
    for t in cex["tests"]:
        # no agent counterexample can auto-refute the claim.
        assert t["refutes_claim"] is False
        assert t["result"] == "not_tested"
    dropped = cex["tests"][0]
    assert dropped["satisfies_all_claim_hypotheses"] is False
    assert dropped["exposes_missing_hypothesis"] is True
    assert dropped["missing_hypothesis"]
    step = cex["tests"][1]
    assert step["target"] == "proof_step"
    assert step["refutes_step_only"] is True
    # admissibility tests stay unverified and only reference known counterexamples.
    for ti in tests["tests"]:
        assert ti["result"] == "not_tested"
        assert ti["evidence_status"] == "human_needed"
        assert "bogus_id" not in ti["uses_counterexamples"]


def test_counterexamples_with_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    _mark_claim_clear(run_dir)

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["counterexamples"]
    )
    validate_run(run_dir)


def test_strategy_unclear_claim_forced_to_lock_route(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")  # -> idea_grade unclear
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    # A model pushing high-confidence deep routes for a still-unclear claim.
    malicious = _FakeClient(
        {
            "routes": [
                {
                    "title": "Just prove it directly",
                    "why_this_route": "looks doable",
                    "main_risk": "none",
                    "estimated_effort": "deep_exploration",
                    "next_loop_plan": "finish the proof",
                },
                {
                    "title": "Another confident route",
                    "why_this_route": "also doable",
                    "main_risk": "none",
                    "estimated_effort": "long_watch",
                    "next_loop_plan": "grind",
                },
            ],
            "question_for_mathematician": "Which one?",
            "synthesis_note": "These will work.",
        }
    )

    run_front_half_agent_pass(run_dir=run_dir, client=malicious, stages=["strategy"])
    validate_run(run_dir)

    brief = read_yaml(run_dir / artifacts["strategy_brief"])
    synthesis = read_yaml(run_dir / artifacts["strategy_synthesis"])
    # unclear claim => only the canonical claim-lock route is allowed.
    assert [s["id"] for s in brief["top_strategies"]] == ["strategy_lock_claim"]
    route = brief["top_strategies"][0]
    assert route["score"] < 75
    assert route["estimated_effort"] != "deep_exploration"
    # brief routes must match synthesis routes (kernel identity rule).
    assert synthesis["routes"] == brief["top_strategies"]
    # human-choice options include 'none'.
    assert any(c["strategy_id"] == "none" for c in brief["allowed_human_choices"])


def test_strategy_with_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    _mark_claim_clear(run_dir)

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["strategy"]
    )
    validate_run(run_dir)


def test_attacker_cannot_upgrade_evidence_or_refute(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    _mark_claim_clear(run_dir)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    # First seed counterexamples + admissibility tests (S4).
    seed = _FakeClient(
        {
            "counterexamples": [
                {
                    "name": "Pressure cex A",
                    "satisfies_all_claim_hypotheses": "unclear",
                    "target": "theorem",
                    "explanation": "candidate pressure",
                }
            ],
            "tests": [
                {
                    "description": "seed admissibility test",
                    "uses_counterexamples": ["cex_001"],
                    "main_observation": "seed",
                    "next_action": "run",
                }
            ],
            "summary": "seed",
        }
    )
    run_front_half_agent_pass(run_dir=run_dir, client=seed, stages=["counterexamples"])
    tests_before = read_yaml(run_dir / artifacts["tests"])
    seed_ids = {t["id"] for t in tests_before["tests"]}

    # Now the attacker tries to declare a refutation with checked/failed evidence.
    attacker = _FakeClient(
        {
            "attacks": [
                {
                    "target_id": "cex_001",
                    "attacks": "proof_step",
                    "survives": True,
                    "observation": "this breaks the proof so the claim is refuted",
                    "next_action": "declare refuted",
                    "result": "failed",  # must be neutralized
                    "evidence_status": "checked",  # must be neutralized
                }
            ],
            "route_blockers": ["rigidity route blocked"],
            "summary": "Refuted via proof-step attack.",
        }
    )
    run_front_half_agent_pass(run_dir=run_dir, client=attacker, stages=["attacker"])
    validate_run(run_dir)

    tests_after = read_yaml(run_dir / artifacts["tests"])
    after_ids = {t["id"] for t in tests_after["tests"]}
    # existing (seed) test ids preserved.
    assert seed_ids.issubset(after_ids)
    # the attacker's appended observation stays unverified.
    appended = [t for t in tests_after["tests"] if t["id"] not in seed_ids]
    assert appended
    for t in appended:
        assert t["result"] == "not_tested"
        assert t["evidence_status"] == "human_needed"


def test_attacker_with_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    _mark_claim_clear(run_dir)

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["attacker"]
    )
    validate_run(run_dir)


def test_source_theorem_cannot_self_certify_source_backed(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    _mark_claim_clear(run_dir)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    # A model trying to certify a source as source_backed with an exact locator
    # and a verified hypothesis match.
    malicious = _FakeClient(
        {
            "source_refs": [
                {
                    "type": "book",
                    "title": "Serre, Topics in Galois Theory",
                    "authors": ["J.-P. Serre"],
                    "locator": {"kind": "theorem", "value": "Thm 3.2.1"},
                    "statement_or_summary": "rigidity gives realizability",
                    "hypothesis_match_note": "matches exactly",
                    "status": "source_backed",  # must be downgraded
                }
            ],
            "steps": [
                {
                    "proof_step_id": "step_rigidity",
                    "needed_theorem": "rigidity criterion",
                    "uses_source_index": 1,
                    "status": "source_backed",  # must be downgraded
                    "hypothesis_match_note": "all hypotheses satisfied",
                    "note": "done",
                }
            ],
        }
    )

    run_front_half_agent_pass(
        run_dir=run_dir, client=malicious, stages=["source_theorem"]
    )
    validate_run(run_dir)

    sg = read_yaml(run_dir / artifacts["source_grounding"])
    assert sg["source_refs"], sg
    for ref in sg["source_refs"]:
        assert ref["status"] != "source_backed"
        assert ref["hypothesis_match"]["matches"] != True  # noqa: E712
    for step in sg["steps"]:
        assert step["status"] != "source_backed"
    # no source_backed steps => no theorem_applicability artifact required.
    manifest = read_yaml(run_dir / "run_manifest.yaml")
    assert "theorem_applicability" not in manifest.get("optional_artifacts", {})


def test_source_theorem_with_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    _mark_claim_clear(run_dir)

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["source_theorem"]
    )
    validate_run(run_dir)


def test_grade_cannot_upgrade_status_or_proof_language(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    _mark_claim_clear(run_dir)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    before = read_yaml(run_dir / artifacts["idea_grade"])

    # A model trying to upgrade the grade to proved/promising/high score.
    malicious = _FakeClient(
        {
            "reasons": ["this is essentially proved"],
            "missing_hypotheses": [],
            "source_gaps": ["need exact rigidity locator"],
            "repair_options": ["formalize"],
            "next_cheapest_test": "finish it",
            "status": "promising",  # must be ignored
            "score": 99,  # must be ignored
            "proof_language_status": "lean_checked",  # must be ignored
            "survived_tests": ["t1"],  # must be ignored
            "failed_tests": ["t2"],  # must be ignored
        }
    )

    run_front_half_agent_pass(
        run_dir=run_dir, client=malicious, stages=["grade_memory_memo"]
    )
    validate_run(run_dir)

    after = read_yaml(run_dir / artifacts["idea_grade"])
    # kernel-controlled fields preserved (no upgrade from agent prose).
    assert after["status"] == before["status"]
    assert after["score"] == before["score"]
    assert after["proof_language_status"] == before["proof_language_status"]
    # evidence lists forced empty (no checked evidence in the front half).
    assert after["survived_tests"] == []
    assert after["failed_tests"] == []
    assert after["human_review_required"] is True
    # qualitative reasoning refreshed from the model.
    assert "need exact rigidity locator" in after["source_gaps"]


def test_full_front_half_pass_with_stub_is_valid(tmp_path: Path) -> None:
    # All registered stages in order on an arbitrary claim, end to end. The stub
    # claim-understanding pass makes this fixture precise enough to continue,
    # and downstream stages still cannot upgrade mathematical status.
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)

    report = run_front_half_agent_pass(run_dir=run_dir, client=StubLLMClient())
    validate_run(run_dir)

    statuses = {s["stage"]: s["status"] for s in report["stages"]}
    for stage in registered_stages():
        if stage in CLAIM_CLEAR_BLOCKED_FRONT_HALF_STAGES:
            assert statuses[stage] == "skipped", report
        else:
            assert statuses[stage] == "ok", report
    # memo renders honestly (no proof-complete language) on the live skeleton.
    from espresso_proof.ui.memo import build_concise_memo

    memo = build_concise_memo(run_dir)
    lowered = memo.lower()
    for banned in ("proof-close", "proof complete", "we proved", "this establishes the theorem"):
        assert banned not in lowered


def test_understanding_claim_with_stub_keeps_vague_claim_honest(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text("Prove the thing about the nice mirror object.", encoding="utf-8")
    run_dir = run_mock_critique("agent_runtime_demo", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    run_front_half_agent_pass(
        run_dir=run_dir, client=StubLLMClient(), stages=["understanding_claim"]
    )
    validate_run(run_dir)

    claim = read_yaml(run_dir / artifacts["locked_claim"])
    assert claim["human_confirmed"] is False
    assert claim["locked"] is True


def test_run_flow_front_half_stub_stays_valid(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "run-flow",
            "--input",
            str(claim_path),
            "--project",
            "agent_runtime_demo",
            "--provider",
            "stub",
            "--front-half",
            "stub",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Front-half agents: mode=stub" in result.output
    run_dir = Path(result.output.splitlines()[0].split("Wrote run artifacts to ")[-1])
    validate_run(run_dir)


def test_run_flow_front_half_rejects_bad_mode(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "run-flow",
            "--input",
            str(claim_path),
            "--front-half",
            "bogus",
            "--output-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code != 0


# Claim text that triggers the seeded hodge_atoms mock scenario, whose scaffold
# writes a tests.yaml referencing the seeded example id ``ex_special_cubic``.
HODGE_ALL_CUBIC_CLAIM = (
    "For all cubic fourfolds, the Hodge atom obstruction proves irrationality.\n\n"
    "The sketch treats the very general case as if it applied to every smooth "
    "cubic fourfold."
)


def test_examples_stage_reconciles_seeded_tests_when_example_ids_change(
    tmp_path: Path,
) -> None:
    """Regression (Golden Dogfood Run): the live examples stage replaces
    examples.yaml with new ids, which must not orphan a seeded tests.yaml that
    referenced the scaffold example id (here ex_special_cubic). The examples
    stage prunes uses_examples to the ids it actually wrote, so the post-stage
    validate_run passes. Pruning only DROPS an unsupported reference; it never
    invents an example id and never upgrades any status.
    """
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(HODGE_ALL_CUBIC_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("hodge_atoms", claim_path, tmp_path)
    _mark_claim_clear(run_dir)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    # Precondition: the seeded scaffold references ex_special_cubic from tests.yaml.
    seeded_tests = read_yaml(run_dir / artifacts["tests"])
    seeded_refs = {eid for t in seeded_tests["tests"] for eid in t.get("uses_examples", [])}
    assert "ex_special_cubic" in seeded_refs

    # The live examples agent returns examples with DIFFERENT ids (ex_001...).
    client = _FakeClient(
        {
            "examples": [
                {
                    "name": "Pfaffian cubic fourfold (general member)",
                    "satisfies_all_claim_hypotheses": True,
                    "what_it_tests": ["rationality pressure"],
                    "expected_conclusion": "rational",
                    "observed_or_inferred_behavior": "known rational family",
                    "nontriviality": "research_level",
                    "source_or_reason": "agent reasoning",
                }
            ],
        }
    )
    run_front_half_agent_pass(run_dir=run_dir, client=client, stages=["examples"])

    # The kernel accepts the run: no dangling example reference remains.
    validate_run(run_dir)

    examples = read_yaml(run_dir / artifacts["examples"])
    example_ids = {ex["id"] for ex in examples["examples"]}
    assert "ex_special_cubic" not in example_ids

    tests = read_yaml(run_dir / artifacts["tests"])
    for test in tests["tests"]:
        # Every retained reference points at an example we actually wrote; the
        # orphan ex_special_cubic was dropped, not re-invented.
        assert set(test.get("uses_examples", [])).issubset(example_ids)
        assert "ex_special_cubic" not in test.get("uses_examples", [])


def test_full_front_half_pass_on_seeded_hodge_scenario_stays_valid(tmp_path: Path) -> None:
    """Regression (Golden Dogfood Run): a full front-half pass over the seeded
    hodge_atoms mock scaffold stays kernel-valid at every stage. The scaffold
    seeds scenario-specific tests.yaml and strategy artifacts; as live stages
    replace their dependencies (examples.yaml, idea_grade.yaml) the run must
    remain valid between stages (examples reconciles tests; regrade reconciles
    strategy), not just at the end.
    """
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(HODGE_ALL_CUBIC_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("hodge_atoms", claim_path, tmp_path)

    report = run_front_half_agent_pass(run_dir=run_dir, client=StubLLMClient())

    # No stage errored; the run is valid after the whole pass.
    for stage in report["stages"]:
        assert stage["status"] in {"ok", "skipped"}, stage
    validate_run(run_dir)


# ---------------------------------------------------------------------------
# Goal 19: stale "Too vague to test" placeholder objection is scrubbed for a
# precise claim (display hygiene; never upgrades any status).
# ---------------------------------------------------------------------------
from espresso_proof.ui.memo import build_concise_memo  # noqa: E402

_PENDING_NOTE = "Front-half review pending; real reviewers run in the Prover/Reviewers pass."


def _seed_failed_vague_review(run_dir: Path, *, precise: bool) -> dict:
    """Set claim precision and seed a placeholder FAILED review carrying stale
    'too vague' text plus one genuine (non-vagueness) downgrade reason."""
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    ledger = read_yaml(run_dir / artifacts["hypothesis_ledger"])
    ledger["claim_precision"] = "precise" if precise else "unclear"
    write_yaml(run_dir / artifacts["hypothesis_ledger"], ledger)
    review = read_yaml(run_dir / artifacts["review"])
    review["reviewers"][0]["verdict"] = "fail"
    review["strongest_objection"] = "The claim is not precise enough to test."
    review["missing_hypotheses"] = ["objects", "hypotheses", "conclusion"]
    review["downgrade_reasons"] = ["Too vague to test.", "No source-backed theorem yet."]
    write_yaml(run_dir / artifacts["review"], review)
    return artifacts


def _precise_run_with_failed_vague_review(tmp_path: Path) -> tuple[Path, dict]:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("g19", claim_path, tmp_path)
    artifacts = _seed_failed_vague_review(run_dir, precise=True)
    run_front_half_agent_pass(run_dir=run_dir, client=StubLLMClient(), stages=["regrade"])
    validate_run(run_dir)
    return run_dir, artifacts


def test_precise_claim_scrubs_stale_vague_objection(tmp_path: Path) -> None:
    run_dir, artifacts = _precise_run_with_failed_vague_review(tmp_path)
    review = read_yaml(run_dir / artifacts["review"])
    assert review["verdict"] == "unclear"
    assert all(r["verdict"] == "unclear" for r in review["reviewers"])  # not "pass"
    assert "vague" not in (review.get("strongest_objection") or "").lower()
    assert review["strongest_objection"] == _PENDING_NOTE
    assert not any("vague" in r.lower() or "not precise enough" in r.lower()
                   for r in review["downgrade_reasons"])
    # the genuine non-vagueness reason is preserved
    assert "No source-backed theorem yet." in review["downgrade_reasons"]
    # neutral pending-review note appears exactly once
    assert review["downgrade_reasons"].count(_PENDING_NOTE) == 1
    # severity conservative, no reruns demanded
    assert all(r["severity"] == "medium" and r["required_reruns"] == []
               for r in review["reviewers"])
    # display hygiene only: the grade is not upgraded to a proof
    grade = read_yaml(run_dir / artifacts["idea_grade"])
    assert grade["proof_language_status"] == "idea_sketch"


def test_precise_claim_reconciles_stale_scaffold_control_artifacts(tmp_path: Path) -> None:
    run_dir, artifacts = _precise_run_with_failed_vague_review(tmp_path)

    tests = read_yaml(run_dir / artifacts["tests"])
    assert tests["target"] == "claim"
    assert tests["tests"] == []
    assert "vague" not in tests["summary"].lower()
    assert "clear claim" in tests["summary"]

    loop = read_yaml(run_dir / artifacts["loop_control"])
    assert loop["should_stop"] is False
    assert loop["stop_reasons"] == []
    assert loop["next_action"] == "run_strategy_brief"

    review = read_yaml(run_dir / artifacts["review"])
    assert review["missing_hypotheses"] == []

    report = (run_dir / artifacts["final_report"]).read_text(encoding="utf-8").lower()
    assert "claim is unclear" not in report
    assert "needs restatement" not in report
    assert "front-half accepted the claim as clear" in report


def test_imprecise_claim_still_uses_claim_lock_and_keeps_vague_language(tmp_path: Path) -> None:
    claim_path = tmp_path / "claim.md"
    claim_path.write_text(ARBITRARY_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("g19", claim_path, tmp_path)
    artifacts = _seed_failed_vague_review(run_dir, precise=False)
    run_front_half_agent_pass(run_dir=run_dir, client=StubLLMClient(), stages=["regrade"])
    validate_run(run_dir)
    # imprecise claim is held at claim-locking, and "too vague" language is allowed
    grade = read_yaml(run_dir / artifacts["idea_grade"])
    assert grade["status"] == "unclear"
    review = read_yaml(run_dir / artifacts["review"])
    vague_anywhere = "vague" in (review.get("strongest_objection") or "").lower() or any(
        "vague" in r.lower() for r in review.get("downgrade_reasons", [])
    )
    assert vague_anywhere, "imprecise claim should keep its claim-vagueness language"
    # strategy stays the safe lock-claim route for an imprecise claim
    run_front_half_agent_pass(run_dir=run_dir, client=StubLLMClient(), stages=["strategy"])
    brief = read_yaml(run_dir / artifacts["strategy_brief"])
    assert any("lock" in (s.get("id", "") + s.get("title", "")).lower()
               for s in brief.get("top_strategies", []))


def test_neutralize_vague_objection_is_idempotent(tmp_path: Path) -> None:
    run_dir, artifacts = _precise_run_with_failed_vague_review(tmp_path)
    first = read_yaml(run_dir / artifacts["review"])
    # running the regrade again must not duplicate the neutral note or re-add vagueness
    run_front_half_agent_pass(run_dir=run_dir, client=StubLLMClient(), stages=["regrade"])
    validate_run(run_dir)
    second = read_yaml(run_dir / artifacts["review"])
    assert second["downgrade_reasons"].count(_PENDING_NOTE) == 1
    assert second["downgrade_reasons"] == first["downgrade_reasons"]
    assert second["strongest_objection"] == first["strongest_objection"]


def test_precise_claim_memo_has_no_stale_vague_language(tmp_path: Path) -> None:
    run_dir, _ = _precise_run_with_failed_vague_review(tmp_path)
    memo = build_concise_memo(run_dir).lower()
    assert "vague" not in memo
    assert "not precise enough to test" not in memo
    for banned in ("proof-close", "proof complete", "we proved", "this establishes the theorem"):
        assert banned not in memo


def _finite_confirmed_machine_check() -> dict:
    return {
        "kind": "finite_enumeration",
        "expression": "@n[1:10] sum(1,n)",
        "expected": "@n[1:10] n*(n+1)/2",
        "notes": "",
    }


def _confirmed_verification_result(check_id: str = "c1") -> dict:
    return {
        "schema_version": 1,
        "claim_id": "claim_001",
        "generated_at": "2026-06-14T00:00:00+00:00",
        "backend": "espresso-stdlib-verifier",
        "results": [
            {
                "check_id": check_id,
                "claim_id": "claim_001",
                "kind": "finite_enumeration",
                "expression": "@n[1:10] sum(1,n)",
                "backend": "stdlib_finite_enumeration",
                "executed": True,
                "verdict": "confirmed",
                "computed": "holds for all n in [1,10]",
                "expected": "@n[1:10] n*(n+1)/2",
                "detail": "executed finite enumeration",
            }
        ],
    }


def _regrade_status(
    tmp_path: Path,
    *,
    counterexamples: list[dict] | None = None,
    check: dict | None = None,
    verification: dict | None = None,
) -> dict:
    from espresso_proof.front_half.stages.grade import _regrade
    from espresso_proof.agents.llm_agents import load_agent_context

    claim_path = tmp_path / "regrade_claim.md"
    claim_path.write_text(
        "For every integer n>=1, 1+2+...+n = n(n+1)/2.\n",
        encoding="utf-8",
    )
    run_dir = run_mock_critique("regrade_test", claim_path, tmp_path)
    ctx = load_agent_context(run_dir)
    ctx["hypothesis_ledger"]["claim_precision"] = "precise"
    if counterexamples is not None:
        ctx["counterexamples"] = {"tests": counterexamples}
    if check is not None:
        ctx["computation_checks"] = {"checks": [check]}
    if verification is not None:
        ctx["computation_verification"] = verification
    _regrade(run_dir=run_dir, client=StubLLMClient(), context=ctx)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    return read_yaml(run_dir / artifacts["idea_grade"])


def test_regrade_missing_support_alone_is_fragile_not_likely_false(tmp_path: Path) -> None:
    grade = _regrade_status(tmp_path, counterexamples=[])
    assert grade["status"] == "fragile"


def test_regrade_confirmed_machine_check_vetoes_likely_false(tmp_path: Path) -> None:
    counterexamples = [
        {"id": "cx1", "satisfies_all_claim_hypotheses": False, "refutes_claim": False}
    ]
    check = {
        "id": "c1",
        "claim_id": "claim_001",
        "status": "checked",
        "machine_check": _finite_confirmed_machine_check(),
    }
    grade = _regrade_status(
        tmp_path,
        counterexamples=counterexamples,
        check=check,
        verification=_confirmed_verification_result(),
    )
    assert grade["status"] == "fragile"
    assert grade["status"] not in {"promising", "proved", "refuted"}
    assert grade["proof_language_status"] == "idea_sketch"


def test_regrade_unchecked_negative_pressure_still_flags_likely_false(tmp_path: Path) -> None:
    counterexamples = [
        {"id": "cx1", "satisfies_all_claim_hypotheses": False, "refutes_claim": False}
    ]
    grade = _regrade_status(tmp_path, counterexamples=counterexamples)
    assert grade["status"] == "likely_false"


def test_regrade_checked_machine_check_without_verification_does_not_veto(
    tmp_path: Path,
) -> None:
    counterexamples = [
        {"id": "cx1", "satisfies_all_claim_hypotheses": False, "refutes_claim": False}
    ]
    check = {
        "id": "c1",
        "claim_id": "claim_001",
        "status": "checked",
        "machine_check": _finite_confirmed_machine_check(),
    }
    grade = _regrade_status(tmp_path, counterexamples=counterexamples, check=check)
    assert grade["status"] == "likely_false"


def test_regrade_stale_verification_for_other_claim_does_not_veto(
    tmp_path: Path,
) -> None:
    counterexamples = [
        {"id": "cx1", "satisfies_all_claim_hypotheses": False, "refutes_claim": False}
    ]
    check = {
        "id": "c1",
        "claim_id": "claim_001",
        "status": "checked",
        "machine_check": _finite_confirmed_machine_check(),
    }
    verification = _confirmed_verification_result()
    verification["claim_id"] = "other_claim"
    verification["results"][0]["claim_id"] = "other_claim"
    grade = _regrade_status(
        tmp_path, counterexamples=counterexamples, check=check, verification=verification
    )
    assert grade["status"] == "likely_false"


def test_regrade_stale_verification_for_other_expression_does_not_veto(
    tmp_path: Path,
) -> None:
    counterexamples = [
        {"id": "cx1", "satisfies_all_claim_hypotheses": False, "refutes_claim": False}
    ]
    check = {
        "id": "c1",
        "claim_id": "claim_001",
        "status": "checked",
        "machine_check": _finite_confirmed_machine_check(),
    }
    verification = _confirmed_verification_result()
    verification["results"][0]["expression"] = "@n[1:10] n"
    grade = _regrade_status(
        tmp_path, counterexamples=counterexamples, check=check, verification=verification
    )
    assert grade["status"] == "likely_false"


def test_regrade_admissible_refutation_still_refuted(tmp_path: Path) -> None:
    counterexamples = [
        {"id": "cx1", "satisfies_all_claim_hypotheses": True, "refutes_claim": True}
    ]
    grade = _regrade_status(tmp_path, counterexamples=counterexamples)
    assert grade["status"] == "refuted"


def test_regrade_unchecked_computation_check_does_not_veto_likely_false(
    tmp_path: Path,
) -> None:
    counterexamples = [
        {"id": "cx1", "satisfies_all_claim_hypotheses": False, "refutes_claim": False}
    ]
    check = {
        "id": "c1",
        "claim_id": "claim_001",
        "status": "human_needed",
        "machine_check": _finite_confirmed_machine_check(),
    }
    grade = _regrade_status(tmp_path, counterexamples=counterexamples, check=check)
    assert grade["status"] == "likely_false"


def test_reconcile_tests_drops_dangling_example_reference(tmp_path: Path) -> None:
    from espresso_proof.front_half.stages.examples import _reconcile_tests_with_examples

    claim_path = tmp_path / "claim.md"
    claim_path.write_text(HODGE_ALL_CUBIC_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("recon_tests", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    tests = read_yaml(run_dir / artifacts["tests"])
    referenced = {eid for test in tests["tests"] for eid in test.get("uses_examples", [])}
    assert "ex_special_cubic" in referenced

    dropped = _reconcile_tests_with_examples(run_dir, artifacts, tests["claim_id"], set())

    assert dropped >= 1
    after = read_yaml(run_dir / artifacts["tests"])
    assert all("ex_special_cubic" not in test.get("uses_examples", []) for test in after["tests"])


def test_reconcile_tests_keeps_valid_bank_counterexample_reference(tmp_path: Path) -> None:
    from espresso_proof.front_half.stages.examples import _reconcile_tests_with_examples
    from espresso_proof.kernel.research_bank import materialize_run_banks

    claim_path = tmp_path / "claim.md"
    claim_path.write_text(HODGE_ALL_CUBIC_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("recon_bank_tests", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    counterexamples = read_yaml(run_dir / artifacts["counterexamples"])
    cex_id = counterexamples["tests"][0]["id"]
    write_yaml(
        run_dir / artifacts["tests"],
        {
            "claim_id": counterexamples["claim_id"],
            "target": "claim",
            "tests": [
                {
                    "id": "t_bank_cex",
                    "description": "valid bank counterexample reference",
                    "uses_examples": [],
                    "uses_counterexamples": [cex_id],
                    "result": "not_tested",
                    "evidence_status": "human_needed",
                    "main_observation": "candidate only",
                    "next_action": "inspect",
                }
            ],
            "summary": "valid bank reference",
        },
    )
    materialize_run_banks(run_dir, keys=["counterexamples"])
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    dropped = _reconcile_tests_with_examples(run_dir, artifacts, counterexamples["claim_id"], set())

    assert dropped == 0
    tests = read_yaml(run_dir / artifacts["tests"])
    assert tests["tests"][0]["uses_counterexamples"] == [cex_id]


def test_reconcile_strategy_unclear_resets_to_lock_only(tmp_path: Path) -> None:
    from espresso_proof.front_half.stages.grade import _reconcile_strategy_after_regrade

    claim_path = tmp_path / "claim.md"
    claim_path.write_text("Vague mirror thing.\n", encoding="utf-8")
    run_dir = run_mock_critique("recon_unclear", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]

    _reconcile_strategy_after_regrade(run_dir, artifacts, {"status": "unclear", "score": 10})

    synthesis = read_yaml(run_dir / artifacts["strategy_synthesis"])
    brief = read_yaml(run_dir / artifacts["strategy_brief"])
    assert [route["id"] for route in synthesis["routes"]] == ["strategy_lock_claim"]
    assert [route["id"] for route in brief["top_strategies"]] == ["strategy_lock_claim"]
    assert synthesis["routes"][0]["estimated_effort"] == "quick_test"


def test_reconcile_strategy_unclear_rewrites_stale_lock_route(tmp_path: Path) -> None:
    from espresso_proof.front_half.stages.grade import _reconcile_strategy_after_regrade

    claim_path = tmp_path / "claim.md"
    claim_path.write_text("Vague mirror thing.\n", encoding="utf-8")
    run_dir = run_mock_critique("recon_stale_lock", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    synthesis = read_yaml(run_dir / artifacts["strategy_synthesis"])
    brief = read_yaml(run_dir / artifacts["strategy_brief"])
    stale = dict(synthesis["routes"][0])
    stale.update({"id": "strategy_lock_claim", "score": 99, "estimated_effort": "deep_exploration"})
    synthesis["routes"] = [stale]
    brief["top_strategies"] = [dict(stale)]
    write_yaml(run_dir / artifacts["strategy_synthesis"], synthesis)
    write_yaml(run_dir / artifacts["strategy_brief"], brief)

    _reconcile_strategy_after_regrade(run_dir, artifacts, {"status": "unclear", "score": 10})

    route = read_yaml(run_dir / artifacts["strategy_synthesis"])["routes"][0]
    assert route["id"] == "strategy_lock_claim"
    assert route["score"] <= 10
    assert route["estimated_effort"] == "quick_test"


def test_reconcile_strategy_clamps_scores_to_lift_limit(tmp_path: Path) -> None:
    from espresso_proof.front_half.stages.grade import _reconcile_strategy_after_regrade
    from espresso_proof.kernel.strategy_rules import score_lift_limit

    claim_path = tmp_path / "claim.md"
    claim_path.write_text(HODGE_ALL_CUBIC_CLAIM, encoding="utf-8")
    run_dir = run_mock_critique("recon_clamp", claim_path, tmp_path)
    artifacts = read_yaml(run_dir / "run_manifest.yaml")["artifacts"]
    idea_score = 10
    synthesis = read_yaml(run_dir / artifacts["strategy_synthesis"])
    brief = read_yaml(run_dir / artifacts["strategy_brief"])
    route = synthesis["routes"][0]
    route["score"] = idea_score + score_lift_limit(route) + 5
    for brief_route in brief["top_strategies"]:
        if brief_route["id"] == route["id"]:
            brief_route["score"] = route["score"]
    write_yaml(run_dir / artifacts["strategy_synthesis"], synthesis)
    write_yaml(run_dir / artifacts["strategy_brief"], brief)

    changed = _reconcile_strategy_after_regrade(
        run_dir, artifacts, {"status": "fragile", "score": idea_score}
    )

    assert changed >= 1
    synthesis = read_yaml(run_dir / artifacts["strategy_synthesis"])
    for route in synthesis["routes"]:
        assert route["score"] <= idea_score + score_lift_limit(route)
